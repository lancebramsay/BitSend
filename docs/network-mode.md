# Network Path

Use the network path when your media server runs on a **different
machine** on your LAN. BitDVR delivers finished recordings to it as a
direct, two-peer BitTorrent transfer — your recording machine seeds,
Deluge on the media-server machine downloads, and the Label plugin
files the result into the right library folder automatically.

## Why BitTorrent, and why it stays private

BitTorrent gives you a checksummed, resumable transfer to a service
(Deluge's web UI) that many media-server boxes already run — no need to
open SSH or a file share on the server just to move video files.

The swarm can never extend beyond your two machines:

- Torrents are created with **no trackers at all** and the
  **private flag** set — no tracker, public or otherwise, ever learns
  they exist, and compliant clients refuse peer discovery for them.
- The seed daemon disables **DHT, local peer discovery, UPnP, and
  NAT-PMP**.
- BitDVR tells Deluge the seeder's address **directly** over Deluge's
  API — no discovery mechanism is used at all.
- Nothing requires (or should have) a port forwarded from the
  internet.

## One-time setup

1. **On the media-server machine:** install Deluge with its web UI,
   enable the Label plugin, and create `movies` and `tv` labels that
   move completed downloads into your library folders — walked through
   step-by-step in [Advanced setup](advanced-setup.md).
2. **On the recording machine:** install the network-path dependencies
   (libtorrent Python bindings, `jq` — see
   [Getting started](README.md#requirements)) and the seed daemon
   below.
3. In BitDVR's **Settings** tab, set **Deluge Host IP** to the
   media-server machine and save. The first Send prompts once for the
   Deluge web password and keeps it in your keyring.

### The seed daemon

`bitdvr-libtorrent-daemon.py` is a small background service that seeds
whatever the Send tab hands it. It watches
`~/.config/bitdvr-libtorrent-daemon/watch/` — dropping a torrent there
starts seeding, deleting it stops. Install it as a systemd user
service at `~/.config/systemd/user/bitdvr-libtorrent-daemon.service`:

```ini
[Unit]
Description=BitDVR libtorrent seed daemon

[Service]
ExecStart=%h/.local/bin/bitdvr-libtorrent-daemon.py
Restart=on-failure

[Install]
WantedBy=default.target
```

```sh
systemctl --user daemon-reload
systemctl --user enable --now bitdvr-libtorrent-daemon
```

It listens on port `51413` for LAN peers. Check on it any time with
`journalctl --user -u bitdvr-libtorrent-daemon`.

## The Send tab

The Send tab shows your staging library (`~/Videos/OBS/Library/`) as a
tree — flat files at the root, series grouped as Series → Season →
file. Select any level and the label under the tree spells out exactly
what will happen before you click anything:

- **A single file** — the everyday case. Sent alone; Deluge's label
  files it under `Movies/` or `TV/` on the server.
- **A whole season** — one torrent containing every episode in the
  folder, delivered next to any seasons already on the server. Batching
  a season is one transfer instead of ten.
- **A whole series** — supported, but treat it as a one-time full drop:
  the GUI warns before doing it, because sending the *same* series
  folder twice nests a duplicate copy inside the first rather than
  merging. New episodes for an already-sent series should go
  individually or by season instead — those merge cleanly every time.

**Send to Plex** runs the whole pipeline: the item is archived to
`Library/.sent/`, a private trackerless torrent is built, the seed
daemon starts seeding, and Deluge on the server is handed the torrent,
the label, and the seeder's address. On a wired LAN the transfer
typically completes in seconds; watch it land in the Deluge web UI at
`http://<server>:8112`.

**Copy Magnet Link** is the ad hoc alternative: same private torrent
and seeding, but nothing is uploaded to Deluge and nothing is archived
— the magnet (with a direct peer hint baked in) goes to your clipboard,
to paste into any BitTorrent client on your LAN. The source file stays
in staging and can still be sent normally later.

## The Clean tab

Everything sent successfully lives on under `Library/.sent/` on the
recording machine — the network path never deletes your local copy for
you. The Clean tab shows that archive in the same tree view; selecting
a file, season, or series and clicking **Remove** deletes the local
copy and stops seeding it if the daemon still is. The media server's
copy is never touched.

`.sent/` is also your retry path: if a send fails partway, the file is
already safely archived there, and sending it again from that path
picks up where things left off.

## Troubleshooting

- **Transfer stuck at 0%** — check the seed daemon is running
  (`systemctl --user status bitdvr-libtorrent-daemon`) and that the two
  machines can reach each other (`ping`, and port `51413` open in the
  recording machine's firewall for LAN traffic).
- **File landed but in the wrong folder** — the Deluge labels do the
  filing; verify the `movies`/`tv` label configuration in
  [Advanced setup](advanced-setup.md#deluge).
- **"Move Completed" shows unchecked in Deluge afterward** — normal.
  It's a pending-action flag that clears once the move has already
  happened; the file is where the label put it.
- **Wrong Deluge password saved** — Settings tab → *Change Password*.

Next: [Advanced setup](advanced-setup.md).
