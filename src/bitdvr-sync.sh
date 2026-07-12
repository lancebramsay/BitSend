#!/bin/bash
set -euo pipefail

# BitDVR step 2 of 2 — sends a file or folder from the local Library
# staging area (see bitdvr-organize.sh) to the media-server box over a
# BitTorrent swarm of exactly two real peers (this machine + the media
# box's Deluge), instead of opening SSH/Samba on that box just to move
# video files. Deluge-web is typically already reachable on the LAN, and
# Deluge already accepts inbound peer connections as its normal job, so
# this adds no new listening service or attack surface on the media box.
#
# The Deluge host has no default: pass --host, or set $BITDVR_PLEX_HOST
# (which is how the BitDVR GUI's Settings tab threads its configured
# Deluge Host IP through without editing this script).
#
# Both the torrent file and the seed are built with libtorrent directly
# (bitdvr-create-torrent.py / bitdvr-libtorrent-daemon.py), not
# transmission-cli. An earlier transmission-create + transmission-daemon
# design got the peer connection to Deluge fully established (handshake,
# encryption, interested/unchoke all completed — confirmed via packet
# capture) but piece data never flowed, even after 20+ minutes with an
# established, unchoked peer. Deluge's own downloads (magnet links, any
# torrent not created/seeded by this setup) worked fine, which pointed
# away from a general "old libtorrent can't talk to anything modern"
# theory and toward Transmission's independent BT implementation
# specifically not interoperating with Deluge's libtorrent for this one
# path. Building and seeding with libtorrent on both ends removes that
# variable entirely. The torrent is created with no trackers at all and
# the private flag set (see bitdvr-create-torrent.py --private) — no
# tracker, public or otherwise, ever sees this info-hash.
# core.connect_peer below is how the two peers actually find each other,
# seeding the connection directly instead of relying on discovery of any
# kind; the seed box's peer port (51413) should never be forwarded from
# the WAN either, so nothing outside the LAN can reach it even if
# something did leak the hash.
#
# --path can be a single file (one movie or one episode) or a directory
# (a whole "Season NN" folder, or a whole Series folder spanning multiple
# seasons) — bitdvr-create-torrent.py preserves whatever's inside a
# directory, so pointing it at "Season 02/" ships every episode in that
# season in one torrent instead of one torrent per file.
#
# --move-to sets where Deluge drops the *entire* --path payload once the
# torrent completes (Deluge moves --path's top-level item — a file or a
# folder — into --move-to, creating --move-to if missing). If omitted,
# the label's own default path is used (fine for movies, which always
# land flat). For TV, always pass --move-to one level *above* whatever
# --path points at — e.g. --path .../Season 02 needs
# --move-to .../<Series>, NOT .../<Series>/Season 02 — so a send only
# ever introduces a new name at the destination (a Season NN folder
# that doesn't exist there yet) rather than colliding with one that
# does. Sending the exact same Season or Series folder a second time
# WOULD collide (Deluge would nest a duplicate inside the existing one
# instead of merging) — organize a season completely, send it once, and
# let any later single new episode go over as its own file instead of
# re-sending the whole season.
#
# If --path lives under ~/Videos/OBS/Library, it's archived to
# Library/.sent/<same relative path> up front (before the torrent is
# even created — see the race-condition note below) — keeps the pending
# tree accurate and prevents an accidental repeat send of the same
# folder. If the send fails partway through, the file is still under
# .sent/ — rerun with --path pointing there directly to retry (it won't
# be re-archived).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${BITDVR_PYTHON:-python3}"
SEED_WATCH_DIR="$HOME/.config/bitdvr-libtorrent-daemon/watch"
# macOS has no ~/Videos; ~/Movies is where OBS defaults its output
# there. Must stay in sync with the same branch in bitdvr-gui.py /
# bitdvr-organize.sh / bitdvr-magnet.sh.
OS="$(uname -s)"
if [[ "$OS" == "Darwin" ]]; then
    RECORDINGS_DIR="$HOME/Movies/OBS"
else
    RECORDINGS_DIR="$HOME/Videos/OBS"
