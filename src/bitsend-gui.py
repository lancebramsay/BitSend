#!/usr/bin/env python3
# BitSend — GUI wrapper around bitsend-music.sh, meant to be installed to
# ~/.local/bin alongside copies of the scripts (see install.sh) and pinned
# to the taskbar as its own app. Sends albums to a media server on another
# machine over a private BitTorrent swarm instead of a network share.
#
#   Send — browse the <Artist>/<Album> music folder and send an album, an
#     artist, or everything. Each album is its own torrent.
#   Clean — delete archived local copies of what's already been sent.
#   Settings — the Deluge host, the library root on it, and the music
#     folder, persisted to ~/.config/bitsend/config.json (not the keyring —
#     these aren't secret). Also holds the "Change Password" action for the
#     keyring secret, since the normal prompt flow only fires when nothing
#     is stored yet. The Deluge host is threaded through to the scripts via
#     the BITSEND_PLEX_HOST env var, matching the env override they accept
#     standalone.
#
# Sends run bitsend-music.sh as a subprocess and stream its stdout/stderr
# into a scrolling log pane live, while also writing each run to a
# timestamped file under ~/.local/share/bitsend/logs/. A send stays running
# until the media server confirms delivery. The deluge-web keyring password
# is checked/collected *before* launching a send, since a GUI child process
# has no TTY for the script's own `read -s` prompt.

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

SCRIPT_DIR = Path(__file__).resolve().parent

