# BitDVR Homebrew formula — the repo itself is the tap:
#
#   brew tap lancebramsay/bitdvr https://github.com/lancebramsay/BitDVR.git
#   brew install --HEAD bitdvr
#
# Head-only until the first tagged GitHub release; when one exists, add
#   url "https://github.com/lancebramsay/BitDVR/archive/refs/tags/vX.Y.Z.tar.gz"
#   sha256 "<shasum -a 256 of that tarball>"
# and plain `brew install bitdvr` starts working.
class Bitdvr < Formula
  desc "Personal DVR: drive OBS, organize recordings, deliver over LAN BitTorrent"
  homepage "https://github.com/lancebramsay/BitDVR"
  license "GPL-3.0-or-later"
  head "https://github.com/lancebramsay/BitDVR.git", branch: "main"

  depends_on "jq"
  # libtorrent-rasterbar builds its Python bindings against Homebrew's
  # current python@3.x — the python-tk/python version here must match it
  # so ONE interpreter has both tkinter (GUI) and libtorrent (torrent
  # scripts). Bump all three together when homebrew-core moves on.
  depends_on "libtorrent-rasterbar"
  depends_on "python-tk@3.14"
  depends_on "python@3.14"

  def install
    python3 = Formula["python@3.14"].opt_bin/"python3.14"

    libexec.install Dir["src/*"]

    # Pin the interpreter the same way install.sh does on Linux: shebangs
    # for the Python entry points, $PYTHON default for the shell scripts
    # that invoke the torrent helpers.
    %w[bitdvr-gui.py bitdvr-create-torrent.py bitdvr-magnet-uri.py
       bitdvr-libtorrent-daemon.py].each do |f|
      inreplace libexec/f, "#!/usr/bin/env python3", "#!#{python3}"
    end
    %w[bitdvr-sync.sh bitdvr-magnet.sh].each do |f|
      inreplace libexec/f, 'PYTHON="${BITDVR_PYTHON:-python3}"',
                           "PYTHON=\"${BITDVR_PYTHON:-#{python3}}\""
    end

    # Exec wrappers rather than symlinks: the shell scripts locate their
    # sibling helpers via dirname of $BASH_SOURCE without resolving
    # symlinks, so they must actually run from libexec.
    {
      "bitdvr"          => "bitdvr-gui.py",
      "bitdvr-organize" => "bitdvr-organize.sh",
      "bitdvr-sync"     => "bitdvr-sync.sh",
      "bitdvr-magnet"   => "bitdvr-magnet.sh",
    }.each do |name, target|
      (bin/name).write <<~SH
        #!/bin/bash
        exec "#{opt_libexec}/#{target}" "$@"
      SH
    end
    chmod 0755, Dir[bin/"bitdvr*"]
  end

  # The seed daemon (network path only) — launchd replacement for the
  # systemd --user unit install.sh sets up on Linux.
  service do
    run [opt_libexec/"bitdvr-libtorrent-daemon.py"]
    keep_alive true
    log_path var/"log/bitdvr-libtorrent-daemon.log"
    error_log_path var/"log/bitdvr-libtorrent-daemon.log"
  end

  def caveats
    <<~EOS
      Launch the app with:  bitdvr

      BitDVR drives OBS Studio 28+, installed separately:
        brew install --cask obs
      In OBS: enable Tools -> WebSocket Server Settings (set a password),
      set the recording output folder to ~/Movies/OBS, and grant OBS
      Screen Recording permission when macOS asks.

      Network path only (sending to a media server on another machine) —
      start the seed daemon:
        brew services start bitdvr

      Passwords are kept in the login Keychain, never on disk.
    EOS
  end

  test do
    (testpath/"payload.txt").write "bitdvr formula test payload"
    system libexec/"bitdvr-create-torrent.py", "--private",
           "-o", testpath/"payload.torrent", testpath/"payload.txt"
    assert_match "magnet:?xt=urn:btih:",
                 shell_output("#{libexec}/bitdvr-magnet-uri.py #{testpath}/payload.torrent")
  end
end
