#!/home/linuxbrew/.linuxbrew/opt/python@3.14/bin/python3
# Prints the magnet URI for an existing .torrent file. Used by
# bitdvr-magnet.sh instead of inlining this into a shell-interpolated
# `python3 -c` string, since torrent/file paths can contain characters
# that aren't safe to splice into Python source text.
import sys

import libtorrent as lt

if __name__ == "__main__":
    print(lt.make_magnet_uri(lt.torrent_info(sys.argv[1])))
