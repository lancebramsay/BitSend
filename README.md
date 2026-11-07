# BitSend

**Send music, movies, and TV to your media server over a private, LAN-only
BitTorrent transfer.** BitSend is a small desktop app for getting files you
already have, such as freshly ripped CDs or discs, onto Plex or Jellyfin on
another machine. It only calls a send done once the server confirms the file
landed. No SSH, Samba, or NFS to open on the server.

BitSend is a fork of [BitDVR](https://github.com/lancebramsay/BitDVR) with the
recording and renaming taken out. It keeps BitDVR's delivery design: a swarm
of exactly two machines you own. To record from OBS and name recordings for
your library, use BitDVR.

## What it does

- **Send** — your staging folder has `Music/`, `Movies/`, and `TV/` inside,
  laid out the way the server's library is. Pick an album, a movie, an episode,
  or a whole artist, show, season, or library. Each album, movie, or TV file
  gets its own torrent, and each one is checked before the first is sent: no
  rip or copy still running, not already sent. BitSend doesn't rename anything;
  matching is left to the media server. It waits until each item reaches your
  library, then stops seeding, removes the finished torrent from Deluge (the
  files stay), and moves your local copy to a `sent` folder. A failed send
  stays put and can be retried.
- **Clean** — delete archived local copies once you're happy they arrived.

## What it doesn't do

BitSend moves **your own files, between your own machines**. Deliberately:

- **No downloading.** It cannot fetch content from the internet —
  BitTorrent is used purely as a delivery mechanism between two machines.
- **No sharing.** Torrents have no trackers and are flagged private, and
  the seed daemon disables DHT, local peer discovery, and port mapping.
- **No DRM circumvention.** It copies files as they are.

See [LEGAL.md](LEGAL.md).

## Getting started

**Linux:**

```sh
git clone https://github.com/lancebramsay/BitSend.git
cd BitSend
./install.sh
```

**macOS (Homebrew):**

```sh
brew tap lancebramsay/bitsend https://github.com/lancebramsay/BitSend.git
brew trust lancebramsay/bitsend
brew install --HEAD bitsend
brew services start bitsend
bitsend
```

Then head to the **[User Guide](docs/README.md)**:

- [Getting started](docs/README.md) — requirements, installation, first run
- [Sending](docs/sending.md) — the Send and Clean tabs, sending from the
  command line, troubleshooting
- [Advanced setup](docs/advanced-setup.md) — media server libraries and the
  Deluge BitTorrent client
- [Design & architecture](docs/design.md) — why it's built this way

## Requirements at a glance

- Linux with a desktop session, or macOS (via Homebrew)
- Python 3 with Tkinter
- [libtorrent](https://libtorrent.org/) Python bindings and `jq`
- A media server such as [Plex](https://www.plex.tv/) or
  [Jellyfin](https://jellyfin.org/), with Deluge and its web UI on the same
  machine

## License

BitSend is free and open source under the
[GNU General Public License v3.0](LICENSE), as was BitDVR before it. It
comes with **no warranty** and its authors accept **no liability** for how
it is used (GPL-3.0 §§15–16).

The macOS app icon ([`docs/assets/bitsend.icns`](docs/assets/bitsend.icns))
is carried over from BitDVR and incorporates the `video-television` icon from
KDE's [Breeze icon theme](https://invent.kde.org/frameworks/breeze-icons)
(LGPL-3.0-or-later).
