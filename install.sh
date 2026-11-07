#!/bin/bash
set -euo pipefail

# BitSend installer — Linux (systemd). See docs/README.md for the manual
# equivalent of everything this does.
#
# Design (see also uninstall.sh):
#   - Never runs as root. Everything lands in the user's own home:
#     ~/.local/bin, ~/.config/systemd/user, ~/.local/share/applications.
#   - Idempotent: re-running upgrades the installed copies in place and
#     re-issues already-satisfied steps harmlessly.
#   - Non-destructive: never touches ~/.config/bitsend/config.json,
#     keyring secrets, or your media.
#   - Installs alongside BitDVR without touching it: by default nothing
#     is shared with it, including the seed daemon's port.
#   - Compiled dependencies (libtorrent bindings, Tk) come from the
#     distro's own package manager — this script never builds them.
#
# Usage: ./install.sh [--skip-deps] [--use-bitdvr-daemon]
#   --skip-deps   don't invoke the package manager; assume dependencies
#                 are already present (they're verified either way).
#   --use-bitdvr-daemon  seed through BitDVR's already-running daemon
#                 (its watch dir and port 51413) instead of installing
#                 BitSend's own. For machines with BitDVR installed where a
#                 firewall already allows its port. BitSend can't send if
#                 that daemon is stopped or BitDVR is uninstalled; rerun
#                 without this flag to go back to BitSend's own daemon.

BIN_DIR="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
APP_DIR="$HOME/.local/share/applications"
SERVICE="bitsend-libtorrent-daemon"
BITDVR_SERVICE="bitdvr-libtorrent-daemon"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SKIP_DEPS=0
USE_BITDVR_DAEMON=0
for arg in "$@"; do
    case "$arg" in
        --skip-deps) SKIP_DEPS=1 ;;
        --use-bitdvr-daemon) USE_BITDVR_DAEMON=1 ;;
        -h|--help)
            sed -n '4,27p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *) echo "unknown option: $arg (try --help)" >&2; exit 1 ;;
    esac
done

info() { echo "==> $*"; }
warn() { echo "WARNING: $*" >&2; }
die()  { echo "error: $*" >&2; exit 1; }

if [[ "$(uname -s)" == "Darwin" ]]; then
    die "on macOS, install through Homebrew instead:
    brew tap lancebramsay/bitsend https://github.com/lancebramsay/BitSend.git
    brew trust lancebramsay/bitsend
    brew install --HEAD bitsend"
fi
[[ "${EUID}" -eq 0 ]] && die "run this as your normal user, not root — everything installs into your own home directory"
if [[ $USE_BITDVR_DAEMON -eq 1 ]] && ! systemctl --user is-active --quiet "$BITDVR_SERVICE"; then
    die "--use-bitdvr-daemon needs BitDVR's seed daemon running ($BITDVR_SERVICE isn't active)"
fi
[[ -f "$REPO_DIR/src/bitsend-gui.py" ]] || die "run this from a BitSend checkout (src/bitsend-gui.py not found next to install.sh)"

# ------------------------------------------------------------ interpreters

# The GUI needs a Python with tkinter; the torrent scripts need one with
# libtorrent. Usually the same /usr/bin/python3, but not always (e.g. a
# Homebrew-on-Linux Python providing both while the system one has
# neither) — so detect each separately, BEFORE deciding any package is
# missing, and pin the installed copies to what was actually found
# rather than hoping `env python3` resolves to the right thing at
# runtime.
have_tk()  { "$1" -c 'import tkinter' >/dev/null 2>&1; }
have_lt()  { "$1" -c 'import libtorrent' >/dev/null 2>&1; }

GUI_PYTHON=""
LT_PYTHON=""
scan_interpreters() {
    local candidates=(python3 /usr/bin/python3 /home/linuxbrew/.linuxbrew/bin/python3 /opt/homebrew/bin/python3) c p
    [[ -n "${BITSEND_PYTHON:-}" ]] && candidates=("$BITSEND_PYTHON" "${candidates[@]}")
    GUI_PYTHON=""
    LT_PYTHON=""
    for c in "${candidates[@]}"; do
        p="$(command -v "$c" 2>/dev/null || true)"
        [[ -n "$p" ]] || continue
        [[ -z "$GUI_PYTHON" ]] && have_tk "$p" && GUI_PYTHON="$p"
        [[ -z "$LT_PYTHON" ]] && have_lt "$p" && LT_PYTHON="$p"
    done
}
scan_interpreters