MUSIC_SCRIPT = SCRIPT_DIR / "bitsend-music.sh"
IS_MACOS = sys.platform == "darwin"
DEFAULT_MUSIC_DIR = Path.home() / "Rips" / "staging"
LOG_DIR = Path.home() / ".local" / "share" / "bitsend" / "logs"
CONFIG_DIR = Path.home() / ".config" / "bitsend"
CONFIG_FILE = CONFIG_DIR / "config.json"
SECRET_TOOL = "secret-tool"
SECRET_SERVICE = "bitsend-deluge-web"
AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aiff"}
DEFAULT_CONFIG = {
    "plex_host": "",
    "remote_library_root": "",
    "music_source": "",  # "" means DEFAULT_MUSIC_DIR
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








def audio_files(directory):
    return sorted(
        p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )


def scan_music_root(root_dir):
    """Returns {artist_name: [album_path]} for a <Artist>/<Album> tree. An
    album counts if there's audio anywhere under it, so multi-disc sets
    with Disc 1/Disc 2 subfolders stay one album."""
    artists = {}
    if not root_dir.is_dir():
        return artists
    for artist in sorted(p for p in root_dir.iterdir() if p.is_dir()):
        albums = [p for p in sorted(artist.iterdir()) if p.is_dir() and audio_files(p)]
        if albums:
            artists[artist.name] = albums
    return artists


def music_source(config):
    configured = str(config.get("music_source", "")).strip()
    return Path(configured).expanduser() if configured else DEFAULT_MUSIC_DIR


def sent_dir(config):
    """Must match SENT_DIR in bitsend-music.sh, which also resolves symlinks
    (on Bazzite, /home is itself one)."""
    return music_source(config).resolve().parent / "sent"


class App(tk.Tk):
    def __init__(self):
        # className sets this window's WM_CLASS to "bitsend" — without it,
        # Tkinter defaults to something derived from the interpreter
        # (bitsend-gui.py runs as python3, since that's the shebang
        # interpreter actually exec'd), which KDE's taskbar can't match
        # back to bitsend.desktop, so it falls back to a generic Python
        # icon instead of the entry's own. Paired with
        # StartupWMClass=bitsend in the .desktop file itself.
        super().__init__(className="bitsend")
        self.title("BitSend")
        self.geometry("760x640")
        self.minsize(620, 480)

        self.log_queue = queue.Queue()
        self.proc = None
        self.log_file = None
        self.on_done = None

        self.config = load_config()

        notebook = ttk.Notebook(self)
        notebook.pack(fill="x", padx=8, pady=(8, 0))
        self.notebook = notebook

        send_tab = ttk.Frame(notebook)
        clean_tab = ttk.Frame(notebook)
        settings_tab = ttk.Frame(notebook)
        notebook.add(send_tab, text="Send")
        notebook.add(clean_tab, text="Clean")
        notebook.add(settings_tab, text="Settings")
        notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.settings_tab = settings_tab

        self._build_send_tab(send_tab)
        self._build_clean_tab(clean_tab)
        self._build_settings_tab(settings_tab)

        log_frame = ttk.Frame(self)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(8, 6))
        self.log_frame = log_frame
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

        self._refresh_tree()
        self._refresh_clean_tree()
        self._on_tab_changed()

    def _on_tab_changed(self, _event=None):
        self._refresh_tree()
        self._refresh_clean_tree()
        # Settings runs no jobs, so its tab drops the log pane and lets
        # the (scrollable) settings content have that space instead.
        if not hasattr(self, "log_frame"):
            return
        if str(self.notebook.select()) == str(self.settings_tab):
            self.log_frame.pack_forget()
            self.notebook.pack_configure(fill="both", expand=True, pady=(8, 6))
        elif not self.log_frame.winfo_ismapped():
            self.notebook.pack_configure(fill="x", expand=False, pady=(8, 0))
            self.log_frame.pack(fill="both", expand=True, padx=8, pady=(8, 6))

    # --------------------------------------------------------------- Settings

    def _make_settings_scrollable(self, tab):
        """Wrap the Settings tab in a vertically scrolling canvas and
        return the frame to build its content into. The tab has outgrown
        small windows, and the notebook sizes itself to its tallest tab —
        so an overflowing Settings tab was also stealing height from the
        log pane below on every other tab. The canvas caps what Settings
        asks for and makes the rest reachable by scrollbar or wheel."""
        canvas = tk.Canvas(tab, borderwidth=0, highlightthickness=0, height=340)
        try:
            canvas.configure(bg=ttk.Style().lookup("TFrame", "background"))
        except tk.TclError:
            pass
        bar = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window, width=e.width),
        )
        # yscrollincrement=1 makes yview_scroll's "units" mean pixels,
        # so both notchy wheels and pixel-precise trackpad deltas map
        # onto the same call.
        canvas.configure(yscrollincrement=1)
        self.settings_canvas = canvas
        # Scroll events are delivered to the widget under the pointer —
        # in this tab that's usually a child label or entry, never the
        # canvas itself — so catch them at the "all" bindtag and scroll
        # only when the target sits inside this canvas.
        self.bind_all("<MouseWheel>", self._on_settings_wheel)
        self.bind_all("<Button-4>", self._on_settings_wheel)  # X11 wheel up
        self.bind_all("<Button-5>", self._on_settings_wheel)  # X11 wheel down
        # Tk 9 splits trackpad gestures out of <MouseWheel> into their
        # own event, with both axes packed into one delta word.
        try:
            self.bind_all("<TouchpadScroll>", self._on_settings_touchpad)
        except tk.TclError:  # Tk 8.6 (Linux distros) has no such event
            pass
        return inner

    def _settings_scroll_hit(self, event):
        """True when the event's target widget sits inside the settings
        canvas (event.widget can be a bare path string for internals
        like a combobox popdown — those never match, which is right)."""
        widget = event.widget
        while widget is not None:
            if widget is self.settings_canvas:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _on_settings_wheel(self, event):
        if not self._settings_scroll_hit(event):
            return
        if event.num == 4:  # X11 encodes direction in the button number
            notches = 1
        elif event.num == 5:
            notches = -1
        else:
            # Tk 9 normalizes wheel deltas to multiples of 120 on every
            # platform; older aqua Tk sent raw line counts.
            delta = event.delta
            notches = delta / 120 if abs(delta) >= 120 else delta
        self.settings_canvas.yview_scroll(round(-notches * 40), "units")

    def _on_settings_touchpad(self, event):
        if not self._settings_scroll_hit(event):
            return
        # Same decode and direction Tk 9's own widget bindings use.
        _dx, dy = self.tk.call("::tk::PreciseScrollDeltas", event.delta)
        if dy:
            self.settings_canvas.yview_scroll(-int(dy), "units")

    def _build_settings_tab(self, tab):
        parent = self._make_settings_scrollable(tab)
        self.set_plex_host_var = tk.StringVar(value=self.config["plex_host"])
        self.set_remote_root_var = tk.StringVar(
            value=self.config.get("remote_library_root", DEFAULT_CONFIG["remote_library_root"])
        )
        self.set_music_source_var = tk.StringVar(value=self.config.get("music_source", ""))

        frm = ttk.Frame(parent)
        frm.pack(fill="x", padx=8, pady=8)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Deluge Host IP:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.set_plex_host_var).grid(
            row=0, column=1, columnspan=2, sticky="ew", padx=6
        )
        ttk.Label(frm, text="Media library root (on Deluge host):").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Entry(frm, textvariable=self.set_remote_root_var).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=6, pady=(6, 0)
        )
        ttk.Label(
            frm,
            text="Path on the Deluge/media-server box containing Music/ (e.g. "
                 "/mnt/media) — every send builds its destination from it.",
            foreground="#888888", wraplength=680, justify="left",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))
        ttk.Label(frm, text="Music folder:").grid(
            row=3, column=0, sticky="w", pady=(10, 0)
        )
        ttk.Entry(frm, textvariable=self.set_music_source_var).grid(
            row=3, column=1, sticky="ew", padx=6, pady=(10, 0)
        )
        ttk.Button(frm, text="Browse...", command=self._on_browse_music_source).grid(
            row=3, column=2, pady=(10, 0)
        )
        ttk.Label(
            frm,
            text="The <Artist>/<Album> tree to send from, such as a CD ripper's "
                 f"output folder. Blank uses {DEFAULT_MUSIC_DIR}. Sent albums move "
                 "to a folder called sent beside it.",
            foreground="#888888", wraplength=680, justify="left",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(2, 0))

        ttk.Button(parent, text="Save", command=self._on_settings_save).pack(pady=(4, 12))

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=8)

        pw_frm = ttk.Frame(parent)
        pw_frm.pack(fill="x", padx=8, pady=12)
        ttk.Label(pw_frm, text="Stored password (in the login keyring):").pack(
            anchor="w", pady=(0, 6)
        )
        ttk.Button(
            pw_frm, text="Change Deluge-web Password...",
            command=self._on_change_deluge_password,
        ).pack(anchor="w", pady=2)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=8)

        export_frm = ttk.Frame(parent)
        export_frm.pack(fill="x", padx=8, pady=12)
        ttk.Label(
            export_frm,
            text="Settings file (hosts and paths — passwords never leave the keyring):",
        ).pack(anchor="w", pady=(0, 6))
        export_btn_row = ttk.Frame(export_frm)
        export_btn_row.pack(anchor="w")
        ttk.Button(
            export_btn_row, text="Export Settings...", command=self._on_export_config,
        ).pack(side="left", padx=(0, 4))
        ttk.Button(
            export_btn_row, text="Import Settings...", command=self._on_import_config,
        ).pack(side="left")

    def _on_browse_music_source(self):
        path = filedialog.askdirectory(initialdir=str(Path.home()))
        if path:
            self.set_music_source_var.set(path)

    def _on_export_config(self):
        path = filedialog.asksaveasfilename(
            title="Export BitSend Settings",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="bitsend-config.json",
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
            title="Import BitSend Settings",
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
            messagebox.showerror("Import failed", "That file doesn't look like a BitSend settings export.")
            return

        self.config.update({k: data[k] for k in DEFAULT_CONFIG if k in data})
        save_config(self.config)
        self.set_plex_host_var.set(self.config["plex_host"])
        self.set_remote_root_var.set(self.config.get("remote_library_root", ""))
        self.set_music_source_var.set(self.config.get("music_source", ""))
        self._refresh_tree()
        self._refresh_clean_tree()
        messagebox.showinfo("Import Settings", "Settings imported and applied.")

    def _on_settings_save(self):
        self.config = {
            "plex_host": self.set_plex_host_var.get().strip(),
            "remote_library_root": self.set_remote_root_var.get().strip(),
            "music_source": self.set_music_source_var.get().strip(),
        }
        save_config(self.config)
        self._refresh_tree()
        self._refresh_clean_tree()
        messagebox.showinfo("Saved", "Settings saved.")

    def _on_change_deluge_password(self):
        self._change_secret(
            SECRET_SERVICE, "deluge-web", "BitSend deluge-web",
            "New deluge-web password:",
        )

    def _change_secret(self, service, account, keyring_label, prompt_text):
        secret_clear(service, account)
        pw = self._get_or_prompt_secret(service, account, keyring_label, prompt_text)
        if pw is not None:
            messagebox.showinfo("Password updated", f"{keyring_label} password updated in the keyring.")
        else:
            messagebox.showinfo("Unchanged", "No new password was stored.")

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

        self.send_details_var = tk.StringVar(value="Select an album to send.")
        ttk.Label(
            parent, textvariable=self.send_details_var, wraplength=680, justify="left"
        ).pack(fill="x", padx=8)

        self.send_btn = ttk.Button(
            parent, text="Send to Server", command=self._on_send, state="disabled"
        )
        self.send_btn.pack(pady=8)

    def _refresh_tree(self):
        if not hasattr(self, "tree"):
            return
        self.tree.delete(*self.tree.get_children())
        music = scan_music_root(music_source(self.config))

        if music:
            total = sum(len(albums) for albums in music.values())
            all_node = self.tree.insert(
                "", "end", text=f"All music ({total} album{'s' if total != 1 else ''})", open=True
            )
            self.tree.item(all_node, values=("all", ""))
            for artist, albums in music.items():
                artist_node = self.tree.insert(all_node, "end", text=artist, open=True)
                self.tree.item(artist_node, values=("artist", artist))
                for album in albums:
                    node = self.tree.insert(artist_node, "end", text=album.name)
                    self.tree.item(node, values=("album", artist, str(album)))
        else:
            self.tree.insert("", "end", text=f"(no albums in {music_source(self.config)})")

        self.send_btn.configure(state="disabled")
        self.send_details_var.set("Select an album to send.")

    def _selected_send_plan(self):
        sel = self.tree.selection()
        if not sel:
            return None
        values = self.tree.item(sel[0], "values")
        if not values:
            return None
        kind, artist = values[0], values[1]
        # The library root on the Deluge host is how the destination is
        # built, and how delivery gets confirmed.
        remote_root = str(self.config.get("remote_library_root", "")).strip().rstrip("/")
        if not remote_root:
            return {
                "kind": kind,
                "cmd": None,
                "detail": (
                    "Sends need the media library root (Settings tab) to build their "
                    "destination path on the Deluge host. Set it, save, and reselect."
                ),
            }

        source = music_source(self.config)
        cmd = [
            str(MUSIC_SCRIPT), "--yes", "--source", str(source),
            "--remote-root", remote_root,
        ]
        if kind == "album":
            path = values[2]
            return {
                "kind": kind,
                "cmd": cmd + [path],
                "detail": f"Will send '{Path(path).name}' → Music/{artist}/",
            }
        if kind == "artist":
            albums = scan_music_root(source).get(artist, [])
            return {
                "kind": kind,
                "cmd": cmd + [str(album) for album in albums],
                "detail": f"Will send {len(albums)} album(s), one torrent each → Music/{artist}/",
            }
        total = sum(len(albums) for albums in scan_music_root(source).values())
        return {
            "kind": kind,
            "cmd": cmd,
            "detail": (
                f"Will send all {total} album(s), one torrent each → Music/<Artist>/. "
                "Every album is checked before the first one is sent."
            ),
        }

    def _on_tree_select(self):
        plan = self._selected_send_plan()
        if plan is None:
            self.send_btn.configure(state="disabled")
            self.send_details_var.set("Select an album to send.")
        else:
            self.send_btn.configure(state="normal" if plan["cmd"] else "disabled")
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
        if not MUSIC_SCRIPT.is_file():
            messagebox.showerror("Missing script", f"Not found: {MUSIC_SCRIPT}")
            return
        if plan["kind"] != "album":
            if not messagebox.askyesno("Send albums?", plan["detail"] + "\n\nSend now?"):
                return
        if not self._ensure_secret():
            self._append_log("==> Send cancelled — no password provided.\n")
            return
        self._run_command(
            plan["cmd"],
            on_done=lambda _rc: (self._refresh_tree(), self._refresh_clean_tree()),
            extra_env={"BITSEND_PLEX_HOST": self.config["plex_host"]},
        )

    # ------------------------------------------------------------- Clean tab

    def _build_clean_tab(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Button(top, text="Refresh", command=self._refresh_clean_tree).pack(side="right")

        ttk.Label(
            parent,
            text="Albums already sent, kept in the sent folder beside your music folder. "
                 "Removing one deletes the local copy on this machine — the media "
                 "server's copy is untouched, but BitSend will then let you send that "
                 "album again.",
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

        self.clean_details_var = tk.StringVar(value="Select an album or artist to remove.")
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
        root = sent_dir(self.config)
        music = scan_music_root(root)

        for artist, albums in music.items():
            artist_node = self.clean_tree.insert("", "end", text=artist, open=True)
            self.clean_tree.item(artist_node, values=(str(root / artist),))
            for album in albums:
                node = self.clean_tree.insert(artist_node, "end", text=album.name)
                self.clean_tree.item(node, values=(str(album),))
        if not music:
            self.clean_tree.insert("", "end", text="(nothing sent yet)")

        self.clean_remove_btn.configure(state="disabled")
        self.clean_details_var.set("Select an album or artist to remove.")

    def _on_clean_tree_select(self):
        sel = self.clean_tree.selection()
        values = self.clean_tree.item(sel[0], "values") if sel else None
        if not values:
            self.clean_remove_btn.configure(state="disabled")
            self.clean_details_var.set("Select an album or artist to remove.")
            return
        p = Path(values[0])
        files = audio_files(p)
        total_size = sum(f.stat().st_size for f in files)
        self.clean_remove_btn.configure(state="normal")
        self.clean_details_var.set(
            f"Will permanently delete {len(files)} file(s) under '{p.name}' "
            f"({total_size / 1_048_576:.0f} MiB total)."
        )

    def _on_remove_sent(self):
        sel = self.clean_tree.selection()
        values = self.clean_tree.item(sel[0], "values") if sel else None
        if not values:
            return
        p = Path(values[0])
        if not messagebox.askyesno(
            "Remove sent files?",
            f"Permanently delete this?\n\n{p}\n\n"
            "This cannot be undone. Your media server's copy is not affected.",
        ):
            return
        try:
            shutil.rmtree(p)
        except OSError as e:
            messagebox.showerror("Remove failed", f"Could not remove:\n\n{e}")
            return
        # Clean up an artist folder left empty by removing its last album.
        if p.parent != sent_dir(self.config):
            try:
                p.parent.rmdir()
            except OSError:
                pass

        self._append_log(f"==> Removed {p.name}.\n")
        self._refresh_clean_tree()

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
                "BitSend deluge-web",
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
        # Sends stay running until delivery is confirmed, and output goes
        # to one shared log pane and log file.
        if self.proc is not None and self.proc.poll() is None:
            messagebox.showinfo("Busy", "Wait for the current job to finish first.")
            return
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"bitsend-{datetime.now():%Y%m%d-%H%M%S}.log"
        self.log_file = open(log_path, "w")
        self.on_done = on_done

        self._append_log(f"$ {' '.join(cmd)}\n\n")

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
            self._notify("BitSend", "Completed.")
        else:
            self._notify("BitSend", f"Failed (exit {rc}) — see log")
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
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    App().mainloop()
