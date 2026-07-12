#!/bin/bash
set -euo pipefail

# BitDVR Dock helper — macOS only. Puts a permanent BitDVR launcher in
# the Dock (or removes it with --remove). This is deliberately a
# separate, user-invoked command rather than part of `brew install`:
# Homebrew's sandbox blocks formulas from writing outside the prefix,
# and self-pinning to the Dock without consent is hostile anyway — no
# supported macOS API exists for it, so this edits the Dock's own
# preferences the way `defaults` allows and restarts the Dock to apply
# (expect a brief flicker).
#
# What pinning does:
#   1. symlinks the installed BitDVR.app into ~/Applications (kept
#      fresh across brew upgrades, since it points at the opt path)
#   2. appends a persistent-apps tile to com.apple.dock unless one for
#      BitDVR.app already exists
#   3. restarts the Dock
#
# Usage: bitdvr-dock [--remove]

[[ "$(uname -s)" == "Darwin" ]] || { echo "error: bitdvr-dock is macOS-only — on Linux, install.sh already adds an application-menu entry" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${BITDVR_PYTHON:-python3}"

# Installed layout: this script lives in <prefix>/libexec with the app
# bundle one level up. A repo checkout has no bundle to pin.
APP_SRC="$(cd "$SCRIPT_DIR/.." && pwd)/BitDVR.app"
APP_LINK="$HOME/Applications/BitDVR.app"

REMOVE=0
case "${1:-}" in
    --remove) REMOVE=1 ;;
    "") ;;
    -h|--help) sed -n '4,21p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "usage: bitdvr-dock [--remove]" >&2; exit 1 ;;
esac

pinned() {
    defaults read com.apple.dock persistent-apps 2>/dev/null | grep -q "Applications/BitDVR.app"
}

if [[ $REMOVE -eq 1 ]]; then
    if ! pinned; then
        echo "BitDVR isn't pinned to the Dock — nothing to do."
        exit 0
    fi
    # There's no `defaults delete-array-element`, so round-trip the
    # Dock's plist: export, drop BitDVR tiles, import. Going through
    # `defaults` (not editing the .plist file) keeps cfprefsd's cache
    # coherent.
    TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
    defaults export com.apple.dock "$TMP"
    "$PYTHON" - "$TMP" <<'EOF'
import plistlib, sys
path = sys.argv[1]
with open(path, "rb") as f:
    dock = plistlib.load(f)
dock["persistent-apps"] = [
    tile for tile in dock.get("persistent-apps", [])
    if "BitDVR.app" not in tile.get("tile-data", {}).get("file-data", {}).get("_CFURLString", "")
]
with open(path, "wb") as f:
    plistlib.dump(dock, f)
EOF
    defaults import com.apple.dock "$TMP"
    killall Dock
    echo "==> Removed BitDVR from the Dock (the symlink in ~/Applications is kept)."
    exit 0
fi

[[ -d "$APP_SRC" ]] || { echo "error: $APP_SRC not found — this command works from a Homebrew install (brew install --HEAD bitdvr), not a repo checkout" >&2; exit 1; }

mkdir -p "$HOME/Applications"
ln -sfn "$APP_SRC" "$APP_LINK"
echo "==> Linked $APP_LINK"

if pinned; then
    echo "==> Already pinned to the Dock — nothing else to do."
    exit 0
fi

defaults write com.apple.dock persistent-apps -array-add "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>file://$APP_LINK/</string><key>_CFURLStringType</key><integer>15</integer></dict></dict></dict>"
killall Dock
echo "==> Pinned BitDVR to the Dock (the Dock restarts to apply — that flicker is normal)."
echo "    Unpin any time with: bitdvr-dock --remove (or drag the icon off the Dock)"