# ---------------------------------------------------------------- packages

# Only what's genuinely missing gets installed — a dependency counts as
# satisfied if ANY detected interpreter provides it, not just the
# default python3. Package names per distro family; libtorrent
# Python-binding names especially vary, so those are candidate lists —
# the first one the package manager recognizes wins.
PM=""
if command -v rpm-ostree >/dev/null; then PM=atomic
elif command -v apt-get >/dev/null; then PM=apt
elif command -v dnf >/dev/null; then PM=dnf
elif command -v pacman >/dev/null; then PM=pacman
fi

pkg_install() { # pkg_install <package>...
    case "$PM" in
        apt) sudo apt-get install -y "$@" ;;
        dnf) sudo dnf install -y "$@" ;;
        pacman) sudo pacman -S --needed --noconfirm "$@" ;;
        *) return 1 ;;
    esac
}

missing=()
if ! command -v jq >/dev/null; then missing+=(jq); fi
if ! command -v secret-tool >/dev/null; then
    case "$PM" in
        apt) missing+=(libsecret-tools) ;;
        *) missing+=(libsecret) ;;
    esac
fi
if [[ -z "$GUI_PYTHON" ]]; then
    case "$PM" in
        apt) missing+=(python3-tk) ;;
        dnf) missing+=(python3-tkinter) ;;
        pacman) missing+=(tk) ;;
        *) missing+=(python3-tk) ;;
    esac
fi

if [[ $SKIP_DEPS -eq 0 && ${#missing[@]} -gt 0 ]]; then
    case "$PM" in
        atomic)
            # Immutable/rpm-ostree distros (Silverblue, Bazzite, ...) —
            # layering packages there is the user's call, not this
            # script's. Homebrew-on-Linux is usually the better route.
            die "this looks like an immutable (rpm-ostree) distro — install these yourself (e.g. 'brew install ${missing[*]}' or 'rpm-ostree install ${missing[*]}'), then rerun: ${missing[*]}"
            ;;
        "")
            die "no supported package manager found (apt/dnf/pacman) — install these yourself, then rerun: ${missing[*]}"
            ;;
        *)
            info "Installing packages (sudo): ${missing[*]}"
            pkg_install "${missing[@]}"
            ;;
    esac
fi

if [[ $SKIP_DEPS -eq 0 && -z "$LT_PYTHON" ]]; then
    # libtorrent Python bindings — the package name varies by distro and
    # some distros ship only the C++ library. Try known names, then fall
    # back to pip only with an explicit prompt (a source build is slow
    # and failure-prone enough that it should never happen silently).
    lt_candidates=()
    case "$PM" in
        apt) lt_candidates=(python3-libtorrent) ;;
        dnf) lt_candidates=(python3-libtorrent rb_libtorrent-python3) ;;
        pacman) lt_candidates=(libtorrent-rasterbar) ;;
        atomic) die "this looks like an immutable (rpm-ostree) distro — install Python libtorrent bindings yourself (e.g. 'brew install libtorrent-rasterbar'), then rerun" ;;
    esac
    lt_ok=0
    for pkg in "${lt_candidates[@]}"; do
        if pkg_install "$pkg" 2>/dev/null && have_lt python3; then lt_ok=1; break; fi
    done
    if [[ $lt_ok -eq 0 ]]; then
        warn "no distro package provided Python libtorrent bindings."
        read -r -p "Try 'pip install libtorrent' (prebuilt wheel if available)? [y/N] " yn
        if [[ "$yn" == [Yy]* ]]; then
            python3 -m pip install --user libtorrent || warn "pip install failed"
        fi
    fi
fi

# Anything just installed shows up on a rescan.
scan_interpreters
[[ -n "$GUI_PYTHON" ]] || die "no Python with tkinter found — install your distro's Tk package (see docs/README.md) and rerun"
[[ -n "$LT_PYTHON" ]] || die "no Python with libtorrent bindings found — install them (see docs/README.md) and rerun"
info "GUI Python: $GUI_PYTHON"
info "libtorrent Python: $LT_PYTHON"

# ------------------------------------------------------------------ files

