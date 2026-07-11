# Local Path

Use local mode when BitDVR runs **on the same machine as your media
server** — recording box and Plex/Jellyfin box are one and the same.
There is nothing to transfer, so BitDVR skips the network machinery
entirely: no BitTorrent, no Deluge, no seed daemon, and none of the
network-path dependencies (libtorrent, `jq`) are needed.

## Enabling it

1. Open the **Settings** tab.
2. Tick **Local mode**.
3. Set the **library root** — the folder your media server's libraries
   point at (default `/mnt/plex-media`). BitDVR creates `Movies/` and
   `TV/` underneath it as needed.
4. Click **Save**.

The **Send** and **Clean** tabs disappear (the remaining tabs
renumber), because there's nothing to send and no staging area to
clean. Untick the box and save to get them back — nothing is lost by
switching modes.

## What changes

In local mode, [organizing](basics.md#the-organize-tab) files the
recording **directly into the library root** instead of the local
staging library:

```
/mnt/plex-media/            ← your library root
├── Movies/
│   └── Movie Title (2026).mkv
└── TV/
    └── Series Name/
        └── Season 01/
            └── Series Name - S01E01.mkv
```

Recording and organizing otherwise behave exactly as described in
[Basics](basics.md), including series/season nesting across repeated
organizes.

## Checklist

- **Write access:** the user running BitDVR needs write permission on
  the library root. If your media server owns the folder, adding your
  user to its group (`sudo usermod -aG plex YOU`, then re-log-in) and
  group-writable permissions is the usual fix.
- **Library scanning:** point your Plex/Jellyfin **Movies** library at
  `<root>/Movies` and your **TV** library at `<root>/TV` — see
  [Advanced setup](advanced-setup.md#media-server-libraries). Both
  servers can watch folders and pick new files up automatically.
- **Disk:** raw OBS recordings land in `~/Videos/OBS/` first;
  organizing *moves* (not copies) the file into the library, so no
  duplicate is left behind.

That's the whole path. If your media server later moves to its own
box, untick Local mode and continue with the
[Network path](network-mode.md).
