#!/usr/bin/env python3
# BitDVR — GUI wrapper around bitdvr-organize.sh and bitdvr-sync.sh, meant
# to be installed to ~/.local/bin alongside copies of those scripts (see
# bitdvr-setup.sh) and pinned to the KDE taskbar as its own app. A DVR for
# OBS recordings that delivers over a private BitTorrent swarm instead of
# a network share.
#
# Four tabs, matching the workflow:
#   Record — controls OBS directly over obs-websocket (v5, built into
#     OBS 28+). Start/Stop are both always available; an optional timer
#     auto-stops after a set duration (the "how long is the movie"
#     hybrid) but never blocks a manual Stop. On stop, the just-recorded
#     file is handed straight to the Organize tab.
#   Organize — purely local. Renames/moves a raw recording from
#     ~/Videos/OBS into ~/Videos/OBS/Library/Movies/ or
#     Library/TV/<Series>/Season NN/. No network, nothing sent yet.
#   Send — browse the Library staging tree and send a single movie/episode
#     file, or bundle a whole Season/Series folder into one torrent.
#     Deluge's "move completed" is a directory move, so bundling is only
#     collision-safe when targeted one level above what's inside the
#     torrent (see bitdvr-sync.sh) — the Send tab shows exactly what will
#     happen, including a warning for whole-series sends. "Copy Magnet
#     Link" is a separate action (bitdvr-magnet.sh) that creates the
#     torrent and starts seeding it the same way, but skips deluge-web
#     entirely and doesn't archive the source file — an ad hoc way to
#     pull a recording into any Deluge/qBittorrent/Transmission client on
#     the LAN instead of the automated Plex send.
#   Settings — OBS WebSocket host/port, the Deluge host, and the library
#     roots, persisted to ~/.config/bitdvr/config.json (not the keyring —
#     these aren't secret). Also holds "Change Password" actions for the
#     two keyring secrets below, since the normal prompt flow only fires
#     when nothing is stored yet. The Deluge host is threaded through to
#     bitdvr-sync.sh via the BITDVR_PLEX_HOST env var rather than a CLI
#     flag, matching the env override the script accepts standalone.
#
# Organize/Send run their shell script as a subprocess and stream its
# stdout/stderr into a shared scrolling log pane live, while also writing
# each run to a timestamped file under ~/.local/share/bitdvr/logs/. The
# deluge-web keyring password is checked/collected *before* launching
# bitdvr-sync.sh, since a GUI child process has no TTY for the script's
# own `read -s` prompt. Record talks to OBS in-process instead (there's
# no separate script to shell out to — obs-websocket is a real WebSocket
# protocol, not a one-shot RPC call curl can drive the way deluge-web's
# JSON endpoint can), so its keyring password is fetched the same way but
# used directly by the obsws_python client.

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

SCRIPT_DIR = Path(__file__).resolve().parent

# obsws_python (and its own dependency, websocket-client) are vendored
# under vendor/ rather than pip-installed — both are pure Python and
# small, so bundling them (licenses included alongside the source)
# removes a PyPI round trip from setup entirely. The installer deploys
# vendor/ alongside this script, so this resolves whether running from
# the repo checkout or from ~/.local/bin.
sys.path.insert(0, str(SCRIPT_DIR / "vendor"))
try:
    import obsws_python as obs
except ImportError:
    obs = None

