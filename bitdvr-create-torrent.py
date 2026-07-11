#!/home/linuxbrew/.linuxbrew/opt/python@3.14/bin/python3
# Creates a .torrent using libtorrent directly (not transmission-create).
# Deluge on the Plex box is itself built on libtorrent, so building the
# torrent with the same library removes one implementation from the
# picture — see bitdvr-sync.sh for the fuller story on why this matters.
import argparse
import os

import libtorrent as lt


def main():
    p = argparse.ArgumentParser()
    p.add_argument("path")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("-t", "--tracker", action="append", default=[])
    p.add_argument(
        "--private", action="store_true",
        help="set the BitTorrent private flag — tells any compliant client "
             "to refuse DHT/PEX/LSD for this torrent regardless of the "
             "client's own settings",
    )
    args = p.parse_args()

    src = os.path.abspath(args.path)
    fs = lt.file_storage()
    lt.add_files(fs, src)
    t = lt.create_torrent(fs)
    for tier, url in enumerate(args.tracker):
        t.add_tracker(url, tier)
    if args.private:
        t.set_priv(True)
    t.set_creator(f"libtorrent {lt.version}")
    lt.set_piece_hashes(t, os.path.dirname(src))

    with open(args.output, "wb") as f:
        f.write(lt.bencode(t.generate()))


if __name__ == "__main__":
    main()
