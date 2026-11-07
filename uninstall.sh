#!/bin/bash
set -euo pipefail

# BitSend uninstaller — reverses install.sh.
#
# By default this removes only what install.sh created: the scripts in
# ~/.local/bin, the desktop entry, and the seed daemon service. Your
# settings, keyring secrets, and music are all kept. Settings
# can be purged:
#
# Usage: ./uninstall.sh [--purge-config]
#   --purge-config   also delete ~/.config/bitsend,
#                    ~/.config/bitsend-libtorrent-daemon (including any
#                    active seeds), and ~/.local/share/bitsend logs
#
# That asks for confirmation. Keyring secrets are never removed
# automatically; clear them with:
#   secret-tool clear service bitsend-deluge-web account deluge-web

BIN_DIR="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
APP_DIR="$HOME/.local/share/applications"
SERVICE="bitsend-libtorrent-daemon"

if [[ "$(uname -s)" == "Darwin" ]]; then
    echo "error: a Homebrew install isn't managed by this script — instead run:" >&2
    echo "    brew services stop bitsend; brew uninstall bitsend" >&2
    exit 1
fi

PURGE_CONFIG=0
for arg in "$@"; do
    case "$arg" in
        --purge-config) PURGE_CONFIG=1 ;;
        -h|--help)
            sed -n '4,17p' "${BASH_SOURCE[0]}"
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
rm -f "$BIN_DIR"/bitsend-gui.py "$BIN_DIR"/bitsend-sync.sh "$BIN_DIR"/bitsend-music.sh \
      "$BIN_DIR"/bitsend-create-torrent.py "$BIN_DIR"/bitsend-libtorrent-daemon.py
rm -f "$APP_DIR/bitsend.desktop"

if [[ $PURGE_CONFIG -eq 1 ]]; then
    if confirm "Delete BitSend settings, seed watch dir, and logs?"; then
        rm -rf "$HOME/.config/bitsend" "$HOME/.config/bitsend-libtorrent-daemon" \
               "$HOME/.local/share/bitsend"
        info "Config and logs removed (keyring secrets kept — see --help)"
    else
        info "Skipped config purge"
    fi
fi

info "BitSend uninstalled."
if [[ $PURGE_CONFIG -eq 0 ]]; then
    echo "    (settings/logs kept — rerun with --purge-config to remove them)"
fi
