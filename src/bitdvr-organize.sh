#!/bin/bash
set -euo pipefail

# BitDVR step 1 of 2 — organizes a raw OBS recording into the local
# staging library (~/Videos/OBS/Library) using Plex's expected folder
# layout, *before* any network transfer happens. This is a purely local
# rename+move — no torrent, no Deluge, nothing sent yet. Run
# bitdvr-sync.sh separately (step 2) once you're ready to send a file, a
# whole season, or a whole series to Plex.
#
# --label picks the library root (Movies/ or TV/) — this also becomes
# the Deluge label bitdvr-sync.sh applies later, so it must stay in sync
# with the plugin's own "movies"/"tv" labels (see plex/deluge.md) rather
# than be inferred from anything else, e.g. from --series being set.
#
# --dest-root overrides where the Movies/TV roots live, in place of the
# default $RECORDINGS_DIR/Library staging area — this is what the GUI's
# Local mode passes when BitDVR runs on the same host as the media
# server, so Organize delivers straight into the live Plex library
# instead of a local staging area that a separate bitdvr-sync.sh send
# would otherwise archive out of.
#
# --series/--season/--episode are independent of --label: either label
# can optionally be organized as part of a series, landing under
# <label-root>/<Series>/[Season NN/] instead of flat, with a Plex-style
# S01E02 tag built from whatever of season/episode is given. Every field
# below is optional — an omitted one is simply left out of the resulting
# name/path rather than required, and the original filename is kept as a
# last-resort fallback if nothing at all is given. Naming follows Plex's
# own conventions where possible, since that's what its scanner actually
# keys on:
#   Flat (no series fields given): "Title (Year).mkv" for movies (Year
#     optional but recommended — Plex matches movies far more reliably
#     with one), or just "Title.mkv" for tv.
#   Series mode (any --series/--season/--episode given): "<Series> -
#     S01E02 - <Title>.mkv" inside <Series>/Season 01/ — the S01E02
#     segment is what Plex's TV scanner keys on, so leaving both season
#     and episode blank means Plex likely won't recognize the file as an
#     episode even though this script will still organize it by name.

# macOS has no ~/Videos; ~/Movies is where OBS defaults its output
# there. Must stay in sync with the same branch in bitdvr-gui.py /
# bitdvr-sync.sh / bitdvr-magnet.sh.
if [[ "$(uname -s)" == "Darwin" ]]; then
    RECORDINGS_DIR="$HOME/Movies/OBS"
else
    RECORDINGS_DIR="$HOME/Videos/OBS"
fi
LIBRARY_DIR="$RECORDINGS_DIR/Library"

usage() {
    cat >&2 <<EOF
Usage:
  $0 --label movies|tv [--title "Title"] [--year YYYY] \\
     [--series "Series Name"] [--season N] [--episode N] [file]

  file       Recording to organize (default: newest .mkv directly in $RECORDINGS_DIR)
  --label    movies or tv — selects the library root AND the Deluge label
             bitdvr-sync.sh applies later; keep in sync with plex/deluge.md
  --title    optional: base name (a movie/TV title, or an episode title
             when --series/--season/--episode are also given)
  --year     movies only, optional: appended as "Title (Year)"
  --series   optional: series name — organizes under <label-root>/<Series>/
  --season   optional: season number — adds a "Season NN" subfolder
  --episode  optional: episode number
  --dest-root  optional: use this dir's Movies/TV roots instead of
             $RECORDINGS_DIR/Library (Local mode)

  Everything except --label is optional. Omitted fields are simply left
  out of the resulting name/path instead of required; with nothing given
  at all, the original filename is kept as-is.
EOF
    exit 1
}

LABEL=""
TITLE=""
YEAR=""
SERIES=""
SEASON=""
EPISODE=""
FILE=""
DEST_ROOT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --label) LABEL="$2"; shift 2 ;;
        --title) TITLE="$2"; shift 2 ;;
        --year) YEAR="$2"; shift 2 ;;
        --series) SERIES="$2"; shift 2 ;;
        --season) SEASON="$2"; shift 2 ;;
        --episode) EPISODE="$2"; shift 2 ;;
        --dest-root) DEST_ROOT="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) FILE="$1"; shift ;;
    esac
