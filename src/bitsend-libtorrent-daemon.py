#!/usr/bin/env python3
# Persistent libtorrent seed daemon for BitSend.
# Deluge on the receiving box is itself built on libtorrent; seeding with
# same library removes Transmission's independent BT implementation from
# the picture (see bitsend-sync.sh for why that mattered).
#
# Watches ~/.config/bitsend-libtorrent-daemon/watch/ for *.torrent files,
# each paired with a *.torrent.json sidecar giving save_path, and seeds
# them in seed_mode (skips hash-checking — the file is assumed complete
# since it was just used to build the torrent). Deleting a .torrent from
# the watch dir stops seeding it. DHT/LSD/UPnP/NAT-PMP are all disabled —
# this should only ever be reachable on the LAN, matching the old
# transmission-daemon's lockdown.
#
# The sidecar may also carry peers: ["ip:port", ...] (bitsend-sync.sh
# writes Deluge's own listen port there) — those are dialed OUTBOUND on
# add, and re-dialed periodically while the torrent has no connected
# peers. This is what makes sends work on segmented LANs where the
# sending machine may open connections to the server VLAN but inbound
# connections back to it are dropped: waiting to be dialed would mean
# 0 seeds forever, dialing out works in both worlds.
#
# Tracked by (filename, mtime) pairs, not filename alone — a resend using
# the same name (same watch-dir filename) overwrites the file
# with genuinely different content (a different info-hash), and keying
# purely on filename made the daemon think it already had that name
# covered, silently continuing to seed the stale torrent under that slot
# forever while Deluge went looking for peers on the new hash nobody was
# actually serving (confirmed via a real stuck-at-0%-forever transfer).
# mtime changing on an already-known filename now triggers a remove +
# re-add instead of being ignored.
import json
import os
import time

import libtorrent as lt

WATCH_DIR = os.path.expanduser("~/.config/bitsend-libtorrent-daemon/watch")
# Not BitDVR's 51413, so the two daemons can run side by side. Must match
# bitsend-sync.sh.
PORT = int(os.environ.get("BITSEND_PEER_PORT", "51414"))


def main():
    os.makedirs(WATCH_DIR, exist_ok=True)

    ses = lt.session({
        "listen_interfaces": f"0.0.0.0:{PORT}",
        "enable_dht": False,
        "enable_lsd": False,
        "enable_upnp": False,
        "enable_natpmp": False,
        "alert_mask": lt.alert_category.status | lt.alert_category.error | lt.alert_category.peer,
    })

    def dial_peers(handle, peers, name):
        for peer in peers:
            ip, _, port = peer.rpartition(":")
            try:
                handle.connect_peer((ip, int(port)))
                print(f"dialing peer {peer} for {name}", flush=True)
            except (ValueError, RuntimeError) as e:
                print(f"bad peer hint {peer!r} for {name}: {e}", flush=True)

    handles = {}  # name -> (mtime, handle, peers)
    loops = 0
    while True:
        current = {}
        for f in os.listdir(WATCH_DIR):
            if f.endswith(".torrent"):
                try:
                    current[f] = os.path.getmtime(os.path.join(WATCH_DIR, f))
                except FileNotFoundError:
                    pass  # removed between listdir() and stat()

        for name, mtime in current.items():
            existing = handles.get(name)
            if existing is not None and existing[0] == mtime:
                continue  # already seeding this exact file, unchanged

            if existing is not None:
                ses.remove_torrent(existing[1])
                print(f"replacing changed torrent: {name}", flush=True)

            sidecar = os.path.join(WATCH_DIR, name + ".json")
            if not os.path.exists(sidecar):
                continue  # sidecar not written yet, pick it up next loop
            with open(sidecar) as f:
                meta = json.load(f)
            info = lt.torrent_info(os.path.join(WATCH_DIR, name))
            h = ses.add_torrent({
                "ti": info,
                "save_path": meta["save_path"],
                "flags": lt.torrent_flags.seed_mode,
            })
            peers = meta.get("peers") or []
            handles[name] = (mtime, h, peers)
            print(f"seeding: {info.name()} from {meta['save_path']}", flush=True)
            dial_peers(h, peers, name)

        for name in set(handles) - set(current):
            _, h, _ = handles.pop(name)
            ses.remove_torrent(h)
            print(f"stopped seeding: {name}", flush=True)

        # Re-dial hinted peers for torrents that still have nobody
        # connected — the first dial can land before the other side has
        # finished checking the torrent, and a dropped connection would
        # otherwise never be retried (there's no tracker or DHT to
        # rediscover anyone here).
        loops += 1
        if loops % 30 == 0:  # every ~60s at the 2s cadence
            for name, (_, h, peers) in handles.items():
                if peers and h.status().num_peers == 0:
                    dial_peers(h, peers, name)

        for alert in ses.pop_alerts():
            print(alert, flush=True)

        time.sleep(2)


if __name__ == "__main__":
    main()
