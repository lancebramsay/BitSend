#!/bin/bash
set -euo pipefail

# Sends one file or folder to the media server over a BitTorrent swarm of
# exactly two real peers (this machine + the server's Deluge), instead of
# opening SSH/Samba/NFS on it. Deluge-web is the only service this needs to
# reach, and Deluge already accepts inbound peer connections as its normal
# job, so this adds no new listening service on the server.
#
# Both the torrent file and the seed are built with libtorrent directly
# (bitsend-create-torrent.py / bitsend-libtorrent-daemon.py) — the same
# engine Deluge uses. An earlier Transmission-based design got the peer
# connection to Deluge fully established but piece data never flowed;
# building and seeding with libtorrent on both ends removes that variable.
# The torrent is created with no trackers at all and the private flag set
# (see bitsend-create-torrent.py --private), so no tracker, public or
# otherwise, ever sees this info-hash. core.connect_peer below is how the
# two peers actually find each other, seeding the connection directly
# instead of relying on discovery of any kind.
#
# --move-to is where Deluge drops the album once the torrent completes, and
# it's how this script confirms delivery. Deluge's "move completed" is
# effectively a directory move, so always pass the folder one level *above*
# the album (Music/<Artist>), so a send only ever introduces a new name at
# the destination. Sending the same album a second time WOULD collide:
# Deluge nests a duplicate inside the existing one instead of merging.
#
# A send is synchronous and only counts once Deluge reports the album at
# --move-to, not merely 100% downloaded: the move happens after the
# download, and until it's done the local copy is the only safety net. Only
# then does this script stop seeding, remove the finished torrent from
# Deluge (the files stay), and move the local copy to --archive-to. A
# failure at any point leaves the album where it was and the seed running;
# rerun the same command to resume, since a torrent already in Deluge is
# picked back up rather than added again.
#
# --torrent-name is the torrent's filename, because album names aren't
# unique (two artists' "Greatest Hits" would otherwise share a watch-dir
# slot). Run through bitsend-music.sh, which checks albums first.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${BITSEND_PYTHON:-python3}"
SEED_WATCH_DIR="$HOME/.config/bitsend-libtorrent-daemon/watch"
OS="$(uname -s)"
TORRENT_DIR="$HOME/.local/share/bitsend/torrents"
PLEX_HOST="${BITSEND_PLEX_HOST:-}"
# Not BitTorrent's usual 6881 range, and not BitDVR's 51413, so the two
# daemons can run side by side. Must match bitsend-libtorrent-daemon.py.
PEER_PORT="${BITSEND_PEER_PORT:-51414}"
SECRET_SERVICE="bitsend-deluge-web"
POLL_SECONDS="${BITSEND_POLL_SECONDS:-3}"
# How long a transfer may sit with no change at all before giving up.
# Progress resets it, so a large movie can take as long as it needs.
STALL_TIMEOUT="${BITSEND_STALL_TIMEOUT:-600}"

# Keyring backends — login keyring via secret-tool on Linux, the login
# Keychain via /usr/bin/security on macOS. secret_store takes the secret
# on stdin on both platforms so it never appears in a process list
# (security's -i mode reads whole commands from stdin; its parser needs
# backslashes and double quotes escaped).
secret_lookup() { # secret_lookup <service> <account>
    if [[ "$OS" == "Darwin" ]]; then
        security find-generic-password -s "$1" -a "$2" -w 2>/dev/null
    else
        secret-tool lookup service "$1" account "$2" 2>/dev/null
    fi
}
secret_store() { # printf '%s' <secret> | secret_store <service> <account> <label>
    if [[ "$OS" == "Darwin" ]]; then
        local secret=""
        # read hits EOF (status 1) on input with no trailing newline —
        # the secret is still in $secret, so don't let set -e trip.
        IFS= read -r secret || [[ -n "$secret" ]]
        secret="${secret//\\/\\\\}"; secret="${secret//\"/\\\"}"
        printf 'add-generic-password -U -s "%s" -a "%s" -l "%s" -w "%s"\n' \
            "$1" "$2" "$3" "$secret" | security -i
    else
        secret-tool store --label="$3" service "$1" account "$2"
    fi
}

