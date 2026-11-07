# Sending

BitSend delivers music, movies, and TV to your media server as a direct, two-peer BitTorrent
transfer. This machine seeds, Deluge on the media server downloads and moves
the result into the right library folder, and BitSend waits for that move to
finish before calling the send done.

## Why BitTorrent, and why it stays private

BitTorrent gives you a checksummed, resumable transfer to a service
(Deluge's web UI) that many media-server boxes already run — no need to
open SSH or a file share on the server just to move files.

The swarm can never extend beyond your two machines:

- Torrents are created with **no trackers at all** and the
  **private flag** set — no tracker, public or otherwise, ever learns
  they exist, and compliant clients refuse peer discovery for them.
- The seed daemon disables **DHT, local peer discovery, UPnP, and
  NAT-PMP**.
- BitSend tells Deluge the seeder's address **directly** over Deluge's
  API — no discovery mechanism is used at all.
- Nothing requires (or should have) a port forwarded from the
  internet.

## One-time setup

1. **On the media-server machine:** install Deluge with its web UI —
   walked through step-by-step in [Advanced setup](advanced-setup.md).
2. **On this machine:** install BitSend, which also sets up the seed daemon
   below.
3. In BitSend's **Settings** tab, set **Deluge Host IP** to the media-server
   machine and **Media library root (on Deluge host)** to the folder there
   containing `Music/`, `Movies/`, and `TV/`, then save. The first send
   prompts once for the Deluge web password and keeps it in your keyring.

### The seed daemon

`bitsend-libtorrent-daemon.py` is a small background service that seeds
whatever a send hands it. It watches
`~/.config/bitsend-libtorrent-daemon/watch/` — dropping a torrent there starts
seeding, deleting it stops. The installer sets it up as a `systemd --user`
service; on **macOS** it's a launchd service, `brew services start bitsend`.

It listens on port `51414` for LAN peers. That's deliberately not BitDVR's
`51413`, so both can run on one machine (or see
[`--use-bitdvr-daemon`](README.md#linux) to share BitDVR's). To change it, set
`BITSEND_PEER_PORT` both in the service and wherever you run the send scripts.
Check on it with `journalctl --user -u bitsend-libtorrent-daemon` (macOS:
`$(brew --prefix)/var/log/bitsend-libtorrent-daemon.log`).

## Getting files ready

BitSend reads three folders inside your **Staging folder** (default
`~/Rips/staging`), laid out the way the library on the server is:

```
staging/
├── Music/
│   └── Artist/
│       └── Album/             one torrent per album
├── Movies/
│   ├── Movie (Year)/          one torrent per folder...
│   └── Movie (Year).mkv       ...or per loose file
└── TV/
    └── Show/
        └── Season 01/
            └── Show - S01E01.mkv   one torrent per file
```

Each item lands in the same place under the library root on the server:
an album in `Music/Artist/`, a movie in `Movies/`, an episode in
`TV/Show/Season 01/`.

BitSend doesn't rename, retag, or check names; matching is up to your media
server, so name things the way it expects. Music servers group by tags,
particularly Album Artist, not folders, so tag albums before sending. Movies
match best as `Title (Year)`, and episodes need an `S01E02`-style tag in the
filename.

TV goes file by file because a show or season usually already exists on the
server. A send can only add a new name at its destination, since Deluge nests
a second copy of a folder inside the first instead of merging. Sending the
episode file into the season folder works whether or not that folder exists.

## The Send tab

The Send tab shows the staging folder's `Music`, `Movies`, and `TV` with
their contents. Select one item, a group (an artist, a show, a season), or a
whole library, and the label under the tree spells out what will happen.
Every album, movie, or TV file is its own torrent, and everything is checked
before the first one is sent.

**Send to Server** runs the whole pipeline: a private trackerless torrent is
built, Deluge is handed the torrent and its destination, the seed daemon
starts seeding, and the log pane follows along until Deluge reports the item
in its library folder. Then BitSend stops seeding, removes the finished
torrent from Deluge (the files stay), and moves your local copy to the same
place in the `sent` folder beside your staging folder. Folders a send leaves
empty in staging are removed.

If a send fails, the item stays where it was and the seed keeps running, so
it can still finish on its own. Send it again to resume: a torrent Deluge
already has is picked back up, not added twice. A send gives up after 10
minutes with no change at all (`BITSEND_STALL_TIMEOUT`, in seconds), not after
a fixed time, so a large movie takes as long as it needs.

## Sending from the command line

`bitsend-send.sh` sends without opening the GUI. Pick the library with
`--library music`, `movies`, or `tv`:

```sh
export BITSEND_PLEX_HOST=192.168.1.20
export BITSEND_REMOTE_ROOT=/mnt/media

bitsend-send.sh --library movies --dry-run          # check everything, send nothing
bitsend-send.sh --library music                     # everything in staging/Music
bitsend-send.sh --library tv ~/Rips/staging/TV/Show # one show, season, or episode
bitsend-send.sh --library music --staging ~/ToSend  # another staging folder
```

(It lives in `~/.local/bin`. On macOS the command is `bitsend-send`.)

Before anything is sent, there must be no temporary files from a rip or copy
still running (`x01.wav`-style files from Asunder, or `.part`, `.partial`, or
`.tmp` files), and nothing may already be in the `sent` folder. Empty folders
are skipped. Those checks can't tell if tracks or episodes are missing from a
partial rip. **A run with no path named sends everything in that library**,
so make sure it's all been reviewed first. The run stops at the first failed
item. Rerun it to carry on: items already sent have moved out of the way.

## The Clean tab

Everything sent successfully lives on in the `sent` folder — BitSend never
deletes your local copy for you. The Clean tab lists it the same way the Send
tab lists staging; **Remove** deletes whatever is selected, from one episode
to a whole library. The media server's copy is never touched. Movies take a
lot of space, so this is where to reclaim it once you've checked they play.

The `sent` folder is also what stops a repeat send, which Deluge would nest as
a duplicate. Once you remove something here, BitSend will let you send it
again.

## Troubleshooting

- **Transfer stuck at 0%** — check the seed daemon is running
  (`systemctl --user status bitsend-libtorrent-daemon`; macOS:
  `brew services info bitsend`) and that the two machines can reach each
  other. The connection works in either direction: the server can reach this
  machine on port `51414`, or the daemon can dial out to Deluge's own peer
  port. On segmented/VLAN'd networks, make sure at least one of those is
  allowed through the firewall. Reaching deluge-web on port `8112` doesn't
  prove either.
- **"couldn't apply label"** — harmless. BitSend sets the destination itself,
  and the label only sorts Deluge's own list. Create the label to silence it
  ([Advanced setup](advanced-setup.md#labels)).
- **Landed but the send timed out** — BitSend confirms delivery by comparing
  Deluge's reported location to the destination it asked for. Check the path
  in deluge-web against **Media library root** in Settings.
- **A file won't play or won't appear on the server** — Deluge has to write
  there and the media server has to read what it wrote. See
  [Permissions](advanced-setup.md#permissions).
- **Wrong Deluge password saved** — Settings tab → *Change Deluge-web
  Password*.

Next: [Advanced setup](advanced-setup.md).
