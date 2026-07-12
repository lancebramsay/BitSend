#!/bin/bash
set -euo pipefail

# BitDVR "copy magnet link" path — creates a torrent for --path and hands
# it to the local libtorrent seed daemon, same as bitdvr-sync.sh, but
# skips deluge-web and skips archiving to Library/.sent/ entirely. This
# is an ad hoc distribution path (paste the magnet into any Deluge/
# qBittorrent/Transmission client on the LAN) rather than the automated
# send to the media server, so the source file is left in place in the
# pending Library tree and can still be sent normally later.
#
# The torrent is created with no trackers at all and the private flag
# set, same as bitdvr-sync.sh — no tracker, public or otherwise, ever
# sees this info-hash. The magnet link includes an x.pe peer hint
# pointing straight at this machine instead, so a client that pastes it
# doesn't need any tracker or DHT to find the seed — both would be
# unusable for this anyway, since the seed daemon has DHT/LSD disabled
# (see bitdvr-libtorrent-daemon.py) and the peer port should never be
# forwarded from the WAN.
#
# Prints the magnet link as the last line of stdout.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${BITDVR_PYTHON:-python3}"
SEED_WATCH_DIR="$HOME/.config/bitdvr-libtorrent-daemon/watch"
# macOS has no ~/Videos; must stay in sync with the same branch in
# bitdvr-gui.py / bitdvr-organize.sh / bitdvr-sync.sh.
OS="$(uname -s)"
if [[ "$OS" == "Darwin" ]]; then
    TORRENT_DIR="$HOME/Movies/OBS/.torrents"
else
    TORRENT_DIR="$HOME/Videos/OBS/.torrents"
fi
PEER_PORT=51413

usage() {
    echo "Usage: $0 --path <file-or-dir>" >&2
    exit 1
}

SRC_PATH=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --path) SRC_PATH="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) usage ;;
    esac
done
if [[ -z "$SRC_PATH" || ! -e "$SRC_PATH" ]]; then
    echo "error: --path not found: $SRC_PATH" >&2
    exit 1
fi
SRC_PATH="$(cd "$(dirname "$SRC_PATH")" && pwd)/$(basename "$SRC_PATH")"

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

# This machine's LAN IP on its default route — `ip route get` on Linux;
# macOS has no `ip`, so ask its routing table for the default interface
# and then that interface for its address.
if [[ "$OS" == "Darwin" ]]; then
    IFACE=$(route -n get default 2>/dev/null | awk '/interface:/ {print $2}' || true)
    LOCAL_IP=$(ipconfig getifaddr "${IFACE:-en0}" 2>/dev/null || true)
else
    LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="src") print $(i+1)}')
fi
if [[ -z "$LOCAL_IP" ]]; then
    echo "error: could not determine this machine's LAN IP" >&2
    exit 1
fi

MAGNET=$("$PYTHON" "$SCRIPT_DIR/bitdvr-magnet-uri.py" "$TORRENT_FILE")
echo "==> Done. Stop seeding when no longer needed: rm '$WATCH_TORRENT' '$WATCH_TORRENT.json'"
echo "${MAGNET}&x.pe=${LOCAL_IP}:${PEER_PORT}"
