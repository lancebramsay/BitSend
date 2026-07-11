#!/bin/bash
set -euo pipefail

# BitDVR uninstaller — reverses install.sh.
#
# By default this removes only what install.sh created: the scripts in
# ~/.local/bin, the desktop entry, and the seed daemon service. Your
# settings, keyring secrets, recordings, and staging library are all
# kept unless explicitly purged:
#
# Usage: ./uninstall.sh [--purge-config] [--purge-library]
#   --purge-config   also delete ~/.config/bitdvr,
#                    ~/.config/bitdvr-libtorrent-daemon (including any
#                    active seeds), and ~/.local/share/bitdvr logs
#   --purge-library  also delete the staging library
#                    (~/Videos/OBS/Library — INCLUDING .sent/ archives)
#
# Both purges ask for confirmation. Keyring secrets are never removed
# automatically; clear them with:
#   secret-tool clear service bitdvr-deluge-web account deluge-web
#   secret-tool clear service bitdvr-obs-websocket account obs-websocket

BIN_DIR="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
APP_DIR="$HOME/.local/share/applications"
SERVICE="bitdvr-libtorrent-daemon"

PURGE_CONFIG=0
PURGE_LIBRARY=0
for arg in "$@"; do
    case "$arg" in
        --purge-config) PURGE_CONFIG=1 ;;
        --purge-library) PURGE_LIBRARY=1 ;;
        -h|--help)
            sed -n '4,21p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *) echo "unknown option: $arg (try --help)" >&2; exit 1 ;;
    esac
done

info() { echo "==> $*"; }

confirm() { # confirm <prompt>
    read -r -p "$1 [y/N] " yn
    [[ "$yn" == [Yy]* ]]
}

info "Stopping seed daemon service"
systemctl --user disable --now "$SERVICE" 2>/dev/null || true
rm -f "$UNIT_DIR/$SERVICE.service"
systemctl --user daemon-reload 2>/dev/null || true

info "Removing installed files from $BIN_DIR"
rm -f "$BIN_DIR"/bitdvr-gui.py "$BIN_DIR"/bitdvr-organize.sh \
      "$BIN_DIR"/bitdvr-sync.sh "$BIN_DIR"/bitdvr-magnet.sh \
      "$BIN_DIR"/bitdvr-create-torrent.py "$BIN_DIR"/bitdvr-magnet-uri.py \
      "$BIN_DIR"/bitdvr-libtorrent-daemon.py
rm -rf "$BIN_DIR/vendor"
rm -f "$APP_DIR/bitdvr.desktop"

if [[ $PURGE_CONFIG -eq 1 ]]; then
    if confirm "Delete BitDVR settings, seed watch dir, and logs?"; then
        rm -rf "$HOME/.config/bitdvr" "$HOME/.config/bitdvr-libtorrent-daemon" \
               "$HOME/.local/share/bitdvr"
        info "Config and logs removed (keyring secrets kept — see --help)"
    else
        info "Skipped config purge"
    fi
fi

if [[ $PURGE_LIBRARY -eq 1 ]]; then
    LIB="$HOME/Videos/OBS/Library"
    if [[ -d "$LIB" ]] && confirm "Delete the staging library at $LIB (including .sent/ archives)?"; then
        rm -rf "$LIB"
        info "Staging library removed"
    else
        info "Skipped library purge"
    fi
fi

info "BitDVR uninstalled."
if [[ $PURGE_CONFIG -eq 0 ]]; then
    echo "    (settings/logs kept — rerun with --purge-config to remove them)"
fi