usage() {
    echo "Usage: $0 --path <album-dir> --move-to <abs-path-on-deluge-host>" >&2
    echo "       --torrent-name <name> --archive-to <local-path> [--host <deluge-host>]" >&2
    echo "       --host defaults to \$BITSEND_PLEX_HOST; one of the two is required" >&2
    exit 1
}

LABEL="music"
SRC_PATH=""
MOVE_TO=""
NAME=""
ARCHIVE_DEST=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --path) SRC_PATH="$2"; shift 2 ;;
        --host) PLEX_HOST="$2"; shift 2 ;;
        --move-to) MOVE_TO="$2"; shift 2 ;;
        --torrent-name) NAME="$2"; shift 2 ;;
        --archive-to) ARCHIVE_DEST="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) usage ;;
    esac
done

if [[ -z "$MOVE_TO" || -z "$NAME" || -z "$ARCHIVE_DEST" ]]; then
    usage
fi
MOVE_TO="${MOVE_TO%/}"
if [[ -z "$PLEX_HOST" ]]; then
    echo "error: no Deluge host configured — pass --host, set \$BITSEND_PLEX_HOST," >&2
    echo "       or set the Deluge Host IP in the BitSend GUI's Settings tab" >&2
    exit 1
fi
DELUGE_WEB_URL="http://${PLEX_HOST}:8112"
if [[ -z "$SRC_PATH" || ! -e "$SRC_PATH" ]]; then
    echo "error: --path not found: $SRC_PATH" >&2
    exit 1
fi
SRC_PATH="$(realpath -- "$SRC_PATH")"
if [[ -e "$ARCHIVE_DEST" ]]; then
    echo "error: already sent (found in the archive): $ARCHIVE_DEST" >&2
    exit 1
fi

DELUGE_WEB_PASSWORD=$(secret_lookup "$SECRET_SERVICE" deluge-web || true)
if [[ -z "$DELUGE_WEB_PASSWORD" ]]; then
    echo "No deluge-web password stored in the login keyring yet."
    read -r -s -p "deluge-web password: " DELUGE_WEB_PASSWORD
    echo
    if [[ -z "$DELUGE_WEB_PASSWORD" ]]; then
        echo "error: empty password entered" >&2
        exit 1
    fi
    printf '%s' "$DELUGE_WEB_PASSWORD" | secret_store "$SECRET_SERVICE" deluge-web "BitSend deluge-web"
    echo "Stored in the login keyring/Keychain — not written to disk in plaintext."
fi

mkdir -p "$TORRENT_DIR"
TORRENT_FILE="$TORRENT_DIR/${NAME}.torrent"
TORRENT_ID_FILE="$TORRENT_FILE.id"

echo "==> Creating torrent for $NAME (private, no trackers)..."
"$PYTHON" "$SCRIPT_DIR/bitsend-create-torrent.py" --private -o "$TORRENT_FILE" "$SRC_PATH"

# NOTE: the seed-daemon handoff happens further down, AFTER Deluge has
# the torrent fully configured (label + move path) — the daemon dials
# Deluge within seconds of the sidecar appearing, and the transfer can
# complete near-instantly on a LAN, so Deluge-side setup must already be
# in place by then (same race that used to bite label.set_torrent, see
# the label comment below).

# This machine's LAN IP on whatever interface routes to the Deluge host
# — `ip route get` on Linux; macOS has no `ip`, so ask its routing table
# for the interface and then that interface for its address.
if [[ "$OS" == "Darwin" ]]; then
    IFACE=$(route -n get "$PLEX_HOST" 2>/dev/null | awk '/interface:/ {print $2}' || true)
    LOCAL_IP=$(ipconfig getifaddr "${IFACE:-en0}" 2>/dev/null || true)
