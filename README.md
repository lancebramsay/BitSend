# BitDVR

**A personal DVR for your desktop.** BitDVR is a small taskbar app that
records what's playing on your screen, names and files the recording
the way media servers like Plex and Jellyfin expect, and — if your
media server lives on another machine — delivers it there over a
private, LAN-only BitTorrent transfer. Think of it as a modern VCR:
press record, get a neatly labeled tape, put the tape on the shelf.

BitDVR doesn't capture video itself — it drives
[OBS Studio](https://obsproject.com/), the same free recording software
used by streamers everywhere, and handles everything around it:
start/stop control with an auto-stop timer, pulling the title and
runtime from whatever's playing, renaming files into
`Show - S01E02.mkv`-style names, sorting them into `Movies/` and `TV/`
folders, and getting the finished file onto your media server.

## What it does

- **Record** — start/stop OBS from a simple GUI, with a live preview,
  an auto-stop timer, and optional auto start/stop tied to your media
  player's playback state.
- **Organize** — rename and file recordings using the folder and naming
  conventions Plex and Jellyfin actually scan for (movies with years,
  shows with seasons and episode numbers).
- **Send** *(network path)* — transfer finished recordings to a media
  server on another machine as a direct, two-peer BitTorrent transfer.
  No public trackers, no DHT, no strangers: every torrent is trackerless
  and flagged private, and the transfer never leaves your LAN.
- **Local mode** *(local path)* — if BitDVR runs on the same machine as
  your media server, skip the network entirely and organize recordings
  straight into your library folder.

## What it doesn't do

BitDVR is built for recording **your own content, for your own private
viewing, on your own network** — the same personal fair use that has
covered home recording since the VCR. Deliberately:

- **No DRM circumvention.** It captures what your screen renders during
  normal playback; it never decrypts or bypasses any copy protection.
- **No downloading.** It cannot fetch content from the internet —
  BitTorrent is used purely as a delivery mechanism between two
  machines you own.
- **No sharing.** Its torrents are invisible to public trackers and DHT
  by design, and distributing recordings to anyone outside your
  household is neither supported nor condoned.

Recording may also be restricted by the **terms of service of paid
streaming platforms** regardless of copyright law — that's on you to
check. See [LEGAL.md](LEGAL.md) for the fair-use basis, the case law
behind it, and the full disclaimer. Good starting points:
[Fair use](https://en.wikipedia.org/wiki/Fair_use) ·
[Time shifting](https://en.wikipedia.org/wiki/Time_shifting) ·
[17 U.S.C. § 107](https://www.law.cornell.edu/uscode/text/17/107).

## Getting started

```sh
git clone https://github.com/lancebramsay/BitDVR.git
cd BitDVR
./install.sh
```

Then head to the **[User Guide](docs/README.md)**:

- [Getting started](docs/README.md) — requirements, installation, and
  choosing between the local and network paths
- [Basics: recording & organizing](docs/basics.md) — the workflow every
  setup shares
- [Local path](docs/local-mode.md) — BitDVR on the same machine as your
  media server
- [Network path](docs/network-mode.md) — sending recordings to another
  machine over LAN BitTorrent
- [Advanced setup](docs/advanced-setup.md) — configuring Plex/Jellyfin
  libraries and the Deluge BitTorrent client

Questions, setup help, and ideas are welcome in
[Discussions](https://github.com/lancebramsay/BitDVR/discussions);
reproducible bugs belong in
[Issues](https://github.com/lancebramsay/BitDVR/issues).

## Requirements at a glance

- Linux with a desktop session
  ([macOS support is planned](https://github.com/lancebramsay/BitDVR/discussions/2))
- [OBS Studio](https://obsproject.com/) 28+ (obs-websocket built in)
- Python 3 with Tkinter
- [libtorrent](https://libtorrent.org/) Python bindings and `jq`
  (network path only)
- A media server such as [Plex](https://www.plex.tv/) or
  [Jellyfin](https://jellyfin.org/), with
  [Deluge](https://deluge-torrent.org/) alongside it for the network
  path

## License

BitDVR is free and open source under the
[GNU General Public License v3.0](LICENSE). It comes with **no
warranty** and its authors accept **no liability** for how it is used
(GPL-3.0 §§15–16). Vendored dependencies keep their own licenses:
[`obsws-python`](src/vendor/obsws_python/LICENSE) (GPL-3.0),
[`websocket-client`](src/vendor/websocket/LICENSE) (Apache-2.0).

The project artwork ([`docs/assets/social-preview.png`](docs/assets/social-preview.png))
incorporates the `video-television` icon from KDE's
[Breeze icon theme](https://invent.kde.org/frameworks/breeze-icons)
(LGPL-3.0-or-later) — the same theme icon the app uses on the desktop.
