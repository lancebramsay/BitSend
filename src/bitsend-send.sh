#!/bin/bash
set -euo pipefail

# Sends media from the staging folder to the media server's library, one
# torrent at a time, through bitsend-sync.sh. The staging folder (default
# ~/Rips/staging) holds one subfolder per library, laid out the way the
# library on the server is:
#
#   Music/<Artist>/<Album>/...      one torrent per album
#   Movies/<Movie folder or file>   one torrent per top-level entry
#   TV/<Show>/.../<file>            one torrent per file
#
# Each torrent lands one level above itself on the server, so a send only
# ever adds a new name there: Deluge's "move completed" nests a duplicate
# inside an existing folder of the same name rather than merging. That's
# why TV goes file by file: a show or season usually already exists on the
# server, and a new episode has to go *into* it. Names aren't checked;
# matching is left to the media server.
#
# After delivery is confirmed, each item moves to a "sent" folder beside the
# staging folder, under the same relative path (sent/TV/<Show>/...), and
# folders it leaves empty in staging are removed.
#
# Everything is checked before anything is sent, so a half-finished rip or
# copy stops the run up front rather than partway through: no x01.wav-style
# files from a rip still running (Asunder's pattern), no .part/.tmp/.partial
# files, nothing already in the archive. The run stops at the first failed
# item; anything already sent stays sent, and rerunning picks up the rest.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC_SCRIPT="$SCRIPT_DIR/bitsend-sync.sh"
STAGING_DIR="$HOME/Rips/staging"
REMOTE_ROOT="${BITSEND_REMOTE_ROOT:-}"

usage() {
    cat >&2 <<EOF
Usage: $0 --library music|movies|tv [--staging <dir>] [--remote-root <path>]
          [--dry-run] [--yes] [path ...]
  Sends everything in the library's staging folder (<staging>/Music, Movies,
  or TV), or only what's at or under each given path: for music an artist or
  album folder, for movies a movie folder or file, for TV a show, season, or
  episode.
  --staging      default ~/Rips/staging; sent items move to "sent" beside it
  --remote-root  the library root on the Deluge host, containing Music/,
                 Movies/, and TV/ (defaults to \$BITSEND_REMOTE_ROOT). The
                 Deluge host comes from \$BITSEND_PLEX_HOST.
EOF
    exit 1
}

LIBRARY=""
DRY_RUN=false
ASSUME_YES=false
PATHS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --library) LIBRARY="$2"; shift 2 ;;
        --staging) STAGING_DIR="$2"; shift 2 ;;
        --remote-root) REMOTE_ROOT="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --yes) ASSUME_YES=true; shift ;;
        -h|--help) usage ;;
        -*) usage ;;
        *) PATHS+=("$1"); shift ;;
    esac
done

# The library's folder name, which is also its folder on the server, and
# the Deluge label applied (see bitsend-sync.sh).
case "$LIBRARY" in
    music) LIB_DIR="Music"; NOUN="album" ;;
    movies) LIB_DIR="Movies"; NOUN="movie" ;;
    tv) LIB_DIR="TV"; NOUN="file" ;;
    *) usage ;;
esac

if ! $DRY_RUN && [[ -z "$REMOTE_ROOT" ]]; then
    echo "error: no library root on the Deluge host — pass --remote-root or set \$BITSEND_REMOTE_ROOT" >&2
    exit 1
fi
REMOTE_ROOT="${REMOTE_ROOT%/}"