else
    LOCAL_IP=$(ip route get "$PLEX_HOST" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="src") print $(i+1)}')
fi
if [[ -z "$LOCAL_IP" ]]; then
    echo "error: could not determine this machine's LAN IP toward the Deluge host" >&2
    exit 1
fi
echo "    Seeding from $LOCAL_IP:$PEER_PORT"

COOKIE_JAR=$(mktemp)
RPC_BODY_FILE=$(mktemp)
trap 'rm -f "$COOKIE_JAR" "$RPC_BODY_FILE"' EXIT

# params_json must be a pre-built JSON array string (build it with jq -nc
# --arg at each call site — artist and album names are free text,
# so this avoids breaking on quotes/backslashes the way manual string
# interpolation would). The composed body is always written to a file
# and posted with --data-binary @file rather than a literal -d "..."
# argument — core.add_torrent_file's params_json embeds the whole
# base64-encoded .torrent, which for a large payload is easily well
# past the kernel's ARG_MAX (2MB) once curl's own argv and the inherited
# environment are added on top ("curl: Argument list too long"). Reading
# from a file has no such limit, and costs nothing for the other calls'
# tiny payloads.
deluge_rpc() {
    local method="$1" params_json="$2"
    printf '{"method": "%s", "params": %s, "id": 1}' "$method" "$params_json" > "$RPC_BODY_FILE"
    curl -s -c "$COOKIE_JAR" -b "$COOKIE_JAR" -H "Content-Type: application/json" \
        --data-binary "@$RPC_BODY_FILE" \
        "$DELUGE_WEB_URL/json"
}

echo "==> Logging into deluge-web..."
LOGIN_RESP=$(deluge_rpc "auth.login" "$(jq -nc --arg pw "$DELUGE_WEB_PASSWORD" '[$pw]')")
if [[ "$(echo "$LOGIN_RESP" | jq -r '.result')" != "true" ]]; then
    echo "error: deluge-web login failed: $LOGIN_RESP" >&2
    exit 1
fi

echo "==> Uploading torrent to deluge-web..."
# --rawfile (reads a file's content into a jq variable), not --arg, for
# the same ARG_MAX reason as deluge_rpc above — this jq invocation is
# itself a separate exec() that would hit the same limit if the base64
# blob were passed as a plain --arg on jq's own command line.
# base64 with no line wrapping: GNU spells it -w0; BSD base64 (macOS)
# has no -w flag and doesn't wrap by default — tr strips newlines either
# way so both produce one unbroken blob.
b64_nowrap() {
    if [[ "$OS" == "Darwin" ]]; then base64 -i "$1" | tr -d '\n'
    else base64 -w0 "$1"; fi
}
ADD_RESP=$(deluge_rpc "core.add_torrent_file" "$(jq -nc --arg name "${NAME}.torrent" --rawfile data <(b64_nowrap "$TORRENT_FILE") '[$name, $data, {}]')")
TORRENT_ID=$(echo "$ADD_RESP" | jq -r '.result // empty')
if [[ -z "$TORRENT_ID" || "$TORRENT_ID" == "false" ]]; then
    # Most likely a retry after a failed send: Deluge refuses a torrent it
    # already has. Reuse the id it gave that first attempt, as long as it
    # still has it. Saved rather than derived, because which hash Deluge
    # uses as the id of a hybrid v1/v2 torrent depends on its libtorrent.
    EARLIER_ID=$(cat "$TORRENT_ID_FILE" 2>/dev/null || true)
    EXISTING=""
    if [[ -n "$EARLIER_ID" ]]; then
        EXISTING=$(deluge_rpc "core.get_torrent_status" "$(jq -nc --arg id "$EARLIER_ID" '[$id, ["name"]]')" | jq -r '.result.name // empty')
    fi
    if [[ -z "$EXISTING" ]]; then
        echo "error: failed to add torrent: $ADD_RESP" >&2
        # Still hand off to the seed daemon (no peer hint — there's nothing
        # to dial), so the manual fallback below actually has a live seed.
        mkdir -p "$SEED_WATCH_DIR"
        WATCH_TORRENT="$SEED_WATCH_DIR/$(basename "$TORRENT_FILE")"
        jq -nc --arg save_path "$(dirname "$SRC_PATH")" '{save_path: $save_path}' > "$WATCH_TORRENT.json"
        cp "$TORRENT_FILE" "$WATCH_TORRENT"
        echo "Fallback: open $DELUGE_WEB_URL manually, add $TORRENT_FILE, then add peer $LOCAL_IP:$PEER_PORT to it." >&2
        exit 1
    fi
    TORRENT_ID="$EARLIER_ID"
    echo "    already in Deluge from an earlier send, resuming"
