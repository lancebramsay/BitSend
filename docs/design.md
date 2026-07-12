# BitDVR: Design & Architecture

This document explains *why* BitDVR is built the way it is — the
constraints it was designed under and the engineering decisions that
follow from them. It's written for contributors, for anyone evaluating
the project, and for the reader whose first reaction to "a DVR that
delivers over BitTorrent" is *why on earth would you do that?* (§4
answers that one directly.)

For *how to use* BitDVR, see the [User Guide](README.md) instead. For
the legal basis of personal recording, see [LEGAL.md](../LEGAL.md) —
this document covers how the design enforces those boundaries
technically (§6).

## 1. Design goals

Every decision below traces back to five constraints, in priority
order:

1. **Personal use only, enforced by design.** The legal footing
   ([LEGAL.md](../LEGAL.md)) is the VCR's: record what you're entitled
   to watch, keep it in your household. Anything that could quietly
   widen that scope — public swarms, internet-reachable services,
   downloading — must be *structurally* impossible, not just
   discouraged.
2. **No new attack surface.** Adding BitDVR to a machine, or pointing
   it at a media server, should not open a single service that wasn't
   already there, and must never put a credential somewhere less safe
   than the OS keyring.
3. **Unprivileged everywhere.** Nothing runs as root. Installation
   touches only the user's own home directory (plus one optional,
   clearly-prompted package-manager step on Linux).
4. **Compose, don't reimplement.** OBS records, libtorrent moves
   bytes, Deluge receives, Plex/Jellyfin serve. BitDVR is the ~2,700
   lines of glue that make them behave like one appliance — it
   contains no video code, no BitTorrent protocol code, and no media
   scanner.
5. **Plain, inspectable scripts.** The whole system is a Tkinter GUI,
   three shell scripts, and three small Python helpers. A user who
   wants to know what a click does can read the script it calls; every
   piece also works standalone from the command line.

## 2. System overview

BitDVR is a pipeline of two mandatory stages and one optional one:

```
              obs-websocket (:4455)
  ┌─────────┐ ◄──────────────────► ┌────────────┐
  │   OBS   │                      │ BitDVR GUI │ ── reads desktop
  │  Studio │ writes               │ (Tkinter)  │    now-playing info
  └─────────┘ ──► ~/Videos/OBS/*.mkv     │           (MPRIS / macOS
                       │                 │ drives     Now Playing)
              RECORD   │                 │
              ─────────┼─────────────────┼──────────────────────────
              ORGANIZE ▼                 ▼
                  bitdvr-organize.sh: rename + file into
                  ~/Videos/OBS/Library/{Movies,TV}/...   (staging)
                       │
              ─────────┼────────────────────────────────────────────
              SEND     ▼            (network path only)
                  bitdvr-sync.sh: archive to Library/.sent/,
                  build private torrent, configure Deluge, hand
                  off to seed daemon
                       │
        ┌──────────────┴───────────────┐
        ▼ sidecar + .torrent           ▼ JSON-RPC (:8112)
  ┌───────────────────┐          ┌───────────────┐
  │ seed daemon       │ ◄──────► │ Deluge on the │ ──► /library/
  │ (libtorrent,      │  :51413  │ media server  │     {Movies,TV}/
  │  systemd/launchd) │  2 peers │ (libtorrent)  │
  └───────────────────┘          └───────────────┘
```

On the **local path** (media server on the same machine) the third
stage disappears entirely: Organize writes straight into the live
library folder and no network component is even loaded.

The stages are deliberately independent. Organize is a pure local
rename — running it commits you to nothing. Send operates on whatever
is in staging, whenever you choose, at file, season, or series
granularity. The GUI is a front-end over the same scripts a user could
run by hand, so automation and inspection stay possible.

## 3. Recording: drive OBS, don't be OBS

