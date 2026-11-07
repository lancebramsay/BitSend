# BitSend: Design & Architecture

This document explains *why* BitSend is built the way it is. BitSend is a
fork of [BitDVR](https://github.com/lancebramsay/BitDVR), which recorded with
OBS and delivered to a media server. BitSend keeps only the delivery half and
points it at music. The delivery design below is inherited.

For *how to use* BitSend, see the [User Guide](README.md) instead.

## 1. Design goals

- **Open nothing new on the media server.** No SSH, no file share. Everything
  goes through a service it may already run.
- **Stay private by construction**, not by setting (§2.2).
- **A send either provably landed or visibly didn't.** The user's local copy
  stays put until the server confirms the destination, and a failed send
  resumes rather than restarts.
- **Keep the moving parts few.** Shell scripts around `curl` and `jq`, a Tk
  GUI that streams their output, one small daemon.

## 2. Delivery: BitTorrent as a two-peer LAN courier

The unusual choice, and the one with the most rationale behind it.

### 2.1 Why not scp / rsync / SMB / NFS?

The job: move large media files from one machine to
the media-server box, unattended, reliably, with the file landing in
the right library folder under the right name. The conventional
options all fail constraint #2 (no new attack surface) before they
fail anything else:

- **scp/rsync-over-SSH** means an SSH credential for the media box
  sitting on the sending machine — a lateral-movement gift, and a
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
  next piece boundary — meaningful when the payload is a large album.
- **Delivery routing comes free.** Deluge's move-on-complete files the
  payload into the library on the *receiver* — no writable share, no agent on
  the media box, nothing but Deluge's existing RPC.
- **One transfer, any shape.** A multi-disc album with its subfolders is one
  torrent, just like a single-folder one.

### 2.2 A swarm of exactly two

Everything BitTorrent is infamous for — strangers, trackers, discovery
— is switched off at every layer, which is what makes constraint #1
hold:

| Layer | Setting | Effect |
|---|---|---|
| Torrent file | no trackers, ever | no tracker (public or private) ever sees the info-hash |
| Torrent file | private flag set | compliant clients refuse DHT/PEX/LSD for this torrent regardless of their own settings |
| Seed daemon | DHT, LSD, UPnP, NAT-PMP all disabled | the seed can't announce itself, be discovered, or punch through the router |
| Network | peer port (51414) never forwarded from WAN | even a leaked info-hash reaches nothing |

With discovery gone, the two peers must be *introduced*, and BitSend
does it from both directions at once (§2.4): the swarm is this machine
and Deluge, and can never grow.

### 2.3 One libtorrent to rule both ends

The first working prototype used `transmission-create` +
`transmission-daemon` for the seeding side. It produced a fully
established, encrypted, unchoked peer connection to Deluge — confirmed
by packet capture — over which piece data simply never flowed, for 20+
minutes, on transfers Deluge handled fine from any other source. Two
independent BitTorrent implementations, one silent interop failure on
exactly this path.

The fix removes the variable rather than debugging it: Deluge is built
on [libtorrent](https://libtorrent.org/), so BitSend builds its
torrents with libtorrent (`bitsend-create-torrent.py`) and seeds with
libtorrent (`bitsend-libtorrent-daemon.py`). Same implementation on
both ends of the wire; the entire class of cross-implementation
protocol disagreement is gone. (This lesson is written into the
scripts' own comments — it cost enough to learn.)

### 2.4 Peer introduction, from both sides

Two mechanisms, either of which suffices:

1. **Server dials seeder** — `bitsend-sync.sh` tells Deluge
   `core.connect_peer(torrent_id, <seeder-ip>, 51414)`, where the
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

### 2.5 Ordering is load-bearing

On a wired LAN, the transfer can complete in well under a second once
the peers meet — which turns "do A, then B" into a race if B was
supposed to be in place first. The ordering in `bitsend-sync.sh` is therefore
deliberate and documented in-line:

- **Label and move-destination are configured before any peer
  introduction.** Deluge's move-on-complete fires exactly once, at
  completion; if the label lands after a sub-second transfer already
  finished, the payload stays wherever it was first written. (Every
  torrent from the era when labeling ran last landed with
  `move_completed: false` — empirically, the race lost every time.)
- **The local file is archived only after delivery is confirmed.** Done
  means Deluge reports the payload at the destination and has finished moving
  it, not merely 100% downloaded: the move comes after the download, and until
  it's over the local copy is the only safety net. Only then does the script
  stop seeding, remove the torrent from Deluge, and archive to the `sent`
  folder, in that order, so the daemon never has a file pulled out
  from under an active seed. BitDVR archived first and fired and forgot;
  waiting is what lets a failure leave the source untouched and retryable.
- **A retry resumes.** Deluge refuses a torrent it already has, so the script
  saves the id Deluge returned the first time and reuses it. It's saved rather
  than derived from the torrent because which hash Deluge uses as the id of a
  hybrid v1/v2 torrent depends on its libtorrent.
- **The stall clock watches for change, not elapsed time.** Any change in
  state, progress, or location resets it, and a move in progress never counts
  as a stall, so a large file can take as long as it needs.

### 2.6 The seed daemon

A ~120-line libtorrent loop, run as a `systemd --user` service on
Linux and a Homebrew-managed launchd service on macOS. Its interface
is a watch directory: a `.torrent` file plus a `.torrent.json` sidecar
(`save_path`, optional `peers` hints) means *seed this*; deleting the
`.torrent` means *stop*. Files are seeded in libtorrent's `seed_mode`
— no hash-check on add, because the daemon is handed the same file the
torrent was just built from.

One subtlety earned its comment block: torrents are tracked by
*(filename, mtime)*, not filename alone. Re-sending something under
the same name overwrites the watch-dir file with a
different info-hash; keyed by name only, the daemon kept seeding the
stale torrent while Deluge searched forever for peers on the new hash.
An mtime change now triggers remove-and-re-add.

## 3. Music: batches of small, independent sends

A collection is hundreds of albums, which shapes three things.
`bitsend-music.sh` wraps `bitsend-sync.sh` to handle them:

- **One torrent per album.** A collection-sized torrent would make a repeat
  send collide with everything at once, and would fail as a unit.
- **Unique torrent names.** Torrents are named `Artist - Album`, because two
  artists' `Greatest Hits` would otherwise share a seed-daemon watch slot.
- **Validation is up front.** Every album is checked before the first send, so
  a half-finished rip stops the run at the start, not hours in.

## 4. Security & legal posture, as implemented

The claims in [LEGAL.md](../LEGAL.md) are only as good as the mechanisms
behind them.

**"No sharing" is architecture, not policy.** Trackerless + private flag +
DHT/LSD/UPnP/NAT-PMP disabled + no WAN port forwarding (§2.2) means there is
no code path by which a BitSend torrent reaches, or is reachable from, anyone
outside the LAN.

**"No downloading" is structural.** The seed daemon only ever adds torrents in
`seed_mode` from files it already has; nothing in the system constructs a
request for remote content.

**Secrets live in the OS keyring, full stop.** The deluge-web password is
stored in the Secret Service keyring on Linux (`secret-tool`) and the login
Keychain on macOS (`security`). It is never written to disk in plaintext and
never passed as a command-line argument (process lists are world-readable —
the macOS store path feeds `security -i` over stdin for exactly this reason).

**Nothing runs privileged, nothing new listens.** Install is per-user
(`~/.local/bin` + `systemd --user` on Linux; Homebrew prefix + `brew services`
on macOS). The one listening socket BitSend adds anywhere is the seed daemon's
LAN peer port on the *sending* machine. The media server gains no agent, no
share, no credential — it's driven entirely through Deluge's existing web RPC.

## 5. Cross-platform strategy

One codebase, explicit platform branches — `sys.platform == "darwin"` in
Python, `uname -s` in shell — chosen over abstraction layers because the
differences are few, shallow, and better kept visible:

| Concern | Linux | macOS |
|---|---|---|
| Secrets | Secret Service keyring (`secret-tool`) | login Keychain (`security`) |
| Notifications | `notify-send` | `osascript` `display notification` |
| Seed daemon | `systemd --user` unit | launchd via `brew services` |
| Install | `install.sh` → `~/.local/bin` | Homebrew formula (repo is its own tap) |
| Launcher | `.desktop` entry | generated `BitSend.app` + opt-in `bitsend-dock` |

The macOS app bundle ships a real interpreter: the Dock brands a process by
the bundle enclosing its *executable*, and Homebrew's `python3` is a stub that
re-execs into Python.framework's own app bundle, so any naive launcher shows
up as "Python". The formula instead copies the actual interpreter binary into
`BitSend.app`, retargets its libpython link to Homebrew's stable opt path,
ad-hoc re-signs it, and points `PYTHONHOME` back at the framework.

The formula also pins the interpreter trio together — `libtorrent-rasterbar`
builds its Python bindings against one specific `python@3.x`, and `python-tk`
must match it so a *single* interpreter has both tkinter and libtorrent.

## 6. Non-goals

Rejected deliberately, not deferred by accident:

- **Internet transfer.** Confinement to the LAN is the design's legal and
  security foundation, not a missing feature.
- **Downloading anything.** BitSend moves files you already have; it will
  never be a content fetcher.
- **Being a media server or scraper.** Plex and Jellyfin are good at that; BitSend's job ends when the file lands in the library.
- **Same-machine delivery.** If the server is local, a file manager does the
  job.
- **Movies and TV.** BitDVR already covers those, organizing included.
- **Renaming or tagging.** Files are sent as they are. Rippers and Picard do
  that job better.
- **A daemon-ized GUI or tray applet.** The GUI is a tool you open; the only
  resident component is the seed daemon, which exists precisely because seeding
  must outlive the GUI.
- **Windows support** — not on principle, but nothing in §5's table has a
  Windows column yet.
