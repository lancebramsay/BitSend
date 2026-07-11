# Advanced Setup: Media Servers & Deluge

This page covers the machinery *around* BitDVR: the media server that
plays your recordings, and — for the [network path](network-mode.md) —
the Deluge BitTorrent client that receives them.

## Media server libraries

BitDVR organizes recordings into the standard layout both
[Plex](https://support.plex.tv/articles/naming-and-organizing-your-movie-media-files/)
and
[Jellyfin](https://jellyfin.org/docs/general/server/media/movies/)
document for their scanners:

```
<library root>/
├── Movies/
│   └── Movie Title (2026).mkv
└── TV/
    └── Series Name/
        └── Season 01/
            └── Series Name - S01E01.mkv
```

Point two libraries at those folders:

- **Plex:** *Settings → Libraries → Add Library* → type **Movies** →
  folder `<root>/Movies`; repeat with type **TV Shows** → `<root>/TV`.
  Enable *Scan my library automatically* so new arrivals appear without
  a manual scan.
- **Jellyfin:** *Dashboard → Libraries → Add Media Library* → content
  type **Movies** → `<root>/Movies`; repeat with **Shows** →
  `<root>/TV`. Enable *real-time monitoring* for automatic pickup.

Two scanner realities worth knowing (BitDVR's Organize tab is built
around both):

- Movies match far more reliably **with a year** in the filename.
- TV scanners generally require the `SxxEyy` pattern inside a show
  folder — a loose file in `TV/` with only a title usually won't show
  up. One-off clips do better filed under Movies.

On the **local path**, that's everything — the library root here is the
same folder you set in BitDVR's Settings tab. The rest of this page is
network-path only.

## Deluge

On the network path, [Deluge](https://deluge-torrent.org/) runs on the
media-server machine and does the receiving: BitDVR hands it a torrent
over the web UI's API, and Deluge's Label plugin moves the completed
download into the right library folder.

### Install and enable the web UI

On the media-server machine (Debian/Ubuntu shown; use your distro's
equivalents):

```sh
sudo apt install deluged deluge-web
sudo systemctl enable --now deluged deluge-web
```

Browse to `http://<server>:8112`, log in (default password `deluge` —
**change it immediately**; this is the password BitDVR will ask for),
and connect the web UI to the local daemon when prompted.

Suggested daemon preferences while you're there:

- **Downloads → Download to:** a scratch folder on the same filesystem
  as your library (e.g. `/mnt/media/incoming`) so completed moves are
  instant renames, not cross-device copies.
- **Network:** BitDVR's transfers don't need DHT, LSD, PEX, or UPnP —
  its torrents are private-flagged, so Deluge won't use them for these
  transfers anyway. Disable them wholesale if this Deluge exists only
  for BitDVR.
- **Do not** forward Deluge's ports or the web UI through your router.
  Everything BitDVR does stays on the LAN.

### Labels: `movies` and `tv`

BitDVR applies a label to every transfer — the Label plugin turns that
into automatic filing:

1. *Preferences → Plugins* → enable **Label**.
2. In the sidebar, right-click **Labels → Add Label**. Create one named
   exactly `movies` and one named exactly `tv` (lowercase — these must
   match BitDVR's library dropdown).
3. Right-click the `movies` label → *Label Options* → enable **Move
   completed to** and set it to `<root>/Movies`. Do the same for `tv`
   with `<root>/TV`.

That's the whole contract: BitDVR sends a torrent with a label, the
label moves the finished file into the folder your media server is
scanning, and the recording appears in Plex/Jellyfin moments after the
transfer completes.

### Permissions

Deluge's service user must be able to **write** into the library
folders, and the media server must be able to **read** what Deluge
writes there. The simple arrangement: put both service users in a
shared group, make the library folders group-writable
(`chmod -R g+rwX`), and set Deluge's umask so new files stay
group-readable.

### Sanity check

After the [network-path setup](network-mode.md) is done end to end,
send yourself a short test recording. You should see the torrent appear
in the Deluge web UI already labeled, hit 100% within seconds on a
wired LAN, land in the right library folder, and show up in your media
server without touching anything by hand. If any of those steps
doesn't happen, the [network-path troubleshooting
list](network-mode.md#troubleshooting) covers the usual causes.