mkdir -p "$STAGING_DIR"
STAGING_DIR="$(realpath -- "$STAGING_DIR")"
SOURCE_DIR="$STAGING_DIR/$LIB_DIR"
SENT_DIR="$(dirname "$STAGING_DIR")/sent/$LIB_DIR"
mkdir -p "$SOURCE_DIR"
[[ ${#PATHS[@]} -gt 0 ]] || PATHS=("$SOURCE_DIR")

# Refuses a path that isn't a whole unit or a folder of them.
check_path() {
    local rel="$1"
    case "$LIBRARY:$rel" in
        music:*/*/*) echo "error: not an artist or <Artist>/<Album> folder: $LIB_DIR/$rel" >&2; return 1 ;;
        movies:*/*) echo "error: send the whole movie, not part of one: $LIB_DIR/$rel" >&2; return 1 ;;
    esac
}

# Expands one path into the items it holds, NUL-separated.
items_under() {
    local p="$1" rel="$2"
    case "$LIBRARY:$rel" in
        # <Artist>/<Album>: depth 2 under the library folder.
        music:) find "$p" -mindepth 2 -maxdepth 2 -type d -print0 ;;
        music:*/*) printf '%s\0' "$p" ;;
        music:*) find "$p" -mindepth 1 -maxdepth 1 -type d -print0 ;;
        movies:) find "$p" -mindepth 1 -maxdepth 1 ! -name '.*' -print0 ;;
        movies:*) printf '%s\0' "$p" ;;
        tv:*) find "$p" -type f ! -name '.*' -print0 ;;
    esac
}

file_count() {
    find "$1" -type f ! -name '.*' | wc -l
}

ITEMS=()
for p in "${PATHS[@]}"; do
    if [[ ! -e "$p" ]]; then
        echo "error: not found: $p" >&2
        exit 1
    fi
    p="$(realpath -- "$p")"
    case "$p" in
        "$SOURCE_DIR"|"$SOURCE_DIR"/*) ;;
        *) echo "error: not inside $SOURCE_DIR: $p" >&2; exit 1 ;;
    esac
    rel="${p#"$SOURCE_DIR"}"
    rel="${rel#/}"
    if [[ "$LIBRARY" == music && "$rel" == */* && ! -d "$p" ]]; then
        echo "error: not an album folder: $LIB_DIR/$rel" >&2
        exit 1
    fi
    check_path "$rel" || exit 1
    while IFS= read -r -d '' item; do
        # Empty folders found along the way are skipped; one named
        # outright still fails the check below.
        if [[ "$item" != "$p" && $(file_count "$item") -eq 0 ]]; then
            continue
        fi
        ITEMS+=("$item")
    done < <(items_under "$p" "$rel" | sort -z)
done

if [[ ${#ITEMS[@]} -eq 0 ]]; then
    echo "Nothing to send in $SOURCE_DIR."
    exit 0
fi

check_item() {
    local item="$1" rel files
    rel="${item#"$SOURCE_DIR"/}"
    if [[ -e "$SENT_DIR/$rel" ]]; then
        echo "error: already sent (found in $SENT_DIR): $rel" >&2
        return 1
    fi
    if [[ -n "$(find "$item" -type f \( -name 'x[0-9][0-9].wav' -o -iname '*.part' \
            -o -iname '*.partial' -o -iname '*.tmp' \) -print -quit)" ]]; then
        echo "error: temporary files present, rip or copy still running? $rel" >&2
        return 1
    fi
    files=$(file_count "$item")
    if [[ $files -eq 0 ]]; then
        echo "error: no files in $rel" >&2
        return 1
    fi
    if [[ -d "$item" ]]; then
        echo "    $rel ($files files)"
    else
        echo "    $rel"
    fi
}

echo "==> Checking $LIB_DIR..."
for item in "${ITEMS[@]}"; do
    check_item "$item"
done

if $DRY_RUN; then
    echo "==> Dry run, nothing sent."
    exit 0
fi
if ! $ASSUME_YES; then
    read -r -p "Send ${#ITEMS[@]} ${NOUN}(s) to the media server? [y/N] " REPLY
    [[ "$REPLY" == [yY]* ]] || { echo "Cancelled."; exit 0; }
fi

# Removes folders a send left empty, up to (not including) the library folder.
prune_empty_parents() {
    local dir="$1"
    while [[ "$dir" == "$SOURCE_DIR"/* ]]; do
        rmdir -- "$dir" 2>/dev/null || return 0
        dir="$(dirname "$dir")"
    done
}

SENT_COUNT=0
for item in "${ITEMS[@]}"; do
    rel="${item#"$SOURCE_DIR"/}"
    parent_rel="$(dirname "$rel")"
    if [[ "$parent_rel" == "." ]]; then
        move_to="$REMOTE_ROOT/$LIB_DIR"
    else
        move_to="$REMOTE_ROOT/$LIB_DIR/$parent_rel"
    fi
    # Unique per item, since the torrent's filename is its slot in the seed
    # daemon's watch dir: two artists' "Greatest Hits", or every show's
    # "Season 01", would otherwise collide.
    name="${rel//\// - }"
    echo "==> Sending $LIB_DIR/$rel..."
    if ! "$SYNC_SCRIPT" --path "$item" --label "$LIBRARY" \
            --move-to "$move_to" \
            --torrent-name "$name" \
            --archive-to "$SENT_DIR/$rel"; then
        echo "error: stopped at $rel. $SENT_COUNT ${NOUN}(s) sent before this." >&2
        exit 1
    fi
    prune_empty_parents "$(dirname "$item")"
    SENT_COUNT=$((SENT_COUNT + 1))
done
echo "==> Done. $SENT_COUNT ${NOUN}(s) sent. Scan the $LIB_DIR library on your media server."