fi
echo "$TORRENT_ID" > "$TORRENT_ID_FILE"
echo "    torrent id: $TORRENT_ID"

# Label and the move-completed path are
# applied *before* core.connect_peer, not after. This machine is
# already seeding the file at 100% — once the peer connects, this is a
# LAN transfer that can finish in well under a second, firing Deluge's
# move-on-complete exactly once. Every prior send confirmed this
# empirically: every early torrent labeled "tv" landed with
# move_completed still false, sitting wherever it was first written
# instead of the label's TV path, because label.set_torrent used to run
# *after* connect_peer and consistently lost the race. Configuring the
# label/move destination first guarantees it's already active before
# the transfer can possibly complete.
# The label only sorts Deluge's own view: --move-to below is
# what decides the destination, so a missing label isn't fatal.
echo "==> Applying label '$LABEL'..."
LABEL_RESP=$(deluge_rpc "label.set_torrent" "$(jq -nc --arg id "$TORRENT_ID" --arg label "$LABEL" '[$id, $label]')")
if [[ "$(echo "$LABEL_RESP" | jq -r '.error // empty')" != "" ]]; then
    echo "    warning: couldn't apply label '$LABEL' (create it in Deluge to fix this), continuing"
fi

echo "==> Setting completed-move path to $MOVE_TO..."
MOVE_RESP=$(deluge_rpc "core.set_torrent_options" "$(jq -nc --arg id "$TORRENT_ID" --arg path "$MOVE_TO" '[[$id], {move_completed: true, move_completed_path: $path}]')")
if [[ "$(echo "$MOVE_RESP" | jq -r '.error // empty')" != "" ]]; then
    echo "error: couldn't set the completed-move path: $MOVE_RESP" >&2
    exit 1
fi

# Hand off to the seed daemon, with Deluge's own peer port in the
# sidecar so the daemon dials OUT to Deluge as well as accepting inbound.
# The outbound direction is what makes sends work on segmented LANs
# where clients may open connections to the server VLAN but not the
# reverse — there, core.connect_peer below asks Deluge to dial a port it
# can never reach, and without this hint the transfer sits at 0 seeds
# forever. Either side reaching the other is sufficient; on open LANs
# both work and whichever lands first wins. If get_listen_port fails
# (very old Deluge), the sidecar just omits the hint and behavior is
# the old inbound-only one.
DELUGE_PEER_PORT=$(deluge_rpc "core.get_listen_port" "[]" | jq -r '.result // empty')
echo "==> Seeding locally via bitsend-libtorrent-daemon..."
mkdir -p "$SEED_WATCH_DIR"
WATCH_TORRENT="$SEED_WATCH_DIR/$(basename "$TORRENT_FILE")"
if [[ -n "$DELUGE_PEER_PORT" ]]; then
    echo "    (daemon will also dial Deluge at $PLEX_HOST:$DELUGE_PEER_PORT)"
    jq -nc --arg save_path "$(dirname "$SRC_PATH")" --arg peer "$PLEX_HOST:$DELUGE_PEER_PORT" \
        '{save_path: $save_path, peers: [$peer]}' > "$WATCH_TORRENT.json"