ORGANIZE_SCRIPT = SCRIPT_DIR / "bitdvr-organize.sh"
SYNC_SCRIPT = SCRIPT_DIR / "bitdvr-sync.sh"
MAGNET_SCRIPT = SCRIPT_DIR / "bitdvr-magnet.sh"
IS_MACOS = sys.platform == "darwin"
# ~/Movies is where OBS defaults its output on macOS (there is no
# ~/Videos there); the OBS/ subfolder keeps raw recordings separated the
# same way as on Linux. Must stay in sync with the same branch in
# bitdvr-organize.sh / bitdvr-sync.sh / bitdvr-magnet.sh.
RECORDINGS_DIR = Path.home() / ("Movies" if IS_MACOS else "Videos") / "OBS"
LIBRARY_DIR = RECORDINGS_DIR / "Library"
SENT_DIR = LIBRARY_DIR / ".sent"
SEED_WATCH_DIR = Path.home() / ".config" / "bitdvr-libtorrent-daemon" / "watch"
LOG_DIR = Path.home() / ".local" / "share" / "bitdvr" / "logs"
CONFIG_DIR = Path.home() / ".config" / "bitdvr"
CONFIG_FILE = CONFIG_DIR / "config.json"
SECRET_TOOL = "secret-tool"
# macOS only: the now-playing reader behind Grab runtime/title and auto
# start/stop. The Homebrew formula rewrites the default to media-control's
# opt path — Dock-launched apps don't have Homebrew's bin on PATH.
MEDIA_CONTROL = os.environ.get("BITDVR_MEDIA_CONTROL", "media-control")
SECRET_SERVICE = "bitdvr-deluge-web"
OBS_SECRET_SERVICE = "bitdvr-obs-websocket"
DEFAULT_CONFIG = {
    "obs_host": "127.0.0.1",
    "obs_port": 4455,
    "plex_host": "",
    "remote_library_root": "",
    "local_mode": False,
    "local_library_root": "",
    "hide_from_capture": True,  # macOS only; ignored on Linux
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.is_file():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            if isinstance(data, dict):
                cfg.update({k: data[k] for k in DEFAULT_CONFIG if k in data})
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def secret_lookup(service, account):
    """Returns the stored secret string, or None if absent/unreadable.
    Linux: the login keyring via secret-tool. macOS: the login Keychain
    via /usr/bin/security (find-generic-password -w prints the secret
    with a trailing newline that secret-tool doesn't add — stripped here
    so both backends return the same string that was stored)."""
    if IS_MACOS:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout[:-1] if result.stdout.endswith("\n") else result.stdout
        return None
    result = subprocess.run(
        [SECRET_TOOL, "lookup", "service", service, "account", account],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and result.stdout:
        return result.stdout
    return None


def secret_store(service, account, label, secret):
    """Stores the secret, returning True on success. Both backends take
    the secret over stdin, never as an argv item another process could
    catch in a process listing — on macOS that means security's -i
    interactive mode (a plain `add-generic-password -w <secret>` would be
    argv), whose command parser needs backslashes and double quotes
    escaped."""
    if IS_MACOS:
        def esc(s):
            return s.replace("\\", "\\\\").replace('"', '\\"')
        cmd = (
            f'add-generic-password -U -s "{esc(service)}" -a "{esc(account)}"'
            f' -l "{esc(label)}" -w "{esc(secret)}"\n'
        )
        proc = subprocess.run(
            ["security", "-i"], input=cmd, text=True, capture_output=True
        )
        return proc.returncode == 0
    store = subprocess.run(
        [SECRET_TOOL, "store", f"--label={label}", "service", service, "account", account],
        input=secret, text=True,
    )
    return store.returncode == 0


def secret_clear(service, account):
    if IS_MACOS:
        subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", account],
            capture_output=True,
        )
    else:
        subprocess.run(
            [SECRET_TOOL, "clear", "service", service, "account", account],
            capture_output=True,
        )


def _mpris_names():
    """Active org.mpris.MediaPlayer2.* D-Bus names on the session bus, via
    busctl (ships with systemd — no extra dependency, unlike playerctl)."""
    try:
        proc = subprocess.run(
            ["busctl", "--user", "--json=short", "list"],
            capture_output=True, text=True, timeout=3,
        )
        if proc.returncode != 0:
            return []
        entries = json.loads(proc.stdout)
        return [e["name"] for e in entries if e.get("name", "").startswith("org.mpris.MediaPlayer2.")]
    except Exception:
        return []


def _mpris_get_property(dest, prop):
    try:
        proc = subprocess.run(
            [
                "busctl", "--user", "--json=short", "get-property", dest,
                "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", prop,
            ],
            capture_output=True, text=True, timeout=3,
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout).get("data")
    except Exception:
        return None


def _macos_apply_capture_hiding(hidden):
    """macOS: set every NSWindow of this process to sharingType
    NSWindowSharingNone (True) or back to the ReadOnly default (False).
    ScreenCaptureKit — which OBS's macOS Screen Capture is built on —
    omits sharing-none windows from every capture, so this keeps
    BitDVR's own control panel out of the recording the same way OBS's
    "hide OBS" checkbox keeps OBS out (that checkbox only covers OBS's
    windows; each app has to exclude its own). Done through the raw
    objc runtime so plain ctypes suffices — no pyobjc dependency. The
    windows are enumerated from NSApplication because Tk's winfo_id is
    not an NSView pointer on macOS (messaging it segfaults)."""
    import ctypes
    import ctypes.util
    try:
        objc = ctypes.CDLL(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        sel = objc.sel_registerName
        # objc_msgSend must be re-typed per call signature on arm64
        send = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
        send_count = ctypes.CFUNCTYPE(
            ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
        send_at = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(("objc_msgSend", objc))
        send_set = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(("objc_msgSend", objc))

        app = send(objc.objc_getClass(b"NSApplication"), sel(b"sharedApplication"))
        windows = send(app, sel(b"windows"))
        sharing = 0 if hidden else 1  # NSWindowSharingNone / NSWindowSharingReadOnly
        for i in range(send_count(windows, sel(b"count"))):
            send_set(send_at(windows, sel(b"objectAtIndex:"), i),
                     sel(b"setSharingType:"), sharing)
        return True
    except Exception:
        return False


def _macos_now_playing():
    """The system-wide now-playing item (what Control Center shows), read
    with media-control — which reaches the private MediaRemote framework
    through the community mediaremote-adapter shim, the only route left
    since Apple locked the framework down in macOS 15.4. Returns the
    payload dict, or None if nothing is registered / the tool is missing
    / a macOS update has broken the shim."""
    try:
        proc = subprocess.run(
            [MEDIA_CONTROL, "get"], capture_output=True, text=True, timeout=3,
        )
        if proc.returncode != 0:
            return None
        payload = json.loads(proc.stdout)
        return payload if isinstance(payload, dict) and payload else None
    except Exception:
        return None


def mpris_snapshot():
    """Returns [{name, status, length_seconds, title}] for every active
    media player. Linux: every org.mpris.MediaPlayer2.* name on the
    session bus. macOS: the single system now-playing item, mapped onto
    the same shape — an item that isn't currently playing reports as
    Paused, so its metadata stays grabbable exactly like a paused MPRIS
    player's."""
    if IS_MACOS:
        item = _macos_now_playing()
        if item is None:
            return []
        length = item.get("duration")
        if not (isinstance(length, (int, float)) and length > 0):
            length = None
        return [{
            "name": item.get("bundleIdentifier") or "now-playing",
            "status": "Playing" if item.get("playing") else "Paused",
            "length_seconds": length,
            "title": item.get("title") or None,
        }]
    players = []
    for name in _mpris_names():
        status = _mpris_get_property(name, "PlaybackStatus")
        if not isinstance(status, str):
            continue
        length_seconds = None
        title = None
        meta = _mpris_get_property(name, "Metadata")
        if isinstance(meta, dict):
            length_entry = meta.get("mpris:length")
            if isinstance(length_entry, dict) and isinstance(length_entry.get("data"), (int, float)):
                length_seconds = length_entry["data"] / 1_000_000
            title_entry = meta.get("xesam:title")
            if isinstance(title_entry, dict):
                title = title_entry.get("data")
        players.append(
            {"name": name, "status": status, "length_seconds": length_seconds, "title": title}
        )
    return players


def newest_raw_recording():
    if not RECORDINGS_DIR.is_dir():
        return None
    candidates = sorted(
        RECORDINGS_DIR.glob("*.mkv"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return candidates[0] if candidates else None


def scan_library_root(root_dir):
    """Returns (loose: [Path], series: {series_name: {season_name: [Path]}})
    for one library root (Movies/ or TV/). Files directly inside a series
    folder with no Season NN subfolder are grouped under season_name ""
    rather than swept into a season/series bundle, since bitdvr-organize.sh
    only creates a Season NN subfolder when --season was actually given —
    keeping them separate avoids ever bundling a directory that overlaps
    with another selectable node."""
    loose = []
    series = {}
    if not root_dir.is_dir():
        return loose, series
    for entry in sorted(root_dir.iterdir()):
        if entry.is_file() and entry.suffix == ".mkv":
            loose.append(entry)
        elif entry.is_dir():
            seasons = {}
            direct = sorted(entry.glob("*.mkv"))
            if direct:
                seasons[""] = direct
            for season_dir in sorted(p for p in entry.iterdir() if p.is_dir()):
                episodes = sorted(season_dir.glob("*.mkv"))
                if episodes:
                    seasons[season_dir.name] = episodes
            if seasons:
                series[entry.name] = seasons
    return loose, series


def scan_library():
    """Returns {"movies": (loose, series), "tv": (loose, series)} — see
    scan_library_root. Both library roots can now hold either flat files
    or a Series/[Season NN/] structure, since Organize's "Part of a
    series" checkbox is independent of the Movies/TV label."""
    return {
        "movies": scan_library_root(LIBRARY_DIR / "Movies"),
        "tv": scan_library_root(LIBRARY_DIR / "TV"),
    }


class App(tk.Tk):
    def __init__(self):
        # className sets this window's WM_CLASS to "bitdvr" — without it,
        # Tkinter defaults to something derived from the interpreter
        # (bitdvr-gui.py runs as python3, since that's the shebang
        # interpreter actually exec'd), which KDE's taskbar can't match
        # back to bitdvr.desktop, so it falls back to a generic Python
        # icon instead of Icon=video-television. Paired with
        # StartupWMClass=bitdvr in the .desktop file itself.
        super().__init__(className="bitdvr")
        self.title("BitDVR")
        self.geometry("760x640")
        self.minsize(620, 480)

        self.log_queue = queue.Queue()
        self.proc = None
        self.log_file = None
        self.on_done = None
        self._captured_lines = []

        self.obs_client = None
        self.record_deadline = None
        self._record_tick_job = None
        self._record_health_tick = 0
        self.record_log_file = None
        self.preview_client = None
        self.preview_job = None
        self.preview_image = None
        self._preview_fail_count = 0
        self.mpris_job = None
        self.mpris_was_playing = False
        self.config = load_config()
        self._capture_hiding_job = None
        self._sync_capture_hiding()

        notebook = ttk.Notebook(self)
        notebook.pack(fill="x", padx=8, pady=(8, 0))
        self.notebook = notebook

        record_tab = ttk.Frame(notebook)
        organize_tab = ttk.Frame(notebook)
        send_tab = ttk.Frame(notebook)
        clean_tab = ttk.Frame(notebook)
        settings_tab = ttk.Frame(notebook)
        notebook.add(record_tab, text="1. Record")
        notebook.add(organize_tab, text="2. Organize")
        notebook.add(send_tab, text="3. Send")
        notebook.add(clean_tab, text="4. Clean")
        notebook.add(settings_tab, text="Settings")
        notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.record_tab = record_tab
        self.organize_tab = organize_tab
        self.send_tab = send_tab
        self.clean_tab = clean_tab
        self.settings_tab = settings_tab

        self._build_record_tab(record_tab)
        self._build_organize_tab(organize_tab)
        self._build_send_tab(send_tab)
        self._build_clean_tab(clean_tab)
        self._build_settings_tab(settings_tab)
        self._apply_local_mode()

        log_frame = ttk.Frame(self)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(8, 6))
        self.log_text = tk.Text(
            log_frame,
            wrap="word",
            state="disabled",
            bg="#111111",
            fg="#dddddd",
            # "monospace" is an X11/fontconfig alias that macOS's Tk
            # doesn't resolve — it silently falls back to a proportional
            # font there, so name a real family instead.
            font=("Menlo", 11) if IS_MACOS else ("monospace", 10),
        )
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken").pack(
            fill="x", side="bottom"
        )

        self._load_default_recording()
        self._refresh_tree()
        self._refresh_clean_tree()
        self._on_tab_changed()

    # ------------------------------------------------------------------ Record

    def _build_record_tab(self, parent):
        self.rec_use_timer_var = tk.BooleanVar(value=True)
        self.rec_minutes_var = tk.StringVar(value="0")
        self.rec_seconds_var = tk.StringVar(value="0")
        self.rec_status_var = tk.StringVar(value="Idle — not recording.")

        frm = ttk.Frame(parent)
        frm.pack(fill="x", padx=8, pady=8)

        ttk.Checkbutton(
            frm, text="Auto-stop after:", variable=self.rec_use_timer_var,
            command=self._rec_on_timer_toggle,
        ).grid(row=0, column=0, sticky="w")
        self.rec_min_spin = ttk.Spinbox(
            frm, from_=0, to=999, textvariable=self.rec_minutes_var, width=5
        )
        self.rec_min_spin.grid(row=0, column=1, padx=(4, 2))
        ttk.Label(frm, text="min").grid(row=0, column=2, sticky="w")
        self.rec_sec_spin = ttk.Spinbox(
            frm, from_=0, to=59, textvariable=self.rec_seconds_var, width=5
        )
        self.rec_sec_spin.grid(row=0, column=3, padx=(8, 2))
        ttk.Label(frm, text="sec").grid(row=0, column=4, sticky="w")
        ttk.Button(
            frm, text="Grab runtime", command=self._on_grab_runtime,
        ).grid(row=0, column=5, padx=(12, 0))
        ttk.Label(
            frm, text="(uncheck for manual stop only — Stop is always available either way)"
        ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(4, 0))

        self.mpris_auto_startstop_var = tk.BooleanVar(value=False)
        mpris_frm = ttk.Frame(parent)
        mpris_frm.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Checkbutton(
            mpris_frm, text="Auto start/stop recording with playback",
            variable=self.mpris_auto_startstop_var, command=self._on_mpris_toggle,
        ).pack(anchor="w")

        btn_frm = ttk.Frame(parent)
        btn_frm.pack(pady=(4, 8))
        self.rec_start_btn = ttk.Button(
            btn_frm, text="Start Recording", command=self._on_record_start
        )
        self.rec_start_btn.pack(side="left", padx=4)
        self.rec_stop_btn = ttk.Button(
            btn_frm, text="Stop Recording", command=self._on_record_stop, state="disabled"
        )
        self.rec_stop_btn.pack(side="left", padx=4)

        ttk.Label(parent, textvariable=self.rec_status_var, anchor="w").pack(
            fill="x", padx=8
        )

        self.rec_preview_label = tk.Label(
            parent,
            text="Preview will appear once this tab is active.",
            background="#111111",
            foreground="#888888",
        )
        self.rec_preview_label.pack(padx=8, pady=(8, 8))

    def _rec_on_timer_toggle(self):
        state = "normal" if self.rec_use_timer_var.get() else "disabled"
        self.rec_min_spin.configure(state=state)
        self.rec_sec_spin.configure(state=state)

    def _on_mpris_toggle(self):
        self._sync_mpris_polling()

    def _sync_capture_hiding(self):
        """macOS: keep this process's windows out of screen captures
        while the setting is on. A slow repeating tick rather than a
        one-shot, because dialogs create new NSWindows after startup
        and each needs the same sharingType treatment."""
        if not IS_MACOS:
            return
        if self._capture_hiding_job is not None:
            self.after_cancel(self._capture_hiding_job)
            self._capture_hiding_job = None
        if self.config.get("hide_from_capture", True):
            self._tick_capture_hiding()
        else:
            _macos_apply_capture_hiding(False)

    def _tick_capture_hiding(self):
        _macos_apply_capture_hiding(True)
        self._capture_hiding_job = self.after(3000, self._tick_capture_hiding)

    def _sync_mpris_polling(self):
        """MPRIS polling runs independent of which tab is showing, so
        auto-start/stop works hands-off even while BitDVR is sitting on
        the Organize tab."""
        should_poll = self.mpris_auto_startstop_var.get()
        if should_poll and self.mpris_job is None:
            self.mpris_was_playing = False
            self._tick_mpris()
        elif not should_poll and self.mpris_job is not None:
            self.after_cancel(self.mpris_job)
            self.mpris_job = None

    def _tick_mpris(self):
        players = mpris_snapshot()
        playing = [p for p in players if p["status"] == "Playing"]
        is_playing = bool(playing)

        if self.mpris_auto_startstop_var.get():
            if is_playing and not self.mpris_was_playing and self.obs_client is None:
                label = playing[0]["title"] or playing[0]["name"]
                self._append_log(f"==> Playback started ({label}) — auto-starting recording.\n")
                self._start_recording(silent=True)
            elif not is_playing and self.mpris_was_playing and self.obs_client is not None:
                self._append_log("==> Playback stopped.\n")
                self._stop_record("playback ended")

        self.mpris_was_playing = is_playing
        self.mpris_job = self.after(2000, self._tick_mpris)

    @staticmethod
    def _mpris_candidates():
        """Active MPRIS players, ranked Playing > Paused > everything
        else. Rank alone, not play state — a Paused (or freshly-loaded
        Stopped) track still reports its real metadata, and knowing the
        runtime/title before hitting Play is the point of grabbing it."""
        players = mpris_snapshot()
        rank = {"Playing": 0, "Paused": 1}
        return sorted(players, key=lambda p: rank.get(p["status"], 2))

    def _on_grab_runtime(self):
        with_length = [p for p in self._mpris_candidates() if p["length_seconds"]]
        if not with_length:
            messagebox.showinfo("Grab runtime", "No active media player is reporting a runtime.")
            return
        mins, secs = divmod(int(with_length[0]["length_seconds"]), 60)
        self.rec_minutes_var.set(str(mins))
        self.rec_seconds_var.set(str(secs))
        self.rec_use_timer_var.set(True)
        self._rec_on_timer_toggle()

    def _on_grab_title(self):
        with_title = [p for p in self._mpris_candidates() if p["title"]]
        if not with_title:
            messagebox.showinfo("Grab title", "No active media player is reporting a title.")
            return
        self.org_title_var.set(with_title[0]["title"])

    def _on_tab_changed(self, _event=None):
        self._refresh_tree()
        self._refresh_clean_tree()
        if hasattr(self, "record_tab") and str(self.notebook.select()) == str(self.record_tab):
            self._start_preview()
        else:
            self._stop_preview()

    def _start_preview(self):
        if self.preview_job is not None or obs is None:
            return
        client = self._get_obs_client()
        if client is None:
            self.rec_preview_label.configure(image="", text="Preview unavailable (no OBS connection).")
            self.preview_image = None
            return
        self.preview_client = client
        self._preview_fail_count = 0
        self._tick_preview()

    def _tick_preview(self):
        if self.preview_client is None:
            return
        try:
            scene = self.preview_client.get_current_program_scene()
            shot = self.preview_client.get_source_screenshot(
                name=scene.current_program_scene_name,
                img_format="png",
                width=320,
                height=180,
                quality=-1,
            )
            b64 = shot.image_data.split(",", 1)[1]
            img = tk.PhotoImage(data=b64)
            self.rec_preview_label.configure(image=img, text="")
            self.preview_image = img
            self._preview_fail_count = 0
        except Exception:
            self._preview_fail_count += 1
            if self._preview_fail_count >= 3:
                self._stop_preview(message="Preview lost connection to OBS.")
                self._start_preview()
                return
        self.preview_job = self.after(1000, self._tick_preview)

    def _stop_preview(self, message="Preview paused — switch back to this tab to resume."):
        if self.preview_job is not None:
            self.after_cancel(self.preview_job)
            self.preview_job = None
        if self.preview_client is not None:
            try:
                self.preview_client.disconnect()
            except Exception:
                pass
            self.preview_client = None
        self.rec_preview_label.configure(image="", text=message)
        self.preview_image = None
        self._preview_fail_count = 0

    def _get_obs_client(self):
        if obs is None:
            messagebox.showerror(
                "Missing dependency",
                "obsws-python is not installed.\n\n"
                "Run: python3 -m pip install --user obsws-python",
            )
            return None
        pw = self._get_or_prompt_secret(
            OBS_SECRET_SERVICE,
            "obs-websocket",
            "BitDVR OBS WebSocket",
            "OBS WebSocket server password (stored in the login keyring):",
        )
        if pw is None:
            return None
        host, port = self.config["obs_host"], self.config["obs_port"]
        while True:
            try:
                return obs.ReqClient(host=host, port=port, password=pw, timeout=5)
            except Exception as e:
                retry = messagebox.askretrycancel(
                    "OBS connection failed",
                    f"Could not connect to OBS WebSocket at {host}:{port}.\n\n"
                    f"{self._describe_obs_error(e)}\n\n"
                    "If OBS isn't running, launch it and click Retry.",
                )
                if not retry:
                    return None

    @staticmethod
    def _describe_obs_error(e):
        """Plain-English gloss for the connect-time exceptions we actually
        see (ConnectionRefusedError's str is just '[Errno 111] Connection
        refused', which isn't self-explanatory) — falls back to str(e) for
        anything else."""
        if isinstance(e, ConnectionRefusedError):
            return "Connection refused — OBS isn't running, or its WebSocket server is disabled (Tools → WebSocket Server Settings in OBS)."
        if isinstance(e, TimeoutError):
            return "Connection timed out — check the host/port in Settings and that OBS is reachable."
        return str(e)

    def _obs_call(self, method, *args, **kwargs):
        """Call `method` on self.obs_client, reconnecting once (with the
        same launch-OBS-and-retry prompt) if the connection was severed.
        Re-raises the original exception if reconnecting fails or is
        cancelled, so callers' existing except blocks still fire."""
        try:
            return getattr(self.obs_client, method)(*args, **kwargs)
        except Exception:
            try:
                self.obs_client.disconnect()
            except Exception:
                pass
            self.obs_client = self._get_obs_client()
            if self.obs_client is None:
                raise
            return getattr(self.obs_client, method)(*args, **kwargs)

    def _on_record_start(self):
        self._start_recording(silent=False)

    def _start_recording(self, silent=False):
        use_timer = self.rec_use_timer_var.get()
        total_seconds = 0
        if use_timer:
            try:
                minutes = int(self.rec_minutes_var.get() or 0)
                seconds = int(self.rec_seconds_var.get() or 0)
            except ValueError:
                if silent:
                    self._append_log("==> Auto-start skipped: invalid timer duration.\n")
                else:
                    messagebox.showerror("Invalid duration", "Minutes/seconds must be numbers.")
                return
            total_seconds = minutes * 60 + seconds
            if total_seconds <= 0:
                if silent:
                    self._append_log(
                        "==> Auto-start skipped: enter a duration, or uncheck auto-stop for manual-only.\n"
                    )
                else:
                    messagebox.showerror(
                        "Invalid duration",
                        "Enter a duration greater than 0, or uncheck auto-stop for manual-only.",
                    )
                return

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"bitdvr-record-{datetime.now():%Y%m%d-%H%M%S}.log"
        self.record_log_file = open(log_path, "w")
        self._append_log(
            f"$ Connecting to OBS WebSocket at {self.config['obs_host']}:{self.config['obs_port']}...\n",
            file=self.record_log_file,
        )

        client = self._get_obs_client()
        if client is None:
            self._append_log("==> Cancelled (no OBS connection).\n", file=self.record_log_file)
            self.record_log_file.close()
            self.record_log_file = None
            return
        try:
            client.start_record()
        except Exception as e:
            self._append_log(f"==> ERROR: failed to start recording: {e}\n", file=self.record_log_file)
            if not silent:
                messagebox.showerror("OBS error", f"Failed to start recording:\n\n{e}")
            try:
                client.disconnect()
            except Exception:
                pass
            self.record_log_file.close()
            self.record_log_file = None
            return

        self.obs_client = client
        self.record_deadline = time.monotonic() + total_seconds if use_timer else None
        self._record_health_tick = 0
        self.rec_start_btn.configure(state="disabled")
        self.rec_stop_btn.configure(state="normal")
        self.status_var.set("Recording...")
        if use_timer:
            mins, secs = divmod(total_seconds, 60)
            self._append_log(
                f"==> Recording started — auto-stop in {mins:02d}:{secs:02d}.\n",
                file=self.record_log_file,
            )
        else:
            self._append_log("==> Recording started — manual stop only.\n", file=self.record_log_file)
        self._tick_record()

    def _tick_record(self):
        if self.obs_client is None:
            return
        self._record_health_tick += 1
        if self._record_health_tick >= 5:
            self._record_health_tick = 0
            try:
                self._obs_call("get_record_status")
            except Exception:
                self._append_log(
                    "==> Lost connection to OBS and reconnect was cancelled — "
                    "BitDVR can no longer control this recording. Check OBS directly.\n",
                    file=self.record_log_file,
                )
                messagebox.showwarning(
                    "OBS connection lost",
                    "BitDVR lost its connection to OBS and can no longer control this "
                    "recording.\n\nOBS itself may still be recording — check it directly.",
                )
                if self._record_tick_job is not None:
                    self.after_cancel(self._record_tick_job)
                    self._record_tick_job = None
                self.obs_client = None
                self.record_deadline = None
                self.rec_start_btn.configure(state="normal")
                self.rec_stop_btn.configure(state="disabled")
                self.rec_status_var.set("Idle — lost connection to OBS.")
                self.status_var.set("Idle")
                if self.record_log_file:
                    self.record_log_file.close()
                    self.record_log_file = None
                return
        if self.record_deadline is not None:
            remaining = self.record_deadline - time.monotonic()
            if remaining <= 0:
                self.rec_status_var.set("Auto-stop timer elapsed — stopping...")
                self._stop_record("timer elapsed")
                return
            mins, secs = divmod(int(remaining), 60)
            self.rec_status_var.set(f"Recording — auto-stop in {mins:02d}:{secs:02d}")
        else:
            self.rec_status_var.set("Recording — manual stop only.")
        self._record_tick_job = self.after(1000, self._tick_record)

    def _on_record_stop(self):
        self._stop_record("stopped manually")

    def _stop_record(self, reason):
        if self.obs_client is None:
            return
        if self._record_tick_job is not None:
            self.after_cancel(self._record_tick_job)
            self._record_tick_job = None

        output_path = None
        try:
            resp = self._obs_call("stop_record")
            output_path = getattr(resp, "output_path", None)
            self._append_log(
                f"==> Recording stopped ({reason}). Output: {output_path}\n",
                file=self.record_log_file,
            )
        except Exception as e:
            self._append_log(f"==> ERROR stopping recording: {e}\n", file=self.record_log_file)
            messagebox.showerror("OBS error", f"Failed to stop recording cleanly:\n\n{e}")
        finally:
            try:
                self.obs_client.disconnect()
            except Exception:
                pass
            self.obs_client = None
            self.record_deadline = None
            if self.record_log_file:
                self.record_log_file.close()
                self.record_log_file = None

        self.rec_start_btn.configure(state="normal")
        self.rec_stop_btn.configure(state="disabled")
        self.rec_status_var.set(f"Idle — last recording {reason}.")
        self.status_var.set("Idle")
        self._notify("BitDVR", f"Recording finished ({reason}).")

        if output_path and Path(output_path).is_file():
            self.org_file_path.set(output_path)
            # Don't clobber a title already grabbed (via "Grab title") or
            # typed by hand before the recording stopped — only fall back
            # to the raw filename stem if the field is still empty.
            if not self.org_title_var.get().strip():
                self.org_title_var.set(Path(output_path).stem)
            self.notebook.select(self.organize_tab)
        else:
            self._load_default_recording()

    # ------------------------------------------------------------ Local mode

    def _apply_local_mode(self):
        """Local mode means BitDVR runs on the same host as the media
        server, so Organize delivers straight into the live library and
        there's no network hop for Send to do, nor a separate local
        staging copy for Clean to remove — Plex's library IS the
        destination now, not a second copy of it. Hide both rather than
        leaving Send merely disabled, since a disabled tab still implies
        a two-step model that no longer applies."""
        state = "hidden" if self.config.get("local_mode", False) else "normal"
        self.notebook.tab(self.send_tab, state=state)
        self.notebook.tab(self.clean_tab, state=state)
        self._renumber_tabs()

    def _renumber_tabs(self):
        names = {
            self.record_tab: "Record",
            self.organize_tab: "Organize",
            self.send_tab: "Send",
            self.clean_tab: "Clean",
        }
        visible = [
            tab for tab in (self.record_tab, self.organize_tab, self.send_tab, self.clean_tab)
            if self.notebook.tab(tab, "state") != "hidden"
        ]
        for i, tab in enumerate(visible, start=1):
            self.notebook.tab(tab, text=f"{i}. {names[tab]}")

    # --------------------------------------------------------------- Settings

    def _build_settings_tab(self, parent):
        self.set_obs_host_var = tk.StringVar(value=self.config["obs_host"])
        self.set_obs_port_var = tk.StringVar(value=str(self.config["obs_port"]))
        self.set_plex_host_var = tk.StringVar(value=self.config["plex_host"])
        self.set_remote_root_var = tk.StringVar(
            value=self.config.get("remote_library_root", DEFAULT_CONFIG["remote_library_root"])
        )
        self.set_local_mode_var = tk.BooleanVar(value=self.config.get("local_mode", False))
        self.set_hide_capture_var = tk.BooleanVar(
            value=self.config.get("hide_from_capture", True)
        )
        self.set_local_library_var = tk.StringVar(
            value=self.config.get("local_library_root", DEFAULT_CONFIG["local_library_root"])
        )

        frm = ttk.Frame(parent)
        frm.pack(fill="x", padx=8, pady=8)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="OBS WebSocket Host IP:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.set_obs_host_var).grid(
            row=0, column=1, sticky="ew", padx=6
        )
        ttk.Label(frm, text="OBS WebSocket Port:").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Entry(frm, textvariable=self.set_obs_port_var, width=10).grid(
            row=1, column=1, sticky="w", padx=6, pady=(6, 0)
        )
        ttk.Label(frm, text="Deluge Host IP:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(frm, textvariable=self.set_plex_host_var).grid(
            row=2, column=1, sticky="ew", padx=6, pady=(6, 0)
        )
        ttk.Label(
            frm, text="(unused in Local mode)", foreground="#888888"
        ).grid(row=2, column=2, sticky="w", padx=(6, 0), pady=(6, 0))
        ttk.Label(frm, text="Media library root (on Deluge host):").grid(
            row=3, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Entry(frm, textvariable=self.set_remote_root_var).grid(
            row=3, column=1, sticky="ew", padx=6, pady=(6, 0)
        )
        ttk.Label(
            frm, text="(unused in Local mode)", foreground="#888888"
        ).grid(row=3, column=2, sticky="w", padx=(6, 0), pady=(6, 0))
        ttk.Label(
            frm,
            text="Path on the Deluge/media-server box containing Movies/ and TV/ "
                 "(e.g. /mnt/media) — series sends build their destination from it.",
            foreground="#888888", wraplength=680, justify="left",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(2, 0))

        ttk.Separator(frm, orient="horizontal").grid(
            row=5, column=0, columnspan=3, sticky="ew", pady=(10, 8)
        )
        ttk.Checkbutton(
            frm,
            text="Local mode (BitDVR runs on the same host as the media server)",
            variable=self.set_local_mode_var, command=self._on_local_mode_toggle,
        ).grid(row=6, column=0, columnspan=3, sticky="w")
        ttk.Label(frm, text="Media library root (this machine):").grid(
            row=7, column=0, sticky="w", pady=(6, 0)
        )
        self.set_local_library_entry = ttk.Entry(frm, textvariable=self.set_local_library_var)
        self.set_local_library_entry.grid(row=7, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Label(
            frm,
            text="Root containing Movies/ and TV/ — Organize writes straight here "
                 "instead of the local staging Library/, and Send/Clean are hidden.",
            foreground="#888888", wraplength=680, justify="left",
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(2, 0))
        self._on_local_mode_toggle()

        ttk.Separator(frm, orient="horizontal").grid(
            row=9, column=0, columnspan=3, sticky="ew", pady=(10, 8)
        )
        ttk.Checkbutton(
            frm,
            text="Hide BitDVR's windows from screen recordings (macOS only)",
            variable=self.set_hide_capture_var,
            state="normal" if IS_MACOS else "disabled",
        ).grid(row=10, column=0, columnspan=3, sticky="w")
        ttk.Label(
            frm,
            text="Keeps this control panel out of the capture even when it's on the "
                 "recorded screen. On Linux, exclusion isn't per-window — put BitDVR "
                 "on a different monitor or workspace than the one OBS captures.",
            foreground="#888888", wraplength=680, justify="left",
        ).grid(row=11, column=0, columnspan=3, sticky="w", pady=(2, 0))

        ttk.Button(parent, text="Save", command=self._on_settings_save).pack(pady=(4, 12))

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=8)

        pw_frm = ttk.Frame(parent)
        pw_frm.pack(fill="x", padx=8, pady=12)
        ttk.Label(pw_frm, text="Stored passwords (in the login keyring):").pack(
            anchor="w", pady=(0, 6)
        )
        ttk.Button(
            pw_frm, text="Change OBS WebSocket Password...",
            command=self._on_change_obs_password,
        ).pack(anchor="w", pady=2)
        ttk.Button(
            pw_frm, text="Change Deluge-web Password...",
            command=self._on_change_deluge_password,
        ).pack(anchor="w", pady=2)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=8)

        export_frm = ttk.Frame(parent)
        export_frm.pack(fill="x", padx=8, pady=12)
        ttk.Label(
            export_frm,
            text="Settings file (host/port only — passwords never leave the keyring):",
        ).pack(anchor="w", pady=(0, 6))
        export_btn_row = ttk.Frame(export_frm)
        export_btn_row.pack(anchor="w")
        ttk.Button(
            export_btn_row, text="Export Settings...", command=self._on_export_config,
        ).pack(side="left", padx=(0, 4))
        ttk.Button(
            export_btn_row, text="Import Settings...", command=self._on_import_config,
        ).pack(side="left")

    def _on_local_mode_toggle(self):
        state = "normal" if self.set_local_mode_var.get() else "disabled"
        self.set_local_library_entry.configure(state=state)

    def _on_export_config(self):
        path = filedialog.asksaveasfilename(
            title="Export BitDVR Settings",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="bitdvr-config.json",
        )
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(self.config, indent=2))
        except OSError as e:
            messagebox.showerror("Export failed", f"Could not write file:\n\n{e}")
            return
        messagebox.showinfo("Export Settings", f"Settings exported to:\n\n{path}")

    def _on_import_config(self):
        path = filedialog.askopenfilename(
            title="Import BitDVR Settings",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError) as e:
            messagebox.showerror("Import failed", f"Could not read that file:\n\n{e}")
            return
        if not isinstance(data, dict) or not any(k in data for k in DEFAULT_CONFIG):
            messagebox.showerror("Import failed", "That file doesn't look like a BitDVR settings export.")
            return

        candidate = dict(self.config)
        candidate.update({k: data[k] for k in DEFAULT_CONFIG if k in data})
        try:
            candidate["obs_port"] = int(candidate["obs_port"])
            if not (1 <= candidate["obs_port"] <= 65535):
                raise ValueError
        except (ValueError, TypeError):
            messagebox.showerror("Import failed", "Imported OBS port is not a valid port number.")
            return
        if not str(candidate.get("obs_host", "")).strip():
            messagebox.showerror("Import failed", "Imported settings are missing an OBS host.")
            return

        self.config = candidate
        save_config(self.config)
        self.set_obs_host_var.set(self.config["obs_host"])
        self.set_obs_port_var.set(str(self.config["obs_port"]))
        self.set_plex_host_var.set(self.config["plex_host"])
        self.set_remote_root_var.set(self.config.get("remote_library_root", ""))
        self.set_local_mode_var.set(self.config.get("local_mode", False))
        self.set_local_library_var.set(
            self.config.get("local_library_root", DEFAULT_CONFIG["local_library_root"])
        )
        self.set_hide_capture_var.set(self.config.get("hide_from_capture", True))
        self._on_local_mode_toggle()
        self._apply_local_mode()
        self._sync_capture_hiding()
        messagebox.showinfo("Import Settings", "Settings imported and applied.")

    def _on_settings_save(self):
        host = self.set_obs_host_var.get().strip()
        plex_host = self.set_plex_host_var.get().strip()
        remote_library_root = self.set_remote_root_var.get().strip()
        local_mode = self.set_local_mode_var.get()
        local_library_root = self.set_local_library_var.get().strip()
        if not host:
            messagebox.showerror("Missing field", "OBS host can't be empty.")
            return
        if local_mode and not local_library_root:
            messagebox.showerror(
                "Missing field",
                "The media library root can't be empty when Local mode is enabled.",
            )
            return
        try:
            port = int(self.set_obs_port_var.get().strip())
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Invalid port", "OBS WebSocket port must be a number between 1 and 65535."
            )
            return

        self.config = {
            "obs_host": host,
            "obs_port": port,
            "plex_host": plex_host,
            "remote_library_root": remote_library_root,
            "local_mode": local_mode,
            "local_library_root": local_library_root,
            "hide_from_capture": self.set_hide_capture_var.get(),
        }
        save_config(self.config)
        self._apply_local_mode()
        self._sync_capture_hiding()
        messagebox.showinfo("Saved", "Settings saved.")

    def _on_change_obs_password(self):
        self._change_secret(
            OBS_SECRET_SERVICE, "obs-websocket", "BitDVR OBS WebSocket",
            "New OBS WebSocket server password:",
        )

    def _on_change_deluge_password(self):
        self._change_secret(
            SECRET_SERVICE, "deluge-web", "BitDVR deluge-web",
            "New deluge-web password:",
        )

    def _change_secret(self, service, account, keyring_label, prompt_text):
        secret_clear(service, account)
        pw = self._get_or_prompt_secret(service, account, keyring_label, prompt_text)
        if pw is not None:
            messagebox.showinfo("Password updated", f"{keyring_label} password updated in the keyring.")
        else:
            messagebox.showinfo("Unchanged", "No new password was stored.")

    # ---------------------------------------------------------------- Organize

    def _build_organize_tab(self, parent):
        self.org_file_path = tk.StringVar()
        self.org_is_series_var = tk.BooleanVar(value=False)
        self.org_library_var = tk.StringVar(value="movies")
        self.org_title_var = tk.StringVar()
        self.org_year_var = tk.StringVar()
        self.org_is_series_var = tk.BooleanVar(value=False)
        self.org_series_var = tk.StringVar()
        self.org_season_var = tk.StringVar()
        self.org_episode_var = tk.StringVar()

        frm = ttk.Frame(parent)
        frm.pack(fill="x", padx=8, pady=8)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Recording:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.org_file_path, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=6
        )
        ttk.Button(frm, text="Browse...", command=self._org_browse).grid(row=0, column=2)
        ttk.Button(frm, text="Delete...", command=self._org_delete).grid(row=0, column=3, padx=(6, 0))

        ttk.Label(frm, text="Library:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        lib_combo = ttk.Combobox(
            frm,
            textvariable=self.org_library_var,
            values=["movies", "tv"],
            state="readonly",
            width=10,
        )
        lib_combo.grid(row=1, column=1, sticky="w", padx=6, pady=(6, 0))
        lib_combo.bind("<<ComboboxSelected>>", lambda _e: self._org_on_library_change())

        title_row = ttk.Frame(frm)
        title_row.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        title_row.columnconfigure(1, weight=1)
        ttk.Label(title_row, text="Title (optional):").grid(row=0, column=0, sticky="w")
        ttk.Entry(title_row, textvariable=self.org_title_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(
            title_row, text="Grab title", command=self._on_grab_title,
        ).grid(row=0, column=2, padx=(6, 0))

        self.org_year_row = ttk.Frame(frm)
        self.org_year_row.grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Label(self.org_year_row, text="Year (optional):").pack(side="left")
        ttk.Entry(self.org_year_row, textvariable=self.org_year_var, width=8).pack(
            side="left", padx=6
        )
        ttk.Label(
            self.org_year_row,
            text='— media servers match movies more reliably as "Title (Year)"',
            foreground="#888888",
        ).pack(side="left")

        ttk.Checkbutton(
            frm,
            text="Part of a series (adds series name / season / episode for naming & folders)",
            variable=self.org_is_series_var, command=self._org_on_series_toggle,
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))

        self.org_series_frame = ttk.Frame(frm)
        self.org_series_frame.columnconfigure(1, weight=1)
        self.org_series_frame.columnconfigure(3, weight=1)
        ttk.Label(self.org_series_frame, text="Series name (optional):").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.org_series_frame, textvariable=self.org_series_var).grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=6
        )
        ttk.Label(self.org_series_frame, text="Season (optional):").grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        ttk.Spinbox(
            self.org_series_frame, from_=0, to=99, textvariable=self.org_season_var, width=5
        ).grid(row=1, column=1, sticky="w", padx=6, pady=(4, 0))
        ttk.Label(self.org_series_frame, text="Episode (optional):").grid(
            row=1, column=2, sticky="w", pady=(4, 0)
        )
        ttk.Spinbox(
            self.org_series_frame, from_=0, to=999, textvariable=self.org_episode_var, width=5
        ).grid(row=1, column=3, sticky="w", padx=6, pady=(4, 0))
        self.org_series_frame.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        self.org_series_frame.grid_remove()

        ttk.Label(
            parent,
            text="Nothing here is required — anything left blank is simply left out of "
                 "the resulting name/path. Season+Episode use the S01E02 pattern "
                 "media-server scanners key on; leaving both blank means the scanner "
                 "may not match the file at all.",
            foreground="#888888", wraplength=680, justify="left",
        ).pack(anchor="w", padx=8, pady=(4, 4))

        ttk.Button(parent, text="Organize", command=self._on_organize).pack(pady=(4, 8))

        self._org_on_library_change()

    def _org_on_library_change(self):
        if self.org_library_var.get() == "movies":
            self.org_year_row.grid()
        else:
            self.org_year_row.grid_remove()

    def _org_on_series_toggle(self):
        if self.org_is_series_var.get():
            self.org_series_frame.grid()
        else:
            self.org_series_frame.grid_remove()

    def _load_default_recording(self):
        f = newest_raw_recording()
        if f:
            self.org_file_path.set(str(f))
            self.org_title_var.set(f.stem)
        else:
            self.org_file_path.set("")
            self.org_title_var.set("")

    def _org_delete(self):
        path = self.org_file_path.get()
        if not path or not Path(path).is_file():
            messagebox.showerror("No recording", "No recording selected to delete.")
            return
        if not messagebox.askyesno(
            "Delete recording?",
            f"Permanently delete this file?\n\n{path}\n\nThis cannot be undone.",
        ):
            return
        try:
            Path(path).unlink()
        except OSError as e:
            messagebox.showerror("Delete failed", f"Could not delete file:\n\n{e}")
            return
        self.status_var.set(f"Deleted {Path(path).name}")
        self._load_default_recording()

    def _org_browse(self):
        initial = str(RECORDINGS_DIR) if RECORDINGS_DIR.exists() else str(Path.home())
        path = filedialog.askopenfilename(
            initialdir=initial,
            filetypes=[("Video files", "*.mkv *.mp4 *.mov"), ("All files", "*.*")],
        )
        if path:
            self.org_file_path.set(path)
            self.org_title_var.set(Path(path).stem)

    def _on_organize(self):
        path = self.org_file_path.get()
        if not path or not Path(path).is_file():
            messagebox.showerror("No recording", "Pick a recording file first.")
            return
        if not ORGANIZE_SCRIPT.is_file():
            messagebox.showerror("Missing script", f"Not found: {ORGANIZE_SCRIPT}")
            return

        library = self.org_library_var.get()
        cmd = [str(ORGANIZE_SCRIPT), "--label", library]

        title = self.org_title_var.get().strip()
        if title:
            cmd += ["--title", title]

        if library == "movies":
            year = self.org_year_var.get().strip()
            if year:
                if not (year.isdigit() and len(year) == 4):
                    messagebox.showerror("Invalid year", "Year must be a 4-digit number.")
                    return
                cmd += ["--year", year]

        if self.org_is_series_var.get():
            series = self.org_series_var.get().strip()
            season = self.org_season_var.get().strip()
            episode = self.org_episode_var.get().strip()
            if series:
                cmd += ["--series", series]
            if season:
                if not season.isdigit():
                    messagebox.showerror("Invalid season", "Season must be a number.")
                    return
                cmd += ["--season", season]
            if episode:
                if not episode.isdigit():
                    messagebox.showerror("Invalid episode", "Episode must be a number.")
                    return
                cmd += ["--episode", episode]

        if self.config.get("local_mode", False):
            cmd += ["--dest-root", self.config.get(
                "local_library_root", DEFAULT_CONFIG["local_library_root"]
            )]

        cmd.append(path)
        self._run_command(cmd, on_done=self._on_organize_done)

    def _on_organize_done(self, rc):
        if rc == 0:
            self._load_default_recording()
            self.org_year_var.set("")
            self.org_series_var.set("")
            self.org_season_var.set("")
            self.org_episode_var.set("")
            self.org_is_series_var.set(False)
            self._org_on_series_toggle()
            self._refresh_tree()

    # -------------------------------------------------------------- Send tab

    def _build_send_tab(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Button(top, text="Refresh", command=self._refresh_tree).pack(side="right")

        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse", height=10)
        tscroll = ttk.Scrollbar(tree_frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tscroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tscroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_tree_select())

        self.send_details_var = tk.StringVar(value="Select a file, season, or series to send.")
        ttk.Label(
            parent, textvariable=self.send_details_var, wraplength=680, justify="left"
        ).pack(fill="x", padx=8)

        btn_row = ttk.Frame(parent)
        btn_row.pack(pady=8)
        self.send_btn = ttk.Button(
            btn_row, text="Send to Server", command=self._on_send, state="disabled"
        )
        self.send_btn.pack(side="left", padx=4)
        self.magnet_btn = ttk.Button(
            btn_row, text="Copy Magnet Link", command=self._on_copy_magnet, state="disabled"
        )
        self.magnet_btn.pack(side="left", padx=4)

    def _refresh_tree(self):
        if not hasattr(self, "tree"):
            return
        self.tree.delete(*self.tree.get_children())
        data = scan_library()
        any_items = False

        for label, heading in (("movies", "Movies"), ("tv", "TV")):
            loose, series = data[label]
            if not loose and not series:
                continue
            any_items = True
            root_node = self.tree.insert("", "end", text=heading, open=True)
            self.tree.item(root_node, tags=("category",))

            for f in loose:
                node = self.tree.insert(root_node, "end", text=f.name)
                self.tree.item(node, tags=("file",), values=("file", str(f), "", "", label))

            for series_name, seasons in series.items():
                series_path = LIBRARY_DIR / heading / series_name
                total = sum(len(files) for files in seasons.values())
                series_node = self.tree.insert(
                    root_node, "end", text=f"{series_name} ({total} file{'s' if total != 1 else ''} total)"
                )
                self.tree.item(
                    series_node, tags=("series",),
                    values=("series", str(series_path), series_name, "", label),
                )
                for season_name, files in seasons.items():
                    if season_name == "":
                        for f in files:
                            node = self.tree.insert(series_node, "end", text=f.name)
                            self.tree.item(
                                node, tags=("series_file",),
                                values=("series_file", str(f), series_name, "", label),
                            )
                        continue
                    season_path = series_path / season_name
                    season_node = self.tree.insert(
                        series_node, "end", text=f"{season_name} ({len(files)} file{'s' if len(files) != 1 else ''})"
                    )
                    self.tree.item(
                        season_node, tags=("season",),
                        values=("season", str(season_path), series_name, season_name, label),
                    )
                    for f in files:
                        node = self.tree.insert(season_node, "end", text=f.name)
                        self.tree.item(
                            node, tags=("episode",),
                            values=("episode", str(f), series_name, season_name, label),
                        )

        if not any_items:
            self.tree.insert("", "end", text="(nothing organized yet)")

        self.send_btn.configure(state="disabled")
        self.magnet_btn.configure(state="disabled")
        self.send_details_var.set("Select a file, season, or series to send.")

    def _selected_send_plan(self):
        sel = self.tree.selection()
        if not sel:
            return None
        values = self.tree.item(sel[0], "values")
        if not values:
            return None
        kind, path, series_name, season_name, label = values
        label_disp = "Movies" if label == "movies" else "TV"
        # Series-shaped sends need the media library root on the Deluge
        # host to build their --move-to path; a flat file send doesn't
        # (the Deluge label's own default path handles it).
        remote_root = str(self.config.get("remote_library_root", "")).strip().rstrip("/")
        plex_root = f"{remote_root}/{label_disp}" if remote_root else None
        if plex_root is None and kind != "file":
            return {
                "kind": kind,
                "path": path,
                "cmd": None,
                "detail": (
                    "Series items need the media library root (Settings tab) to "
                    "build their destination path on the Deluge host. Set it, "
                    "save, and reselect. Copy Magnet Link still works."
                ),
            }

        if kind == "file":
            return {
                "kind": kind,
                "path": path,
                "cmd": [str(SYNC_SCRIPT), "--label", label, "--path", path],
                "detail": f"Will send '{Path(path).name}' as a single file → {label_disp}/",
            }
        if kind == "series_file":
            move_to = f"{plex_root}/{series_name}"
            return {
                "kind": kind,
                "path": path,
                "cmd": [
                    str(SYNC_SCRIPT), "--label", label, "--path", path, "--move-to", move_to,
                ],
                "detail": f"Will send '{Path(path).name}' as a single file → {label_disp}/{series_name}/",
            }
        if kind == "episode":
            move_to = f"{plex_root}/{series_name}/{season_name}"
            return {
                "kind": kind,
                "path": path,
                "cmd": [
                    str(SYNC_SCRIPT), "--label", label, "--path", path, "--move-to", move_to,
                ],
                "detail": f"Will send '{Path(path).name}' as a single file → {label_disp}/{series_name}/{season_name}/",
            }
        if kind == "season":
            file_count = len(list(Path(path).glob("*.mkv")))
            move_to = f"{plex_root}/{series_name}"
            return {
                "kind": kind,
                "path": path,
                "cmd": [
                    str(SYNC_SCRIPT), "--label", label, "--path", path, "--move-to", move_to,
                ],
                "detail": (
                    f"Will bundle all {file_count} file(s) of {season_name} into ONE torrent "
                    f"→ {label_disp}/{series_name}/{season_name}/. Safe to repeat for other "
                    f"seasons of this series."
                ),
            }
        if kind == "series":
            file_count = sum(1 for _ in Path(path).rglob("*.mkv"))
            move_to = plex_root
            return {
                "kind": kind,
                "path": path,
                "cmd": [
                    str(SYNC_SCRIPT), "--label", label, "--path", path, "--move-to", move_to,
                ],
                "detail": (
                    f"⚠ Will bundle the ENTIRE series '{series_name}' ({file_count} files, "
                    f"everything under it) into ONE torrent → {label_disp}/ root. Only safe "
                    f"as a one-time full send — sending this series again later risks a "
                    f"duplicate nested folder on the media server. Prefer sending season-by-season "
                    f"if you'll add more later."
                ),
            }
        return None

    def _on_tree_select(self):
        plan = self._selected_send_plan()
        if plan is None:
            self.send_btn.configure(state="disabled")
            self.magnet_btn.configure(state="disabled")
            self.send_details_var.set("Select a file, season, or series to send.")
        else:
            self.send_btn.configure(state="normal" if plan["cmd"] else "disabled")
            self.magnet_btn.configure(state="normal")
            self.send_details_var.set(plan["detail"])

    def _on_send(self):
        plan = self._selected_send_plan()
        if plan is None or plan["cmd"] is None:
            return
        if not str(self.config.get("plex_host", "")).strip():
            messagebox.showerror(
                "Deluge host not set",
                "Set the Deluge Host IP in the Settings tab before sending.",
            )
            return
        if not SYNC_SCRIPT.is_file():
            messagebox.showerror("Missing script", f"Not found: {SYNC_SCRIPT}")
            return
        if plan["kind"] == "series":
            if not messagebox.askyesno(
                "Send whole series?",
                plan["detail"] + "\n\nSend the entire series now?",
            ):
                return
        if not self._ensure_secret():
            self.status_var.set("Cancelled — no password provided.")
            return
        self._run_command(
            plan["cmd"],
            on_done=lambda _rc: self._refresh_tree(),
            extra_env={"BITDVR_PLEX_HOST": self.config["plex_host"]},
        )

    def _on_copy_magnet(self):
        plan = self._selected_send_plan()
        if plan is None:
            return
        if not MAGNET_SCRIPT.is_file():
            messagebox.showerror("Missing script", f"Not found: {MAGNET_SCRIPT}")
            return
        self._run_command(
            [str(MAGNET_SCRIPT), "--path", plan["path"]],
            on_done=self._on_magnet_done,
        )

    def _on_magnet_done(self, rc):
        if rc != 0:
            return
        magnet = next(
            (line.strip() for line in self._captured_lines if line.startswith("magnet:")), None
        )
        if not magnet:
            messagebox.showerror("Copy Magnet Link", "Script finished but no magnet link was found in its output.")
            return
        self.clipboard_clear()
        self.clipboard_append(magnet)
        self.status_var.set("Magnet link copied to clipboard.")
        self._notify("BitDVR", "Magnet link copied to clipboard.")

    # ------------------------------------------------------------- Clean tab

    def _build_clean_tab(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Button(top, text="Refresh", command=self._refresh_clean_tree).pack(side="right")

        ttk.Label(
            parent,
            text="Files already sent (archived under Library/.sent/). "
                 "Removing one deletes the local copy on this machine and stops "
                 "seeding it, if it's still active — the media server's copy is untouched.",
            foreground="#888888", wraplength=680, justify="left",
        ).pack(fill="x", padx=8, pady=(4, 0))

        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=8)
        self.clean_tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse", height=10)
        cscroll = ttk.Scrollbar(tree_frame, command=self.clean_tree.yview)
        self.clean_tree.configure(yscrollcommand=cscroll.set)
        self.clean_tree.pack(side="left", fill="both", expand=True)
        cscroll.pack(side="right", fill="y")
        self.clean_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_clean_tree_select())

        self.clean_details_var = tk.StringVar(value="Select a file, season, or series to remove.")
        ttk.Label(
            parent, textvariable=self.clean_details_var, wraplength=680, justify="left"
        ).pack(fill="x", padx=8)

        self.clean_remove_btn = ttk.Button(
            parent, text="Remove", command=self._on_remove_sent, state="disabled"
        )
        self.clean_remove_btn.pack(pady=8)

    def _refresh_clean_tree(self):
        if not hasattr(self, "clean_tree"):
            return
        self.clean_tree.delete(*self.clean_tree.get_children())
        any_items = False

        for label, heading in (("movies", "Movies"), ("tv", "TV")):
            loose, series = scan_library_root(SENT_DIR / heading)
            if not loose and not series:
                continue
            any_items = True
            root_node = self.clean_tree.insert("", "end", text=heading, open=True)
            self.clean_tree.item(root_node, tags=("category",))

            for f in loose:
                node = self.clean_tree.insert(root_node, "end", text=f.name)
                self.clean_tree.item(node, tags=("file",), values=("file", str(f)))

            for series_name, seasons in series.items():
                series_path = SENT_DIR / heading / series_name
                total = sum(len(files) for files in seasons.values())
                series_node = self.clean_tree.insert(
                    root_node, "end", text=f"{series_name} ({total} file{'s' if total != 1 else ''} total)"
                )
                self.clean_tree.item(series_node, tags=("series",), values=("series", str(series_path)))
                for season_name, files in seasons.items():
                    if season_name == "":
                        for f in files:
                            node = self.clean_tree.insert(series_node, "end", text=f.name)
                            self.clean_tree.item(node, tags=("file",), values=("file", str(f)))
                        continue
                    season_path = series_path / season_name
                    season_node = self.clean_tree.insert(
                        series_node, "end", text=f"{season_name} ({len(files)} file{'s' if len(files) != 1 else ''})"
                    )
                    self.clean_tree.item(season_node, tags=("season",), values=("season", str(season_path)))
                    for f in files:
                        node = self.clean_tree.insert(season_node, "end", text=f.name)
                        self.clean_tree.item(node, tags=("file",), values=("file", str(f)))

        if not any_items:
            self.clean_tree.insert("", "end", text="(nothing sent yet)")

        self.clean_remove_btn.configure(state="disabled")
        self.clean_details_var.set("Select a file, season, or series to remove.")

    def _on_clean_tree_select(self):
        sel = self.clean_tree.selection()
        values = self.clean_tree.item(sel[0], "values") if sel else None
        if not values:
            self.clean_remove_btn.configure(state="disabled")
            self.clean_details_var.set("Select a file, season, or series to remove.")
            return
        kind, path = values
        p = Path(path)
        if kind == "file":
            size = p.stat().st_size if p.is_file() else 0
            detail = f"Will permanently delete '{p.name}' ({size / 1_048_576:.0f} MiB)."
        else:
            files = list(p.rglob("*.mkv"))
            total_size = sum(f.stat().st_size for f in files)
            detail = (
                f"Will permanently delete all {len(files)} file(s) under '{p.name}' "
                f"({total_size / 1_048_576:.0f} MiB total)."
            )
        self.clean_remove_btn.configure(state="normal")
        self.clean_details_var.set(detail)

    def _on_remove_sent(self):
        sel = self.clean_tree.selection()
        values = self.clean_tree.item(sel[0], "values") if sel else None
        if not values:
            return
        kind, path = values
        p = Path(path)
        if not messagebox.askyesno(
            "Remove sent files?",
            f"Permanently delete this {'file' if kind == 'file' else kind}?\n\n{p}\n\n"
            "This cannot be undone. Your media server's copy is not affected.",
        ):
            return

        files = [p] if kind == "file" else sorted(p.rglob("*.mkv"))
        for f in files:
            self._stop_seeding_file(f)
        try:
            if kind == "file":
                p.unlink()
            else:
                shutil.rmtree(p)
        except OSError as e:
            messagebox.showerror("Remove failed", f"Could not remove:\n\n{e}")
            return

        # Clean up now-empty parent dirs, same as bitdvr-sync.sh does on
        # archive — stop at Library/.sent/Movies or Library/.sent/TV.
        parent = p.parent
        while parent not in (SENT_DIR / "Movies", SENT_DIR / "TV") and SENT_DIR in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

        self.status_var.set(f"Removed {p.name}")
        self._refresh_clean_tree()

    @staticmethod
    def _stop_seeding_file(file_path):
        """Removes any matching watch-dir entry for file_path's basename,
        if the seed daemon is still tracking it — same manual step
        bitdvr-sync.sh's own output already tells you to run by hand."""
        torrent_name = file_path.name + ".torrent"
        (SEED_WATCH_DIR / torrent_name).unlink(missing_ok=True)
        (SEED_WATCH_DIR / (torrent_name + ".json")).unlink(missing_ok=True)

    # ------------------------------------------------------- shared plumbing

    def _append_log(self, line, file=None):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        target = file if file is not None else self.log_file
        if target:
            target.write(line)
            target.flush()

    def _ensure_secret(self):
        return (
            self._get_or_prompt_secret(
                SECRET_SERVICE,
                "deluge-web",
                "BitDVR deluge-web",
                "deluge-web password (stored in the login keyring):",
            )
            is not None
        )

    def _get_or_prompt_secret(self, service, account, keyring_label, prompt_text):
        """Looks up service/account in the login keyring, prompting once (and
        storing) if it isn't there yet. Returns the secret string, or None if
        the user cancelled the prompt or storing failed."""
        secret = secret_lookup(service, account)
        if secret is not None:
            return secret

        pw = self._prompt_password(prompt_text)
        if not pw:
            return None
        return pw if secret_store(service, account, keyring_label, pw) else None

    def _prompt_password(self, prompt_text):
        dialog = tk.Toplevel(self)
        dialog.title("Enter Password")
        dialog.transient(self)
        dialog.grab_set()
        # Don't wait for the periodic tick — a password dialog must
        # never be readable in a capture, even for a moment.
        if IS_MACOS and self.config.get("hide_from_capture", True):
            self.update_idletasks()
            _macos_apply_capture_hiding(True)
        ttk.Label(dialog, text=prompt_text).pack(padx=12, pady=(12, 4))
        pw_var = tk.StringVar()
        entry = ttk.Entry(dialog, textvariable=pw_var, show="*", width=32)
        entry.pack(padx=12, pady=4)
        entry.focus_set()

        result = {}

        def on_ok(_event=None):
            result["pw"] = pw_var.get()
            dialog.destroy()

        entry.bind("<Return>", on_ok)
        btns = ttk.Frame(dialog)
        btns.pack(pady=(4, 12))
        ttk.Button(btns, text="OK", command=on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)

        dialog.wait_window()
        return result.get("pw")

    def _run_command(self, cmd, on_done=None, extra_env=None):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"bitdvr-{datetime.now():%Y%m%d-%H%M%S}.log"
        self.log_file = open(log_path, "w")
        self.on_done = on_done
        self._captured_lines = []

        self._append_log(f"$ {' '.join(cmd)}\n\n")
        self.status_var.set("Running...")

        env = {**os.environ, **extra_env} if extra_env else None
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            env=env,
        )
        threading.Thread(target=self._read_output, daemon=True).start()
        self.after(100, self._poll_queue)

    def _read_output(self):
        for line in self.proc.stdout:
            self.log_queue.put(line)
        self.proc.wait()
        self.log_queue.put(None)

    def _poll_queue(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                if line is None:
                    self._on_finished()
                    return
                self._captured_lines.append(line)
                self._append_log(line)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _on_finished(self):
        rc = self.proc.returncode
        if self.log_file:
            self.log_file.close()
            self.log_file = None
        if rc == 0:
            self.status_var.set("Done ✓")
            self._notify("BitDVR", "Completed.")
        else:
            self.status_var.set(f"Failed (exit {rc}) — see log")
            self._notify("BitDVR", f"Failed (exit {rc}) — see log")
        if self.on_done:
            self.on_done(rc)
            self.on_done = None

    @staticmethod
    def _notify(title, body):
        # Title/body go through as osascript run-handler arguments rather
        # than being spliced into the AppleScript source, so free-text
        # titles can't break (or inject into) the script.
        if IS_MACOS:
            cmd = [
                "osascript", "-e",
                "on run argv\n"
                "display notification (item 2 of argv) with title (item 1 of argv)\n"
                "end run",
                title, body,
            ]
        else:
            cmd = ["notify-send", title, body]
        try:
            subprocess.run(cmd, check=False)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    App().mainloop()