Screen recording is a solved problem with a decade of tuning behind
it, so BitDVR doesn't capture anything itself — it remote-controls
[OBS Studio](https://obsproject.com/) over its built-in WebSocket
server (obs-websocket, standard since OBS 28). BitDVR gets every
encoder, capture method, and platform quirk OBS has already absorbed
(X11, Wayland, macOS ScreenCaptureKit, NVENC/VAAPI/AMF hardware
encoding), and stays a thin client: start/stop, status, and a
once-a-second program-scene screenshot used as a live preview so the
user can confirm OBS is pointed at the right display *before* a
two-hour recording, not after.

The client library (`obsws-python`, with its dependency
`websocket-client`) is vendored into the source tree rather than
pip-installed — both are small and pure Python, and bundling them
(licenses included) means installation never touches PyPI.

Configuring OBS follows the same restraint. The two settings BitDVR
requires (recording path, mkv container) are applied by a setup button
that shows the exact diff and waits for a yes; scenes and sources are
never touched. Everything else — encoder, quality, canvas — stays the
user's unless they opt into a *quality preset*, which routes
known-good recording settings for their display (resolution, 30 fps,
hardware HEVC at constant quality) through that same show-then-confirm
flow. One wrinkle is absorbed here: OBS reads its rate-control setting
only at launch, so when a preset needs to change it, BitDVR offers —
again explicitly — to quit OBS, write the setting, and relaunch.

Recording is hands-off in two independent ways, both optional:

- **Auto-stop timer** — grab the runtime of whatever the desktop's
  media player is playing and stop the recording when it elapses.
- **Auto start/stop with playback** — poll playback state every two
  seconds; recording starts when playback starts and stops when it
  pauses or ends.

Both read the desktop's now-playing metadata rather than any
service-specific API: the **MPRIS** D-Bus interface on Linux (queried
with `busctl`, which ships with systemd — no `playerctl` dependency),
and the system **Now Playing** item on macOS (read via
[`media-control`](https://github.com/ungive/media-control), a shim
around the private MediaRemote framework — see §7 for the trade-off
that implies). One title/runtime/state source covers browsers and
native players alike, and BitDVR never needs to know *what* is being
played back, only that something is.

A recording session also has to survive its weakest link: the
WebSocket connection. A brief obs-websocket drop during an active
recording is bridged by reconnecting; if OBS itself is gone, the user
is prompted to relaunch it rather than the session just dying.

## 4. Delivery: BitTorrent as a two-peer LAN courier

The unusual choice, and the one with the most rationale behind it.

### 4.1 Why not scp / rsync / SMB / NFS?

The job: move multi-gigabyte video files from the recording machine to
the media-server box, unattended, reliably, with the file landing in
the right library folder under the right name. The conventional
options all fail constraint #2 (no new attack surface) before they
fail anything else:

- **scp/rsync-over-SSH** means an SSH credential for the media box
  sitting on the recording machine — a lateral-movement gift, and a
  new trust relationship to manage.
- **SMB/NFS** means running a file server on the media box, exported
  write-access to the library, mount management, and (for NFS)
  UID-mapping headaches across machines.
- All of them then need a *second* mechanism anyway to tell the media
  stack what arrived — none of them label or sort anything.

The observation that unlocks the design: **a media-server box that
receives torrents already runs a BitTorrent client as its normal
job.** [Deluge](https://deluge-torrent.org/) accepting a torrent, a
category label, and inbound peer connections is not a new service —
it's the existing one, doing exactly what it does all day. BitTorrent
also happens to be *better* at the job than a copy command:

- **Integrity is intrinsic.** Every piece is hash-verified against the
  torrent's info-dict. A completed transfer is a byte-correct one, no
  separate checksum step.
- **Interruption is survivable.** A dropped transfer resumes at the
  next piece boundary — meaningful when the payload is a 40 GB season
  folder.
- **Delivery routing comes free.** Deluge's Label plugin maps the
  torrent's label (`movies`/`tv`) to a completed-move destination, so
  the *receiver* files the payload into the library — no writable
  share, no agent on the media box, nothing but Deluge's existing RPC.
- **One transfer, any shape.** A single episode, a `Season 02/`
  folder, or a whole series directory is one torrent either way.

### 4.2 A swarm of exactly two

Everything BitTorrent is infamous for — strangers, trackers, discovery
— is switched off at every layer, which is what makes constraint #1
hold:

| Layer | Setting | Effect |
|---|---|---|
| Torrent file | no trackers, ever | no tracker (public or private) ever sees the info-hash |
| Torrent file | private flag set | compliant clients refuse DHT/PEX/LSD for this torrent regardless of their own settings |
| Seed daemon | DHT, LSD, UPnP, NAT-PMP all disabled | the seed can't announce itself, be discovered, or punch through the router |
| Network | peer port (51413) never forwarded from WAN | even a leaked info-hash reaches nothing |

With discovery gone, the two peers must be *introduced*, and BitDVR
does it from both directions at once (§4.4): the swarm is this machine
and Deluge, and can never grow.

### 4.3 One libtorrent to rule both ends

The first working prototype used `transmission-create` +
`transmission-daemon` for the seeding side. It produced a fully
established, encrypted, unchoked peer connection to Deluge — confirmed
by packet capture — over which piece data simply never flowed, for 20+
minutes, on transfers Deluge handled fine from any other source. Two
independent BitTorrent implementations, one silent interop failure on
exactly this path.

The fix removes the variable rather than debugging it: Deluge is built
on [libtorrent](https://libtorrent.org/), so BitDVR builds its
torrents with libtorrent (`bitdvr-create-torrent.py`) and seeds with
libtorrent (`bitdvr-libtorrent-daemon.py`). Same implementation on
both ends of the wire; the entire class of cross-implementation
protocol disagreement is gone. (This lesson is written into the
scripts' own comments — it cost enough to learn.)

### 4.4 Peer introduction, from both sides

Two mechanisms, either of which suffices:

1. **Server dials seeder** — `bitdvr-sync.sh` tells Deluge
   `core.connect_peer(torrent_id, <seeder-ip>, 51413)`, where the
   seeder IP is discovered by asking the local routing table which
   source address reaches the Deluge host.
2. **Seeder dials server** — the sync script asks Deluge for its own
   listen port (`core.get_listen_port`) and writes it into the seed
   daemon's handoff as a peer hint; the daemon dials out to it on add,
   and re-dials every ~60 s while the torrent has no connected peer.

Mechanism 2 exists because of segmented home networks. On a
VLAN-segmented LAN, a common firewall policy lets client VLANs open
connections *toward* the server VLAN but drops connections initiated
the other way. There, mechanism 1 asks Deluge to dial a port it can
never reach and the transfer sits at "0 seeds" forever. Outbound
dialing from the seeder works in both worlds; on an open LAN both
mechanisms race harmlessly and whichever lands first wins. There is no
tracker or DHT to rediscover a dropped connection, so the daemon owns
the retry loop.

### 4.5 Ordering is load-bearing

On a wired LAN, the transfer can complete in well under a second once
the peers meet — which turns "do A, then B" into a race if B was
supposed to be in place first. Two orderings in `bitdvr-sync.sh` are
therefore deliberate and documented in-line:

- **Label and move-destination are configured before any peer
  introduction.** Deluge's move-on-complete fires exactly once, at
  completion; if the label lands after a sub-second transfer already
  finished, the payload stays wherever it was first written. (Every
  torrent from the era when labeling ran last landed with
  `move_completed: false` — empirically, the race lost every time.)
- **The local file is archived into `Library/.sent/` before the
  torrent is even created.** The seed daemon reads the payload from
  wherever it lives at hand-off; moving it afterward would pull the
  file out from under an active seed. Archiving first also makes the
  staging tree honest (sent things stop showing as pending) and makes
  a failed send retryable without re-archiving.

### 4.6 The seed daemon

A ~120-line libtorrent loop, run as a `systemd --user` service on
Linux and a Homebrew-managed launchd service on macOS. Its interface
is a watch directory: a `.torrent` file plus a `.torrent.json` sidecar
(`save_path`, optional `peers` hints) means *seed this*; deleting the
`.torrent` means *stop*. Files are seeded in libtorrent's `seed_mode`
— no hash-check on add, because the daemon is handed the same file the
torrent was just built from.

One subtlety earned its comment block: torrents are tracked by
*(filename, mtime)*, not filename alone. Re-sending a recording under
the same organized title overwrites the watch-dir file with a
different info-hash; keyed by name only, the daemon kept seeding the
stale torrent while Deluge searched forever for peers on the new hash.
An mtime change now triggers remove-and-re-add.

### 4.7 The escape hatch

`bitdvr-magnet.sh` (the GUI's "Copy Magnet Link") runs the same
private-torrent-plus-seed pipeline but skips Deluge entirely: the
magnet URI — with a direct `x.pe` peer hint baked in — goes to the
clipboard, to be pasted into any BitTorrent client on the LAN. Same
confinement properties, no server-side setup at all, and the file
stays in staging for a normal send later. It doubles as the fallback
when a Deluge upload fails: the sync script still hands the torrent to
the seed daemon so a manual add on the server side has a live seed
waiting.

## 5. Organizing: speak the scanner's language

Media-server scanners are pattern matchers, and the patterns are
public convention: `Movies/Title (Year).ext` for films,
`TV/Series/Season NN/Series - S01E02[ - Title].ext` for episodes.
`bitdvr-organize.sh` renames and files raw recordings
(`2026-07-10 21-03-55.mkv`) into exactly those shapes. Design points:

- **Every field is optional.** An omitted field is left out of the
  result rather than demanded; with nothing given, the original
  filename survives. The tool never blocks on metadata.
- **The library choice (`movies`/`tv`) does double duty** as the
  Deluge label applied at send time — one user decision, threaded
  through the whole pipeline, keeping the folder layout and the
  receiver's filing rule impossible to contradict.
- **Organizing and sending are separate acts.** Staging
  (`Library/Movies/`, `Library/TV/`) accumulates until the user sends
  — one episode at a time or a whole season in one torrent. In Local
  mode the same script just targets the live library directly
  (`--dest-root`) and the pipeline ends here.

## 6. Security & legal posture, as implemented

The claims in [LEGAL.md](../LEGAL.md) are only as good as the
mechanisms behind them. The mapping:

**"No sharing" is architecture, not policy.** Trackerless + private
flag + DHT/LSD/UPnP/NAT-PMP disabled + no WAN port forwarding (§4.2)
means there is no code path by which a BitDVR torrent reaches, or is
reachable from, anyone outside the LAN. Distribution isn't forbidden
by a setting someone could flip; the capability isn't present.

**"No downloading" is structural.** The seed daemon only ever adds
torrents in `seed_mode` from files it already has; nothing in the
system constructs a request for remote content.

**"No circumvention" is inherited.** BitDVR captures via OBS, which
records what the screen renders during normal playback. Nothing
decrypts, strips, or bypasses a protection measure.

**Secrets live in the OS keyring, full stop.** The OBS WebSocket and
deluge-web passwords are stored in the Secret Service keyring on Linux
(`secret-tool`) and the login Keychain on macOS (`security`). They are
never written to disk in plaintext and never passed as command-line
arguments (process lists are world-readable — the macOS store path
feeds `security -i` over stdin for exactly this reason).

**Nothing runs privileged, nothing new listens.** Install is per-user
(`~/.local/bin` + `systemd --user` on Linux; Homebrew prefix + `brew
services` on macOS). The one listening socket BitDVR adds anywhere is
the seed daemon's LAN peer port on the *recording* machine. The media
server gains no agent, no share, no credential — it's driven entirely
through Deluge's existing web RPC.

## 7. Cross-platform strategy

One codebase, explicit platform branches — `sys.platform == "darwin"`
in Python, `uname -s` in shell — chosen over abstraction layers
because the differences are few, shallow, and better kept visible:

| Concern | Linux | macOS |
|---|---|---|
| Secrets | Secret Service keyring (`secret-tool`) | login Keychain (`security`) |
| Now-playing | MPRIS via `busctl` | Now Playing via `media-control` |
| Notifications | `notify-send` | `osascript` `display notification` |
| Recordings dir | `~/Videos/OBS` | `~/Movies/OBS` (no `~/Videos` on macOS) |
| Seed daemon | `systemd --user` unit | launchd via `brew services` |
| Install | `install.sh` → `~/.local/bin` | Homebrew formula (repo is its own tap) |
| Launcher | `.desktop` entry | generated `BitDVR.app` + opt-in `bitdvr-dock` |

Three macOS-specific decisions worth recording:

- **The now-playing readout rides a private framework.** Apple locked
  MediaRemote to entitled processes in macOS 15.4; `media-control`
  reaches it through the community
  [mediaremote-adapter](https://github.com/ungive/mediaremote-adapter)
  shim. This is the one dependency in the system that a macOS update
  can break. The failure mode is contained by design: the snapshot
  function returns "no players" and the three convenience features go
  quiet; recording, organizing, and sending are untouched.
- **The GUI hides itself from its own recordings.** OBS's "hide OBS
  from capture" checkbox only covers OBS's windows — ScreenCaptureKit
  expects each app to exclude its own, via `NSWindow.sharingType =
  none` (the API password managers use). BitDVR applies it to every
  window of its process (reached from Tkinter with plain `ctypes` and
  the objc runtime — no pyobjc; note that Tk's `winfo_id` is *not* an
  NSView on macOS), re-applied on a slow tick so late-created dialogs
  are covered, and immediately for password prompts. Default on,
  toggleable in Settings.
- **The app bundle ships a real interpreter.** The Dock brands a
  process by the bundle enclosing its *executable*, and Homebrew's
  `python3` is a stub that re-execs into Python.framework's own app
  bundle — so any naive launcher shows up as "Python". The formula
  instead copies the actual interpreter binary into `BitDVR.app`,
  retargets its libpython link to Homebrew's stable opt path, ad-hoc
  re-signs it, and points `PYTHONHOME` back at the framework.

The Homebrew formula also pins the interpreter trio together —
`libtorrent-rasterbar` builds its Python bindings against one specific
`python@3.x`, and `python-tk` must match it so a *single* interpreter
has both tkinter and libtorrent.

## 8. Non-goals

Rejected deliberately, not deferred by accident:

- **Internet transfer.** Confinement to the LAN is the design's legal
  and security foundation, not a missing feature.
- **Downloading anything.** BitDVR moves files it created; it will
  never be a content fetcher.
- **DRM handling of any kind** — see §6.
- **Being a media server or scraper.** Plex and Jellyfin are good at
  that; BitDVR's job ends when the file lands, correctly named, in
  the library.
- **A daemon-ized GUI or tray applet.** The GUI is a tool you open;
  the only resident component is the seed daemon, which exists
  precisely because seeding must outlive the GUI.
- **Windows support** — not on principle, but nothing in §7's table
  has a Windows column yet, and no one has asked.