fi
LIBRARY_DIR="$RECORDINGS_DIR/Library"
TORRENT_DIR="$RECORDINGS_DIR/.torrents"
PLEX_HOST="${BITDVR_PLEX_HOST:-}"
PEER_PORT=51413
SECRET_SERVICE="bitdvr-deluge-web"

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
    echo "Usage: $0 --label movies|tv --path <file-or-dir> [--host <deluge-host>] [--move-to <abs-path-on-deluge-host>]" >&2
    echo "       --host defaults to \$BITDVR_PLEX_HOST; one of the two is required" >&2
    exit 1
}

LABEL=""
SRC_PATH=""
MOVE_TO=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --label) LABEL="$2"; shift 2 ;;
        --path) SRC_PATH="$2"; shift 2 ;;
        --host) PLEX_HOST="$2"; shift 2 ;;
        --move-to) MOVE_TO="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) usage ;;
    esac
done

if [[ "$LABEL" != "movies" && "$LABEL" != "tv" ]]; then
    echo "error: --label must be 'movies' or 'tv'" >&2
    exit 1
fi
if [[ -z "$PLEX_HOST" ]]; then
    echo "error: no Deluge host configured — pass --host, set \$BITDVR_PLEX_HOST," >&2
    echo "       or set the Deluge Host IP in the BitDVR GUI's Settings tab" >&2
    exit 1
fi
DELUGE_WEB_URL="http://${PLEX_HOST}:8112"
if [[ -z "$SRC_PATH" || ! -e "$SRC_PATH" ]]; then
    echo "error: --path not found: $SRC_PATH" >&2
    exit 1
fi
# realpath, not `cd && pwd` — the latter's symlink resolution depends on
# how the calling shell's PWD tracking was seeded (physical vs logical),
# which is inconsistent between an interactive shell and a subprocess
# spawned by the GUI. On Bazzite specifically, $HOME is /home/lramsay
# but /home is itself a symlink to /var/home — a SRC_PATH resolved
# inconsistently with LIBRARY_DIR below silently fails the .sent/ guard
# case match further down, causing a file already archived once to get
# archived a second time into Library/.sent/.sent/. realpath always
# resolves to the same canonical physical path regardless of context.
SRC_PATH="$(realpath -- "$SRC_PATH")"
LIBRARY_DIR="$(realpath -- "$LIBRARY_DIR")"