info "Installing BitSend to $BIN_DIR"
mkdir -p "$BIN_DIR"
# Left behind by earlier installs: the movies/TV/music build's organizer
# and magnet helpers, and the music-only build's bitsend-music.sh.
rm -f "$BIN_DIR/bitsend-organize.sh" "$BIN_DIR/bitsend-magnet.sh" "$BIN_DIR/bitsend-magnet-uri.py" \
      "$BIN_DIR/bitsend-music.sh"
for f in bitsend-gui.py bitsend-sync.sh bitsend-send.sh \
         bitsend-create-torrent.py bitsend-libtorrent-daemon.py; do
    install -m 0755 "$REPO_DIR/src/$f" "$BIN_DIR/$f"
done

# Pin interpreters in the installed copies (repo copies stay generic).
sed -i "1s|^#!/usr/bin/env python3$|#!$GUI_PYTHON|" "$BIN_DIR/bitsend-gui.py"
sed -i "1s|^#!/usr/bin/env python3$|#!$LT_PYTHON|" \
    "$BIN_DIR/bitsend-create-torrent.py" "$BIN_DIR/bitsend-libtorrent-daemon.py"
sed -i "s|^PYTHON=\"\${BITSEND_PYTHON:-python3}\"$|PYTHON=\"\${BITSEND_PYTHON:-$LT_PYTHON}\"|" \
    "$BIN_DIR/bitsend-sync.sh"

if [[ $USE_BITDVR_DAEMON -eq 1 ]]; then
    info "Pointing BitSend at BitDVR's seed daemon"
    sed -i -e 's|^SEED_WATCH_DIR="\$HOME/.config/bitsend-libtorrent-daemon/watch"$|SEED_WATCH_DIR="$HOME/.config/bitdvr-libtorrent-daemon/watch"|' \
        -e 's|^PEER_PORT="\${BITSEND_PEER_PORT:-51414}"$|PEER_PORT="${BITSEND_PEER_PORT:-51413}"|' \
        "$BIN_DIR/bitsend-sync.sh"
    sed -i 's|^SEED_WATCH_DIR = .*$|SEED_WATCH_DIR = Path.home() / ".config" / "bitdvr-libtorrent-daemon" / "watch"|' \
        "$BIN_DIR/bitsend-gui.py"
    rm -f "$BIN_DIR/bitsend-libtorrent-daemon.py"
fi

info "Installing desktop entry"
mkdir -p "$APP_DIR"
# StartupWMClass matches the WM_CLASS the GUI sets on itself, so
# taskbars associate its window with this entry (and its icon) instead
# of showing a generic interpreter icon. document-send is a standard
# freedesktop theme icon, present in common icon themes.
cat > "$APP_DIR/bitsend.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=BitSend
Comment=Send music, movies, and TV to your media server over private LAN BitTorrent
Exec=$BIN_DIR/bitsend-gui.py
Icon=document-send
Terminal=false
Categories=Network;FileTransfer;
StartupWMClass=bitsend
EOF

# ---------------------------------------------------------------- service

if [[ $USE_BITDVR_DAEMON -eq 1 ]]; then
    # A previous standalone install's service would hold nothing but a port.
    systemctl --user disable --now "$SERVICE" 2>/dev/null || true
    rm -f "$UNIT_DIR/$SERVICE.service"
    systemctl --user daemon-reload 2>/dev/null || true
    info "Using BitDVR's seed daemon ($BITDVR_SERVICE); no service of its own installed"
else
    info "Installing seed daemon service ($SERVICE)"
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/$SERVICE.service" <<EOF
[Unit]
Description=BitSend libtorrent seed daemon
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=$BIN_DIR/bitsend-libtorrent-daemon.py
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
EOF
    # Warn rather than die: on non-systemd setups (or odd sessions) the
    # unit file is still in place for the user to wire up themselves.
    if systemctl --user daemon-reload 2>/dev/null && \
       systemctl --user enable --now "$SERVICE" 2>/dev/null; then
        info "Seed daemon enabled and running"
    else
        warn "could not enable the user service automatically — run: systemctl --user enable --now $SERVICE"
    fi
fi

# ------------------------------------------------------------------- done

echo
info "Done. Next steps:"
echo "    1. Launch BitSend from your app menu, or: $BIN_DIR/bitsend-gui.py"
echo "    2. Open the Settings tab and configure it — see docs/README.md#first-run"
echo "    Logs: ~/.local/share/bitsend/logs/   Uninstall: ./uninstall.sh"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH — the desktop entry still works, but add it to your shell profile for CLI use" ;;
esac
