# Basics: Recording & Organizing

These two steps are the heart of BitDVR and are identical on the
[local](local-mode.md) and [network](network-mode.md) paths. A shared
log pane below the tabs shows what every action is doing, and each run
is also written to a timestamped file under `~/.local/share/bitdvr/logs/`.

## The Record tab

BitDVR controls OBS over its built-in WebSocket server — OBS does the
actual capturing, BitDVR does the driving.

- **Start / Stop Recording** — always available, whatever else is
  configured.
- **Auto-stop timer** — enter minutes/seconds and the recording stops
  itself. Manual Stop still works, so an over-generous estimate costs
  nothing.
- **Grab runtime** — one click fills the timer from whatever your media
  player is currently playing (or has paused), read from the desktop's
  now-playing info: the MPRIS interface on Linux, the system now-playing
  item (what Control Center shows) on macOS. Works with most media
  players and browsers on both platforms; on macOS the player has to
  publish to Now Playing (browsers, Music, Spotify, and TV do — QuickTime
  Player doesn't), and the readout goes through a community shim around a
  private Apple framework, so a macOS update can knock it out until the
  shim catches up (everything else keeps working).
- **Auto start/stop recording with playback** — a toggle that starts
  recording when playback starts and stops it when playback pauses or
  stops. Set the timer or just use this, put the movie on, and walk
  away. Reads the same now-playing source as Grab runtime.
- **Live preview** — a small once-a-second snapshot of OBS's program
  scene, so you can confirm OBS is pointed at the right screen before
  committing to a two-hour recording.
- **Connection recovery** — if OBS isn't running, or its WebSocket
  connection drops mid-session, BitDVR prompts you to launch/restart
  OBS and retry rather than just failing. A brief drop during an active
  recording is bridged automatically; if you cancel the reconnect
  prompt instead, BitDVR gives up control of that recording (OBS may
  still be recording on its own — check it directly).

When a recording stops — manually, by timer, or by playback ending —
BitDVR switches to the Organize tab with the new file pre-loaded.

### Recording quality is OBS's job

BitDVR records whatever OBS is configured to record. Sensible starting
points: record to Matroska (`.mkv`, survives interruptions), use your
GPU's hardware encoder (NVENC/VAAPI/AMF) with quality-based rate
control, and capture the display or window your player uses. Any OBS
tutorial on local recording applies as-is.

## The Organize tab

Organizing renames and files a raw recording (`2026-07-10 21-03-55.mkv`)
into the folder-and-filename conventions media-server scanners key on.
It is purely local — nothing touches the network.

The file picker defaults to your newest recording. Then:

- **Library** *(required)* — `movies` or `tv`. Chooses the top-level
  folder, and on the network path it's also the label Deluge files the
  transfer under.
- **Title / Year** — for a standalone item: `Movie Title (2026).mkv`.
  A year makes movie matching dramatically more reliable; it applies
  under the movies library only.
- **Grab title** — one click pulls the currently-playing title from
  your media player (the same now-playing source as Grab runtime), so
  you don't retype it.
- **Part of a series** — reveals Series / Season / Episode fields,
  available under *either* library (a movie trilogy can be a "series"
  too). This produces the `Series Name/Season 01/Series Name -
  S01E02.mkv` layout, and the `S01E02` piece is exactly what TV
  scanners recognize episodes by.
- **Delete** — permanently discards a bad take (with a confirmation
  showing the full path first).

Every field except Library is optional — blanks are simply left out of
the name rather than being errors. Repeated organizes nest correctly:
episode 2 of a season lands next to episode 1 in the same
`Season NN/` folder.

The resulting layout:

```
Movies/
├── Movie Title (2026).mkv
└── My Trilogy/
    └── Season 01/
        └── My Trilogy - S01E01 - Part One.mkv
TV/
└── Series Name/
    ├── Season 01/
    │   ├── Series Name - S01E01.mkv
    │   └── Series Name - S01E02.mkv
    └── Season 02/
        └── Series Name - S02E01.mkv
```

> **Tip:** a loose TV file with no series/season/episode won't be
> recognized by most TV library scanners even though it's on disk in
> the right place. For a one-off clip that isn't really an episode of
> anything, organize it under **movies** instead — movie scanners are
> far more forgiving of a bare title.

## Where organized files go

- **Network path:** into a local staging library at
  `~/Videos/OBS/Library/` (macOS: `~/Movies/OBS/Library/`), waiting for
  the [Send tab](network-mode.md).
- **Local mode:** directly into your media library folder — that's the
  whole point. See [Local path](local-mode.md).

Next: [Local path](local-mode.md) or [Network path](network-mode.md).
