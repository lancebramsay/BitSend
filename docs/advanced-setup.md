# Advanced Setup: Media Servers & Deluge

This page covers the machinery *around* BitSend: the media server that plays
your music, and the Deluge BitTorrent client that receives them.

## Media server libraries

BitSend sends albums into the standard layout both
Plex and Jellyfin scan for:

```
<library root>/
└── Music/
    └── Artist/
        └── Album/
            └── 01 - Track.flac
```

Point a music library at that folder:

- **Plex:** *Settings → Libraries → Add Library* → type **Music** → folder
  `<root>/Music`. Enable *Scan my library automatically* so new arrivals appear
  without a manual scan.
- **Jellyfin:** *Dashboard → Libraries → Add Media Library* → content type
  **Music** → `<root>/Music`. Enable *real-time monitoring*.

Music libraries group by embedded tags, particularly **Album Artist**, so
inconsistent values split one album into several. Tag before sending.

## Deluge

[Deluge](https://deluge-torrent.org/) runs on the media-server machine and
does the receiving: BitSend hands it a torrent and a destination over the web
UI's API, and Deluge moves the completed download into your library folder.

### Install and enable the web UI

On the media-server machine (Debian/Ubuntu shown; use your distro's
equivalents):

```sh
sudo apt install deluged deluge-web
sudo systemctl enable --now deluged deluge-web
```

Browse to `http://<server>:8112`, log in (default password `deluge` —
**change it immediately**; this is the password BitSend will ask for),
and connect the web UI to the local daemon when prompted.

Suggested daemon preferences while you're there:

- **Downloads → Download to:** a scratch folder on the same filesystem
  as your library (e.g. `/mnt/media/incoming`) so completed moves are
  instant renames, not cross-device copies.
- **Network:** BitSend's transfers don't need DHT, LSD, PEX, or UPnP —
  its torrents are private-flagged, so Deluge won't use them for these
  transfers anyway. Disable them wholesale if this Deluge exists only
  for BitSend.
- **Do not** forward Deluge's ports or the web UI through your router.
  Everything BitSend does stays on the LAN.

### Labels

Optional. BitSend sets each transfer's destination itself, so nothing here
affects where files land. Labels just sort Deluge's own list, and BitSend
removes finished torrents from it anyway. If you want one:

1. *Preferences → Plugins* → enable **Label**.
2. In the sidebar, right-click **Labels → Add Label**. Create `music`
   (lowercase).

Without it, sends log a harmless warning.

### Permissions

Deluge's service user must be able to **write** into the library
folders, and the media server must be able to **read** what Deluge
writes there. The simple arrangement: put both service users in a
shared group, make the library folders group-writable
(`chmod -R g+rwX`), and set Deluge's umask so new files stay
group-readable.

### Sanity check

Send one album first. You should see the torrent appear in the Deluge
web UI, hit 100% within seconds on a wired LAN, land in the right library
folder, and disappear from Deluge's list once BitSend finishes. If not, the
[troubleshooting list](sending.md#troubleshooting) covers the usual causes.