done

if [[ "$LABEL" != "movies" && "$LABEL" != "tv" ]]; then
    echo "error: --label must be 'movies' or 'tv'" >&2
    exit 1
fi

[[ -n "$DEST_ROOT" ]] && LIBRARY_DIR="$DEST_ROOT"

if [[ -z "$FILE" ]]; then
    # "mtime path" lines, newest first — GNU find spells this -printf,
    # BSD find (macOS) has no -printf so stat -f fills in.
    if [[ "$(uname -s)" == "Darwin" ]]; then
        FILE=$(find "$RECORDINGS_DIR" -maxdepth 1 -name "*.mkv" -exec stat -f '%m %N' {} + 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
    else
        FILE=$(find "$RECORDINGS_DIR" -maxdepth 1 -name "*.mkv" -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
    fi
    if [[ -z "$FILE" ]]; then
        echo "error: no unorganized .mkv recordings found directly in $RECORDINGS_DIR" >&2
        exit 1
    fi
fi

if [[ ! -f "$FILE" ]]; then
    echo "error: file not found: $FILE" >&2
    exit 1
fi

if [[ -n "$SEASON" && ! "$SEASON" =~ ^[0-9]+$ ]]; then
    echo "error: --season must be a number" >&2
    exit 1
fi
if [[ -n "$EPISODE" && ! "$EPISODE" =~ ^[0-9]+$ ]]; then
    echo "error: --episode must be a number" >&2
    exit 1
fi
if [[ "$LABEL" != "movies" && -n "$YEAR" ]]; then
    echo "error: --year only applies to --label movies" >&2
    exit 1
fi

EXT="${FILE##*.}"
STEM="$(basename "$FILE")"
STEM="${STEM%.*}"

LIBRARY_ROOT="$LIBRARY_DIR/Movies"
[[ "$LABEL" == "tv" ]] && LIBRARY_ROOT="$LIBRARY_DIR/TV"

if [[ -n "$SERIES" || -n "$SEASON" || -n "$EPISODE" ]]; then
    SERIES_DIR="${SERIES:-Unsorted}"
    DEST_DIR="$LIBRARY_ROOT/$SERIES_DIR"

    SXE=""
    if [[ -n "$SEASON" ]]; then
        SEASON_PAD=$(printf "%02d" "$SEASON")
        DEST_DIR="$DEST_DIR/Season $SEASON_PAD"
        SXE="S${SEASON_PAD}"
    fi
    if [[ -n "$EPISODE" ]]; then
        EPISODE_PAD=$(printf "%02d" "$EPISODE")
        SXE="${SXE}E${EPISODE_PAD}"
    fi

    PARTS=()
    [[ -n "$SERIES" ]] && PARTS+=("$SERIES")
    [[ -n "$SXE" ]] && PARTS+=("$SXE")
    [[ -n "$TITLE" ]] && PARTS+=("$TITLE")

    if [[ ${#PARTS[@]} -eq 0 ]]; then
        BASENAME="$STEM"
    else
        BASENAME=""
        for part in "${PARTS[@]}"; do
            if [[ -z "$BASENAME" ]]; then
                BASENAME="$part"
            else
                BASENAME="$BASENAME - $part"
            fi
        done
    fi
else
    DEST_DIR="$LIBRARY_ROOT"
    if [[ -n "$TITLE" ]]; then
        BASENAME="$TITLE"
        [[ -n "$YEAR" ]] && BASENAME="$BASENAME ($YEAR)"
    else
        BASENAME="$STEM"
    fi
fi

DEST="$DEST_DIR/${BASENAME}.${EXT}"

if [[ -e "$DEST" ]]; then
    echo "error: already exists: $DEST" >&2
    exit 1
fi

mkdir -p "$DEST_DIR"
mv -- "$FILE" "$DEST"
echo "==> Organized: ${DEST#"$LIBRARY_DIR"/}"
echo "    $DEST"
