# BitSend User Guide

BitSend sends albums to a media server on another machine over a private,
two-peer BitTorrent transfer.

## Contents

1. **Getting started** (this page) — requirements, installation, first run
2. [Sending](sending.md) — the Send and Clean tabs, sending from the command
   line, troubleshooting
3. [Advanced setup](advanced-setup.md) — Plex/Jellyfin libraries and the
   Deluge BitTorrent client
4. [Design & architecture](design.md) — why BitSend is built the way it is

## How it works

BitSend runs on the machine that has the music. Your media server runs
[Deluge](https://deluge-torrent.org/) with its web UI. BitSend seeds each
send from a small background daemon, tells Deluge to fetch it, and waits
until Deluge has moved it into your library folder.

## Requirements

- **Linux** with a desktop session (X11 or Wayland), or **macOS** (installed
  via Homebrew — see below). On macOS every dependency in this list comes in
  automatically with `brew install`, and passwords live in the login
  Keychain instead of the desktop keyring.
- **Python 3.10+ with Tkinter** — `python3-tk` (Debian/Ubuntu),
  `python3-tkinter` (Fedora), `tk` (Arch), or `brew install python-tk`.
- **`secret-tool`** (package `libsecret` / `libsecret-tools`) — BitSend
  stores the Deluge password in your desktop keyring, never in plain text on
  disk.
- **libtorrent Python bindings** — `python3-libtorrent` (Debian/Ubuntu),
  `libtorrent-rasterbar` with Python bindings, or
  `brew install libtorrent-rasterbar`.
- **`jq`** — used to build API requests safely from free-text titles.
- **[Deluge](https://deluge-torrent.org/)** with its web UI on the
  media-server machine — covered in [Advanced setup](advanced-setup.md).

## Installation

### macOS (Homebrew)

```sh
brew tap lancebramsay/bitsend https://github.com/lancebramsay/BitSend.git
brew trust lancebramsay/bitsend
brew install --HEAD bitsend
brew services start bitsend
```

(`brew trust` is Homebrew's standard confirmation step for any tap outside
homebrew-core.)

This installs BitSend plus all its dependencies, puts `bitsend` on your PATH
to launch the GUI, and starts the seed daemon as a launchd service.

The install also builds a `BitSend.app` bundle. To put a permanent launcher
in the Dock, run `bitsend-dock`. It links the app into `~/Applications` and
pins it, and the Dock restarts briefly. Unpin with `bitsend-dock --remove`.
Spotlight doesn't index symlinked apps, so launch BitSend from the Dock,
Finder, or the `bitsend` command.

To remove: `brew services stop bitsend`, then `brew uninstall bitsend`.
Settings, your music, and Keychain entries are kept.

### Linux

```sh
git clone https://github.com/lancebramsay/BitSend.git
cd BitSend
./install.sh
```

The installer never runs as root and touches only your own home directory.
It:

- installs missing dependencies through your distro's package manager
  (`apt`/`dnf`/`pacman`, with a `sudo` prompt for that step only);
- finds a Python with Tkinter and one with libtorrent (not always the same
  one) and pins the installed scripts to them;
- copies BitSend to `~/.local/bin`, adds an application-menu entry, and sets
  up the seed daemon as a `systemd --user` service;
- is safe to re-run any time — updates in place, never overwrites your
  settings.

`--skip-deps` skips the package-manager step if you'd rather install
dependencies yourself. On immutable distros like Bazzite or Silverblue, the
installer stops and lists what's missing instead of layering packages; use
Homebrew on Linux, then rerun.

To remove BitSend, run `./uninstall.sh`. Your music is never touched, and
settings are kept unless you pass `--purge-config`, which asks first.

BitSend can be installed alongside BitDVR. By default they share nothing:
separate scripts, settings, keyring entry, seed daemon, and peer port. If BitDVR's daemon is already running and your firewall allows its
port, `./install.sh --use-bitdvr-daemon` seeds through it instead of adding a
second service. BitSend then can't send while that daemon is stopped or after
BitDVR is uninstalled; rerun `./install.sh` without the flag to switch back.

## First run

1. Open the **Settings** tab.
2. Set **Deluge Host IP** to the media-server machine.
3. Set **Media library root (on Deluge host)** to the folder there that
   contains `Music/` (e.g. `/mnt/media`).
4. Set **Music folder** to your `<Artist>/<Album>` tree, such as a CD ripper's
   output folder. Blank uses `~/Rips/staging`.
5. Click **Save**. The first send prompts once for the Deluge web password
   and stores it in your desktop keyring.

## Where BitSend keeps things

| What | Where |
|---|---|
| Music to send | the **Music folder** setting (default `~/Rips/staging`) |
| Sent albums | a `sent` folder beside it (default `~/Rips/sent`) |
| Settings | `~/.config/bitsend/config.json` |
| Passwords | desktop keyring (via `secret-tool`) — macOS: login Keychain; never on disk |
| Logs (one per run) | `~/.local/share/bitsend/logs/` |
| Seed daemon watch dir | `~/.config/bitsend-libtorrent-daemon/watch/` |

Next: [Sending](sending.md).
