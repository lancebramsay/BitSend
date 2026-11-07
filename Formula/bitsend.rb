# BitSend Homebrew formula — the repo itself is the tap:
#
#   brew tap lancebramsay/bitsend https://github.com/lancebramsay/BitSend.git
#   brew trust lancebramsay/bitsend
#   brew install --HEAD bitsend
#
# Head-only until the first tagged GitHub release; when one exists, add
#   url "https://github.com/lancebramsay/BitSend/archive/refs/tags/vX.Y.Z.tar.gz"
#   sha256 "<shasum -a 256 of that tarball>"
# and plain `brew install bitsend` starts working.
class Bitsend < Formula
  desc "Send music, movies, and TV to a media server over private LAN BitTorrent"
  homepage "https://github.com/lancebramsay/BitSend"
  license "GPL-3.0-or-later"
  head "https://github.com/lancebramsay/BitSend.git", branch: "main"

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
    %w[bitsend-gui.py bitsend-create-torrent.py bitsend-libtorrent-daemon.py].each do |f|
      inreplace libexec/f, "#!/usr/bin/env python3", "#!#{python3}"
    end
    %w[bitsend-sync.sh bitsend-dock.sh].each do |f|
      inreplace libexec/f, 'PYTHON="${BITSEND_PYTHON:-python3}"',
                           "PYTHON=\"${BITSEND_PYTHON:-#{python3}}\""
    end
    # Exec wrappers rather than symlinks: the shell scripts locate their
    # sibling helpers via dirname of $BASH_SOURCE without resolving
    # symlinks, so they must actually run from libexec.
    {
      "bitsend"          => "bitsend-gui.py",
      "bitsend-sync"     => "bitsend-sync.sh",
      "bitsend-send"     => "bitsend-send.sh",
      "bitsend-dock"     => "bitsend-dock.sh",
    }.each do |name, target|
      (bin/name).write <<~SH
        #!/bin/bash
        exec "#{opt_libexec}/#{target}" "$@"
      SH
    end
    chmod 0755, Dir[bin/"bitsend*"]

    # A minimal BitSend.app so the GUI lives in the Dock like a real app —
    # the macOS counterpart of the .desktop entry install.sh writes on
    # Linux. Users symlink it into ~/Applications (see caveats) — a
    # symlink to opt_prefix survives upgrades, a copy wouldn't.
    #
    # The Dock takes the process's name/icon from the bundle enclosing
    # its EXECUTABLE, not from whatever bundle launched it — and
    # Homebrew's bin/python3.x is a stub that re-execs the interpreter
    # inside Python.framework's own Python.app, so any exec-the-python
    # launcher ends up branded as a generic "Python". Instead, a copy of
    # the real interpreter binary lives inside this bundle: it links
    # libpython by absolute path (retargeted from the version-pinned
    # Cellar path to the stable opt path so python patch upgrades don't
    # strand it, then ad-hoc re-signed since install_name_tool
    # invalidates the arm64 signature), and PYTHONHOME points it at the
    # framework it was separated from.
    return unless OS.mac?

    python_keg = Formula["python@3.14"]
    pyhome = python_keg.opt_prefix/"Frameworks/Python.framework/Versions/3.14"

    app = prefix/"BitSend.app"
    (app/"Contents/MacOS").mkpath
    app_python = app/"Contents/MacOS/BitSend-python"
    cp pyhome/"Resources/Python.app/Contents/MacOS/Python", app_python
    system "install_name_tool", "-change",
           python_keg.prefix/"Frameworks/Python.framework/Versions/3.14/Python",
           pyhome/"Python", app_python
    system "codesign", "-f", "-s", "-", app_python
    (app/"Contents/MacOS/BitSend").write <<~SH
      #!/bin/bash
      HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
      export PYTHONHOME="#{pyhome}"
      exec "$HERE/BitSend-python" "#{opt_libexec}/bitsend-gui.py" "$@"
    SH
    chmod 0755, [app/"Contents/MacOS/BitSend", app_python]
    (app/"Contents/Resources").install "docs/assets/bitsend.icns"
    (app/"Contents/Info.plist").write <<~XML
      <?xml version="1.0" encoding="UTF-8"?>
      <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
      <plist version="1.0">
      <dict>
        <key>CFBundlePackageType</key><string>APPL</string>
        <key>CFBundleName</key><string>BitSend</string>
        <key>CFBundleDisplayName</key><string>BitSend</string>
        <key>CFBundleIdentifier</key><string>com.github.lancebramsay.bitsend</string>
        <key>CFBundleExecutable</key><string>BitSend</string>
        <key>CFBundleIconFile</key><string>bitsend</string>
        <key>CFBundleShortVersionString</key><string>#{version}</string>
        <key>LSMinimumSystemVersion</key><string>11.0</string>
        <key>NSHighResolutionCapable</key><true/>
      </dict>
      </plist>
    XML
  end

  # The seed daemon (network path only) — launchd replacement for the
  # systemd --user unit install.sh sets up on Linux.
  service do
    run [opt_libexec/"bitsend-libtorrent-daemon.py"]
    keep_alive true
    log_path var/"log/bitsend-libtorrent-daemon.log"
    error_log_path var/"log/bitsend-libtorrent-daemon.log"
  end

  def caveats
    <<~EOS
      Launch the app with:  bitsend

      For a permanent launcher in the Dock (links the app bundle into
      ~/Applications and pins it — the Dock restarts briefly):
        bitsend-dock
      Unpin with `bitsend-dock --remove` or by dragging the icon off.
      Spotlight doesn't index symlinked apps — use the Dock, Finder,
      or the `bitsend` command.

      The seed daemon has to be running to send anything:
        brew services start bitsend

      Passwords are kept in the login Keychain, never on disk.
    EOS
  end

  test do
    (testpath/"payload.txt").write "bitsend formula test payload"
    system libexec/"bitsend-create-torrent.py", "--private",
           "-o", testpath/"payload.torrent", testpath/"payload.txt"
    assert_path_exists testpath/"payload.torrent"
  end
end