# Archived (moved to Library/.sent/) *before* the torrent is created and
# handed to the seed daemon, not after — the seed daemon reads the file
# from wherever SRC_PATH points at hand-off time, and a later mv would
# race its own async pickup/hash-check, silently pulling the file out
# from under it (this raced transmission-daemon too, previously, and is
# suspected to be the real cause behind transfers that hashed as 0%
# and never moved, rather than a Deluge/Transmission incompatibility).
# Skipping paths already under .sent/ also guards against re-archiving
# on a retry after a failed send further down this script.
case "$SRC_PATH" in
    "$LIBRARY_DIR"/.sent/*) ;;
    "$LIBRARY_DIR"/*)
        ORIGINAL_PARENT="$(dirname "$SRC_PATH")"
        REL="${SRC_PATH#"$LIBRARY_DIR"/}"
        ARCHIVE_DEST="$LIBRARY_DIR/.sent/$REL"
        mkdir -p "$(dirname "$ARCHIVE_DEST")"
        mv -- "$SRC_PATH" "$ARCHIVE_DEST"
        SRC_PATH="$ARCHIVE_DEST"
        echo "==> Archived local copy to Library/.sent/$REL (won't show up to resend again)"
        # Clean up now-empty parent dirs left behind in the pending tree
        # (e.g. a Series folder with no seasons left after the last one
        # was sent), stopping at Library/Movies or Library/TV.
        PARENT="$ORIGINAL_PARENT"
        while [[ "$PARENT" != "$LIBRARY_DIR/Movies" && "$PARENT" != "$LIBRARY_DIR/TV" && "$PARENT" == "$LIBRARY_DIR"/* ]]; do
            rmdir "$PARENT" 2>/dev/null || break
            PARENT="$(dirname "$PARENT")"
        done
        ;;
esac

DELUGE_WEB_PASSWORD=$(secret_lookup "$SECRET_SERVICE" deluge-web || true)
if [[ -z "$DELUGE_WEB_PASSWORD" ]]; then
    echo "No deluge-web password stored in the login keyring yet."
    read -r -s -p "deluge-web password: " DELUGE_WEB_PASSWORD
    echo
    if [[ -z "$DELUGE_WEB_PASSWORD" ]]; then
        echo "error: empty password entered" >&2
        exit 1
    fi
    printf '%s' "$DELUGE_WEB_PASSWORD" | secret_store "$SECRET_SERVICE" deluge-web "BitDVR deluge-web"
    echo "Stored in the login keyring/Keychain — not written to disk in plaintext."
fi

mkdir -p "$TORRENT_DIR"
NAME="$(basename "$SRC_PATH")"
TORRENT_FILE="$TORRENT_DIR/${NAME}.torrent"

echo "==> Creating torrent for $NAME (private, no trackers)..."
"$PYTHON" "$SCRIPT_DIR/bitdvr-create-torrent.py" --private -o "$TORRENT_FILE" "$SRC_PATH"

echo "==> Seeding locally via bitdvr-libtorrent-daemon..."
mkdir -p "$SEED_WATCH_DIR"
WATCH_TORRENT="$SEED_WATCH_DIR/$(basename "$TORRENT_FILE")"
jq -nc --arg save_path "$(dirname "$SRC_PATH")" '{save_path: $save_path}' > "$WATCH_TORRENT.json"
cp "$TORRENT_FILE" "$WATCH_TORRENT"

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
# --arg at each call site — series/title fields are free-text GUI input,
# so this avoids breaking on quotes/backslashes the way manual string
# interpolation would). The composed body is always written to a file
# and posted with --data-binary @file rather than a literal -d "..."
# argument — core.add_torrent_file's params_json embeds the whole
# base64-encoded .torrent, which for a real recording is easily well
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
    echo "error: failed to add torrent: $ADD_RESP" >&2
    echo "Fallback: open $DELUGE_WEB_URL manually, add $TORRENT_FILE, then add peer $LOCAL_IP:$PEER_PORT to it." >&2
    exit 1
fi
echo "    torrent id: $TORRENT_ID"

# Label (and, for series sends, the move-completed override) are
# applied *before* core.connect_peer, not after. This machine is
# already seeding the file at 100% — once the peer connects, this is a
# LAN transfer that can finish in well under a second, firing Deluge's
# move-on-complete exactly once. Every prior send confirmed this
# empirically: every torrent BitDVR had labeled "tv" landed with
# move_completed still false, sitting wherever it was first written
# instead of the label's TV path, because label.set_torrent used to run
# *after* connect_peer and consistently lost the race. Configuring the
# label/move destination first guarantees it's already active before
# the transfer can possibly complete.
echo "==> Applying label '$LABEL'..."
LABEL_RESP=$(deluge_rpc "label.set_torrent" "$(jq -nc --arg id "$TORRENT_ID" --arg label "$LABEL" '[$id, $label]')")
echo "    $LABEL_RESP"

if [[ -n "$MOVE_TO" ]]; then
    echo "==> Overriding completed-move path to $MOVE_TO..."
    MOVE_RESP=$(deluge_rpc "core.set_torrent_options" "$(jq -nc --arg id "$TORRENT_ID" --arg path "$MOVE_TO" '[[$id], {move_completed: true, move_completed_path: $path}]')")
    echo "    $MOVE_RESP"
    echo "    (Deluge should create $MOVE_TO if it doesn't exist yet — unverified against the live box, watch deluge-web to confirm.)"
fi

echo "==> Connecting this machine as a direct peer..."
# core.connect_peer(torrent_id, ip, port) takes ip and port as separate
# args, not a combined "ip:port" string — passing them combined leaves
# Deluge's RPC dispatcher one positional argument short and it silently
# no-ops the peer connection (surfaces as 0 seeds/0 peers forever, since
# there is no tracker and nothing else was ever going to find the seed
# on its own).
PEER_RESP=$(deluge_rpc "core.connect_peer" "$(jq -nc --arg id "$TORRENT_ID" --arg ip "$LOCAL_IP" --argjson port "$PEER_PORT" '[$id, $ip, $port]')")
echo "    $PEER_RESP"

echo "==> Done. Watch progress at $DELUGE_WEB_URL"
echo "    It should download directly from this machine, not from the internet."
echo "    Once it finishes, stop seeding: rm '$WATCH_TORRENT' '$WATCH_TORRENT.json'"
