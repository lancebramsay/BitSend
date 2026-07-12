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
- **Scheduled recordings** — tick the *Schedule recordings* box to
  reveal the panel (it stays out of the way otherwise), then queue a
  start time, a runtime (type it, or *Grab runtime* from a queued-up
  player), and an optional title that prefills the Organize tab
  afterward. At the start time BitDVR
  begins recording and the runtime stops it — launching OBS itself if
  it isn't running, and holding the machine awake (via `caffeinate` on
  macOS, `systemd-inhibit` on Linux) while anything is scheduled. Two
  honest limits: BitDVR records the screen, so *the content must be
  playing on screen at that time* — a schedule can press Record for
  you, it can't tune a channel — and the BitDVR window must still be
  open. A schedule that comes due while BitDVR is closed (or more
  than 5 minutes late, e.g. the machine was asleep) is declared
  missed, with a note in the log; up to 5 minutes late, it starts
  late instead. Schedules survive restarting BitDVR.
- **Live preview** — a small once-a-second snapshot of OBS's program
  scene, so you can confirm OBS is pointed at the right screen before
  committing to a two-hour recording.
- **Invisible to the recording** *(macOS)* — BitDVR marks its own
  windows as excluded from screen capture (the same macOS facility
  password managers use), so the control panel never shows up in a
  recording even when it's sitting on the captured screen. On by
  default; toggle it in Settings. Linux has no per-window exclusion —
  there, keep BitDVR on a different monitor or workspace than the one
  OBS captures.
- **Connection recovery** — if OBS isn't running, or its WebSocket
  connection drops mid-session, BitDVR prompts you to launch/restart
  OBS and retry rather than just failing. A brief drop during an active
  recording is bridged automatically; if you cancel the reconnect
  prompt instead, BitDVR gives up control of that recording (OBS may
  still be recording on its own — check it directly).

When a recording stops — manually, by timer, or by playback ending —
BitDVR switches to the Organize tab with the new file pre-loaded.

### Setting up the OBS side: scene, output, video

BitDVR records whatever OBS is configured to record — it drives OBS's
active scene but never edits it, so the capture itself is a one-time
OBS setup. Any OBS tutorial on local recording applies as-is; the
short version, plus the one genuine gotcha, below.

**Scene & source.** One scene with a single display-capture source is
all a DVR needs: *macOS Screen Capture* (method: Display) on macOS,
*Screen Capture (PipeWire)* on Wayland, or *Display Capture (XSHM)* on
X11. Capture the display you play media on — BitDVR's Record-tab
preview shows you exactly what OBS sees, so use it to confirm the
right screen before a long recording. Window or application capture
can work too, but display capture plus fullscreen playback is the
combination that never breaks when a player redraws its window or
switches to fullscreen mid-session.

**Output settings** (*Settings → Output → Recording*): set the
recording path to `~/Videos/OBS` (macOS: `~/Movies/OBS`) so BitDVR
finds the files; record to Matroska (`.mkv` — it survives crashes and
interruptions, and can be remuxed to `.mp4` afterward if something
needs that); and use your GPU's hardware encoder (Apple VT on macOS,
NVENC/VAAPI/AMF on Linux) with quality-based rate control rather than
a fixed low bitrate.

**Video settings** (*Settings → Video*): set the **base (canvas)
resolution to your display's native resolution** so the capture fills
the canvas exactly, and the output (scaled) resolution to match it —
that's the whole story on a 16:9 monitor.

**The 16:10 MacBook wrinkle.** MacBook built-in displays are taller
than 16:9 (this guide's test machine is 2560×1664, ratio ≈ 1.54 vs
16:9's 1.78), so a full-screen recording doesn't naturally match a TV
shape. Two sane setups:

1. **Record the display as-is** (base = output = native). Fullscreen
   16:9 video arrives with thin black bars baked in above and below
   (that's what the display showed), and the file has a nonstandard
   resolution — but every player and TV letterboxes/scales it
   correctly, so for personal viewing this is the zero-fuss option.
2. **Produce true 16:9** (a clean 1920×1080 with no baked bars): set
   base *and* output to 1920×1080, then crop the capture source to the
   16:9 band of the screen where fullscreen video actually renders.
   Right-click the source → *Edit Transform*, crop top and bottom by
   `(display height − display width × 9⁄16) ÷ 2` — for 2560×1664
   that's (1664 − 1440) ÷ 2 = **112 px each** — then *Fit to screen*.
   The cropped region is exactly 16:9 and fills the canvas edge to
   edge.

Either way, confirm with the Record-tab preview and a short test
recording before trusting it with a movie.

**If the preview or recording shows a frozen, stale image** (macOS):
macOS periodically re-confirms screen-recording permission, and OBS's
capture freezes on its last frame until you do. Check *System Settings
→ Privacy & Security → Screen & System Audio Recording*, make sure OBS
is enabled (re-toggle it if it already looks enabled), then quit and
relaunch OBS.

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