else
    jq -nc --arg save_path "$(dirname "$SRC_PATH")" '{save_path: $save_path}' > "$WATCH_TORRENT.json"
fi
cp "$TORRENT_FILE" "$WATCH_TORRENT"

echo "==> Connecting this machine as a direct peer..."
# core.connect_peer(torrent_id, ip, port) takes ip and port as separate
# args, not a combined "ip:port" string — passing them combined leaves
# Deluge's RPC dispatcher one positional argument short and it silently
# no-ops the peer connection (surfaces as 0 seeds/0 peers forever, since
# there is no tracker and nothing else was ever going to find the seed
# on its own).
PEER_RESP=$(deluge_rpc "core.connect_peer" "$(jq -nc --arg id "$TORRENT_ID" --arg ip "$LOCAL_IP" --argjson port "$PEER_PORT" '[$id, $ip, $port]')")
echo "    $PEER_RESP"

# Done means Deluge reports the payload at its final path and has finished
# moving it. Anything else that changes (progress, state, location) counts
# as life and resets the stall clock, and a move in progress is never a
# stall: a big file crossing filesystems shows no other change while it runs.
echo "==> Waiting for Deluge to finish..."
STALLED=0
LAST_SEEN=""
while (( STALLED < STALL_TIMEOUT )); do
    STATUS=$(deluge_rpc "core.get_torrent_status" \
        "$(jq -nc --arg id "$TORRENT_ID" '[$id, ["state", "progress", "save_path", "download_location"]]')")
    STATE=$(echo "$STATUS" | jq -r '.result.state // empty')
    PROGRESS=$(echo "$STATUS" | jq -r '.result.progress // 0')
    LOCATION=$(echo "$STATUS" | jq -r '.result.save_path // .result.download_location // empty')
    LOCATION="${LOCATION%/}"
    if [[ "$STATE" == "Error" ]]; then
        echo "error: Deluge reports an error for this torrent: $STATUS" >&2
        exit 1
    fi
    if [[ "$LOCATION" == "$MOVE_TO" && "$STATE" != "Moving" && "${PROGRESS%.*}" == "100" ]]; then
        break
    fi
    SEEN="$STATE|$PROGRESS|$LOCATION"
    if [[ "$SEEN" != "$LAST_SEEN" || "$STATE" == "Moving" ]]; then
        [[ "${PROGRESS%.*}" == "${LAST_SEEN_PCT:-}" ]] || echo "    ${PROGRESS%.*}% ($STATE)"
        LAST_SEEN_PCT="${PROGRESS%.*}"
        LAST_SEEN="$SEEN"
        STALLED=0
    else
        STALLED=$((STALLED + POLL_SECONDS))
    fi
    sleep "$POLL_SECONDS"
done
if (( STALLED >= STALL_TIMEOUT )); then
    echo "error: no progress for ${STALL_TIMEOUT}s (state '$STATE', ${PROGRESS%.*}%, at '$LOCATION')." >&2
    echo "Still seeding, so it can finish on its own. Check $DELUGE_WEB_URL, then rerun this send." >&2
    exit 1
fi

echo "==> Landed in $MOVE_TO, cleaning up..."
rm -f "$WATCH_TORRENT" "$WATCH_TORRENT.json" "$TORRENT_FILE" "$TORRENT_ID_FILE"
if ! deluge_rpc "core.remove_torrent" "$(jq -nc --arg id "$TORRENT_ID" '[$id, false]')" | jq -e '.result == true' >/dev/null; then
    echo "    warning: the finished torrent is still listed in deluge-web (the files are fine)" >&2
fi

mkdir -p "$(dirname "$ARCHIVE_DEST")"
mv -- "$SRC_PATH" "$ARCHIVE_DEST"
echo "    Local copy archived to $ARCHIVE_DEST"
echo "==> Done."
