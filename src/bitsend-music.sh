#!/bin/bash
set -euo pipefail

# Sends album folders to the media server's Music library, one at a time,
# through bitsend-sync.sh. Takes <Artist>/<Album> folders from --source
# (default: ~/Rips/staging), and moves each album to a "sent" folder beside
# it once delivery is confirmed.
#
# Everything is checked before anything is sent, so a half-finished rip
# stops the run up front rather than partway through a batch: each album
# must be an <Artist>/<Album> folder with audio in it, there must be no
# x01.wav-style temporary files from a rip still running (Asunder's output
# pattern), and it must not already be in the archive.
#
# Each album gets its own torrent named "Artist - Album", so two artists'
# "Greatest Hits" can't collide in the seed daemon's watch dir, and goes to
# <remote-root>/Music/<Artist>, one level above the album, so a send only
# ever adds a new name there. The run stops at the first failed album;
# anything already sent stays sent, and rerunning picks up the rest.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC_SCRIPT="$SCRIPT_DIR/bitsend-sync.sh"
SOURCE_DIR="$HOME/Rips/staging"
REMOTE_ROOT="${BITSEND_REMOTE_ROOT:-}"

usage() {
    echo "Usage: $0 [--source <dir>] [--remote-root <path>] [--dry-run] [--yes] [album-dir ...]" >&2
    echo "  With no album-dir, sends every <Artist>/<Album> in the source folder." >&2
    echo "  --remote-root is the library root on the Deluge host, containing Music/" >&2
    echo "  (defaults to \$BITSEND_REMOTE_ROOT). The Deluge host comes from" >&2
    echo "  \$BITSEND_PLEX_HOST." >&2
    exit 1
}

DRY_RUN=false
ASSUME_YES=false
ALBUMS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --source) SOURCE_DIR="$2"; shift 2 ;;
        --remote-root) REMOTE_ROOT="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --yes) ASSUME_YES=true; shift ;;
        -h|--help) usage ;;
        -*) usage ;;
        *) ALBUMS+=("$1"); shift ;;
    esac
done

if ! $DRY_RUN && [[ -z "$REMOTE_ROOT" ]]; then
    echo "error: no library root on the Deluge host — pass --remote-root or set \$BITSEND_REMOTE_ROOT" >&2
    exit 1
fi
REMOTE_ROOT="${REMOTE_ROOT%/}"

mkdir -p "$SOURCE_DIR"
SOURCE_DIR="$(realpath -- "$SOURCE_DIR")"
SENT_DIR="$(dirname "$SOURCE_DIR")/sent"
if [[ ${#ALBUMS[@]} -eq 0 ]]; then
    shopt -s nullglob
    ALBUMS=("$SOURCE_DIR"/*/*/)
    shopt -u nullglob
fi
if [[ ${#ALBUMS[@]} -eq 0 ]]; then
    echo "Nothing to send in $SOURCE_DIR."
    exit 0
fi

check_album() {
    local album="$1" rel tracks=0
    case "$album" in
        "$SOURCE_DIR"/*/*) ;;
        *) echo "error: not an <Artist>/<Album> folder under $SOURCE_DIR: $album" >&2; return 1 ;;
    esac
    rel="${album#"$SOURCE_DIR"/}"
    if [[ "$rel" == */*/* || ! -d "$album" ]]; then
        echo "error: not an <Artist>/<Album> folder under $SOURCE_DIR: $album" >&2
        return 1
    fi
    if [[ -e "$SENT_DIR/$rel" ]]; then
        echo "error: already sent (found in $SENT_DIR): $rel" >&2
        return 1
    fi
    if [[ -n "$(find "$album" -type f -name 'x[0-9][0-9].wav' -print -quit)" ]]; then
        echo "error: temporary .wav files present, rip still running? $rel" >&2
        return 1
    fi
    while IFS= read -r -d '' _; do
        tracks=$((tracks + 1))
    done < <(find "$album" -type f \( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.m4a' \
        -o -iname '*.ogg' -o -iname '*.opus' -o -iname '*.wav' -o -iname '*.aiff' \) -print0)
    if [[ $tracks -eq 0 ]]; then
        echo "error: no audio files in $rel" >&2
        return 1
    fi
    echo "    $rel ($tracks tracks)"
}

echo "==> Checking albums..."
for i in "${!ALBUMS[@]}"; do
    ALBUMS[i]="$(realpath -- "${ALBUMS[i]}")"
    check_album "${ALBUMS[i]}"
done

if $DRY_RUN; then
    echo "==> Dry run, nothing sent."
    exit 0
fi
if ! $ASSUME_YES; then
    read -r -p "Send ${#ALBUMS[@]} album(s) to the media server? [y/N] " REPLY
    [[ "$REPLY" == [yY]* ]] || { echo "Cancelled."; exit 0; }
fi

SENT_COUNT=0
for album in "${ALBUMS[@]}"; do
    rel="${album#"$SOURCE_DIR"/}"
    artist="$(basename "$(dirname "$album")")"
    echo "==> Sending $rel..."
    if ! "$SYNC_SCRIPT" --path "$album" \
            --move-to "$REMOTE_ROOT/Music/$artist" \
            --torrent-name "$artist - $(basename "$album")" \
            --archive-to "$SENT_DIR/$rel"; then
        echo "error: stopped at $rel. $SENT_COUNT album(s) sent before this." >&2
        exit 1
    fi
    rmdir "$(dirname "$album")" 2>/dev/null || true
    SENT_COUNT=$((SENT_COUNT + 1))
done
echo "==> Done. $SENT_COUNT album(s) sent. Scan the Music library on your media server."
