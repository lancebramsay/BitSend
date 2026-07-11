# BitDVR User Guide

BitDVR records what's playing on your screen (via OBS Studio), names
and files the recording the way media servers expect, and gets it into
your media library. This guide covers installation and both ways of
running it.

## Contents

1. **Getting started** (this page) — requirements, installation,
   choosing a path
2. [Basics: recording & organizing](basics.md) — the workflow every
   setup shares
3. [Local path](local-mode.md) — BitDVR on the same machine as your
   media server
4. [Network path](network-mode.md) — sending recordings to another
   machine over LAN BitTorrent
5. [Advanced setup](advanced-setup.md) — Plex/Jellyfin libraries and
   the Deluge BitTorrent client

## The two paths

Everything starts the same: BitDVR drives OBS to **record**, then
**organizes** the file with media-server-friendly names and folders.
What happens next depends on where your media server lives:

| | Local path | Network path |
|---|---|---|
| Media server runs on | the same machine as BitDVR | a different machine on your LAN |
| After organizing | file lands directly in your library folder | file is sent to the server over a private, two-peer BitTorrent transfer |
| Extra components | none | Deluge (on the server) + BitDVR's seed daemon (on the recording machine) |
| GUI tabs | Record, Organize | Record, Organize, Send, Clean |

If you're not sure, start with the **local path** — it has no network
components at all, and you can switch to the network path later from
the Settings tab.

## Requirements

- **Linux** with a desktop session (X11 or Wayland). macOS support is
  planned.
- **[OBS Studio](https://obsproject.com/) 28 or newer** — version 28
  ships with the WebSocket server BitDVR uses for control. Enable it
  under *Tools → WebSocket Server Settings* and set a password.
- **Python 3.10+ with Tkinter** — `python3-tk` (Debian/Ubuntu),
  `python3-tkinter` (Fedora), `tk` (Arch), or
  `brew install python-tk`.
- **`secret-tool`** (package `libsecret` / `libsecret-tools`) — BitDVR
  stores the OBS and Deluge passwords in your desktop keyring, never in
  plain text on disk.

Additionally, for the **network path** only:

- **libtorrent Python bindings** — `python3-libtorrent`
  (Debian/Ubuntu), `libtorrent-rasterbar` with Python bindings, or
  `brew install libtorrent-rasterbar`.
- **`jq`** — used to build API requests safely from free-text titles.
- **[Deluge](https://deluge-torrent.org/)** with its web UI on the
  media-server machine — covered in [Advanced setup](advanced-setup.md).

## Installation

BitDVR is plain scripts — installation is copying them somewhere on
your `PATH`:

```sh
git clone https://github.com/<you>/bitdvr.git
cd bitdvr
mkdir -p ~/.local/bin
cp bitdvr-*.py bitdvr-*.sh ~/.local/bin/
cp -r vendor ~/.local/bin/
```

> **Note:** the script shebangs currently point at a Homebrew Python
> (`/home/linuxbrew/.linuxbrew/...`). If your Python lives elsewhere,
> update the first line of each `.py` file and the `PYTHON=` variable
> near the top of the `.sh` scripts to match (e.g.
> `/usr/bin/python3`). A proper installer that handles this is on the
> roadmap.

Launch the GUI with `bitdvr-gui.py`. To pin it to your taskbar, drop a
desktop entry at `~/.local/share/applications/bitdvr.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=BitDVR
Exec=/home/YOU/.local/bin/bitdvr-gui.py
Categories=AudioVideo;Recorder;
```

The **network path** additionally needs the seed daemon running as a
user service — see [Network path: the seed daemon](network-mode.md#the-seed-daemon).

## First run

1. Open the **Settings** tab.
2. Set **OBS WebSocket Host IP** (`127.0.0.1` if OBS runs on the same
   machine) and **Port** (OBS defaults to `4455`).
3. Pick your path:
   - **Local:** tick *Local mode* and set your media library folder
     (e.g. `/mnt/media`). The Send and Clean tabs disappear — see
     [Local path](local-mode.md).
   - **Network:** set **Deluge Host IP** to the media-server machine,
     and **Media library root (on Deluge host)** to the folder there
     that contains `Movies/` and `TV/` (e.g. `/mnt/media`) — see
     [Network path](network-mode.md).
4. Click **Save**. The first time BitDVR talks to OBS (and, on the
   network path, to Deluge) it prompts once for that password and
   stores it in your desktop keyring.

## Where BitDVR keeps things

| What | Where |
|---|---|
| Raw OBS recordings | `~/Videos/OBS/` (set in OBS itself) |
| Organized staging library (network path) | `~/Videos/OBS/Library/` |
| Sent-item archive (network path) | `~/Videos/OBS/Library/.sent/` |
| Settings | `~/.config/bitdvr/config.json` |
| Passwords | your desktop keyring (via `secret-tool`), never on disk |
| Logs (one per run) | `~/.local/share/bitdvr/logs/` |
| Seed daemon watch dir (network path) | `~/.config/bitdvr-libtorrent-daemon/watch/` |

Next: [Basics — recording & organizing](basics.md).
