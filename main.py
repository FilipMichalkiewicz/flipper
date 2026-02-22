"""
Flipper — MAC Address Scanner + IPTV Player
Plain Tkinter — Windows compatible.
Features: mpv embedded player, proxy rotation with full retry,
session persistence, profiles with naming, channel search,
navigation stack, progress bar, account info tab, settings tab,
channel count filter, channel cache.
"""

import os
import sys
import platform
import shutil
import subprocess
import ctypes
from pathlib import Path
from typing import Optional

_WIN_DLL_HANDLES = []

import tkinter as tk
from tkinter import ttk, filedialog, simpledialog
import threading
import time
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor


def _get_flipper_data_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            path = os.path.join(base, "Flipper")
            os.makedirs(path, exist_ok=True)
            return path
    path = os.path.join(str(Path.home()), ".flipper")
    os.makedirs(path, exist_ok=True)
    return path


def _get_flipper_mpv_dir() -> str:
    path = os.path.join(_get_flipper_data_dir(), "mpv")
    os.makedirs(path, exist_ok=True)
    return path


def _copy_mpv_dll_to_runtime_dir() -> Optional[str]:
    target_dir = _get_flipper_mpv_dir()
    for dll_name in ("libmpv-2.dll", "libmpv.dll"):
        # Prefer stable sources first; _MEIPASS only as last-resort fallback.
        candidate_paths = []
        candidate_paths.append(os.path.join(target_dir, dll_name))
        candidate_paths.append(
            os.path.join(_get_flipper_data_dir(), dll_name)
        )
        candidate_paths.append(os.path.join(os.path.dirname(__file__), dll_name))

        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidate_paths.append(os.path.join(meipass, dll_name))

        for src in candidate_paths:
            if not os.path.isfile(src):
                continue
            dst = os.path.join(target_dir, dll_name)
            try:
                if (not os.path.exists(dst) or
                        os.path.getsize(src) != os.path.getsize(dst)):
                    shutil.copy2(src, dst)
                return target_dir
            except Exception:
                continue
    return None

def _prepend_to_path(path: str):
    if not path:
        return
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    if path not in parts:
        os.environ["PATH"] = path + os.pathsep + current


def _add_windows_dll_directory(path: str):
    if sys.platform != "win32" or not path:
        return
    add_dir = getattr(os, "add_dll_directory", None)
    if not add_dir:
        return
    try:
        handle = add_dir(path)
        _WIN_DLL_HANDLES.append(handle)
    except Exception:
        pass


def _is_mpv_dll_loadable() -> bool:
    for dll_name in ("libmpv-2.dll", "libmpv.dll"):
        try:
            ctypes.CDLL(dll_name)
            return True
        except OSError:
            pass
    return False


def _find_mpv_dll_dir() -> Optional[str]:
    candidates = []

    # Stable runtime location first (avoids Temp onefile extraction path)
    candidates.append(_get_flipper_mpv_dir())
    candidates.append(_get_flipper_data_dir())

    mpv_bin = shutil.which("mpv")
    if mpv_bin:
        candidates.append(os.path.dirname(mpv_bin))

    local_mpv_dir = os.path.join(os.path.dirname(__file__), ".mpv")
    candidates.append(local_mpv_dir)

    path_env = os.environ.get("PATH", "")
    if path_env:
        candidates.extend(path_env.split(os.pathsep))

    seen = set()
    for directory in candidates:
        if not directory or directory in seen:
            continue
        seen.add(directory)
        for dll_name in ("libmpv-2.dll", "libmpv.dll"):
            if os.path.isfile(os.path.join(directory, dll_name)):
                return directory
    return None


def _find_mpv_dll_under(root: str, max_depth: int = 5) -> Optional[str]:
    root_path = Path(root)
    if not root_path.exists():
        return None

    for current_root, dirs, files in os.walk(root):
        rel = Path(current_root).relative_to(root_path)
        if len(rel.parts) > max_depth:
            dirs[:] = []
            continue
        if "libmpv-2.dll" in files or "libmpv.dll" in files:
            return current_root
    return None


def _try_install_mpv_with_winget() -> bool:
    winget = shutil.which("winget")
    if not winget:
        return False

    candidate_ids = [
        "shinchiro.mpv",
        "MPV.MPV",
        "mpv.mpv",
    ]

    for package_id in candidate_ids:
        try:
            proc = subprocess.run(
                [
                    winget,
                    "install",
                    "--id",
                    package_id,
                    "-e",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                    "--silent",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=180,
            )
            if proc.returncode == 0:
                return True
        except Exception:
            continue
    return False


def _ensure_mpv_runtime_windows():
    if sys.platform != "win32":
        return

    runtime_dir = _copy_mpv_dll_to_runtime_dir()
    if runtime_dir:
        _add_windows_dll_directory(runtime_dir)
        _prepend_to_path(runtime_dir)
        if _is_mpv_dll_loadable():
            return

    if _is_mpv_dll_loadable():
        return

    dll_dir = _find_mpv_dll_dir()
    if dll_dir:
        _add_windows_dll_directory(dll_dir)
        _prepend_to_path(dll_dir)
        if _is_mpv_dll_loadable():
            return

    installed = _try_install_mpv_with_winget()
    if not installed:
        return

    search_roots = [
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("ProgramFiles", ""),
        os.environ.get("ProgramFiles(x86)", ""),
    ]
    for root in search_roots:
        if not root:
            continue
        found = _find_mpv_dll_under(root)
        if found:
            _add_windows_dll_directory(found)
            _prepend_to_path(found)
            break


_ensure_mpv_runtime_windows()

try:
    import mpv
    HAS_MPV = True
except (ImportError, OSError):
    HAS_MPV = False

from scanner import (
    generate_random_mac, check_mac, get_responding_endpoint, parse_url,
    get_handshake, get_genres, get_channels, get_stream_url,
    fetch_free_proxies, set_proxy_list, get_proxy_list, add_proxy,
    remove_proxy, get_current_proxy, rotate_proxy, report_proxy_fail,
    report_proxy_success, should_remove_proxy, make_cookies, make_params,
    random_user_agent, _request_get, count_channels_quick,
)
from constants import RESULTS_FILE, SESSION_FILE

MAX_LOG_SAVE = 500
CONFIG_FILE = "config.ini"
CHANNELS_CACHE_FILE = "channels_cache.json"
BG_DARK = "#0a0a1e"
BG_SIDEBAR = "#1a1a2e"
BG_INPUT = "#12122a"
BG_BAR = "#16162a"
FG_DIM = "#888888"
ACCENT = "#2563eb"
MAX_PROXY_RETRIES = 15


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Flipper — MAC Scanner & Player")
        self.root.geometry("1300x800")
        self.root.minsize(1050, 650)

        # ── State ──────────────────────────────────────────
        self.is_running = False
        self.is_paused = False
        self.scan_thread = None
        self.executor = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()

        self.checked_count = 0
        self.found_count = 0
        self.active_macs = []          # [{url, mac, expiry, proxy}, ...]
        self.mac_proxy_map = {}        # {mac: proxy_str}
        self.profiles = []             # [{name, mac, url, proxy}, ...]
        self.active_profile = None     # currently selected profile dict
        self.log_history = []          # [(full_msg, tag), ...]

        # Player state
        self.player_token = None
        self.player_channels = []
        self.player_genres = []
        self.player_content_type = "itv"
        self.mpv_player = None
        self.current_tab = 0
        self.current_stream_url = None

        # Navigation stack
        self.nav_stack = []

        # Keep on top
        self.keep_on_top_var = tk.BooleanVar(value=False)

        # Settings
        self.verbose_logs_var = tk.BooleanVar(value=False)
        self.use_proxy_var = tk.BooleanVar(value=True)
        self.player_use_proxy_var = tk.BooleanVar(value=True)
        self.min_channels = 0
        self.save_folder = _get_flipper_data_dir()

        # Account info
        self.account_info_text = ""

        self._setup_styles()
        self._build_gui()
        self._load_session()
        self._auto_fetch_proxies_on_startup()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Timeout helper ─────────────────────────────────────
    def _get_timeout(self):
        try:
            return int(self.timeout_entry.get().strip())
        except (ValueError, AttributeError):
            return 5

    # ── Styles ─────────────────────────────────────────────
    def _setup_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview",
                        background="#1e1e3a", foreground="#d0d0e8",
                        fieldbackground="#1e1e3a", rowheight=26,
                        font=("Menlo", 11))
        style.configure("Treeview.Heading",
                        background="#2a2a4a", foreground="#ffffff",
                        font=("Menlo", 11, "bold"))
        style.map("Treeview", background=[("selected", ACCENT)])
        style.configure("green.Horizontal.TProgressbar",
                        troughcolor="#1e1e3a",
                        background="#00b359",
                        darkcolor="#009945",
                        lightcolor="#00ff88",
                        bordercolor="#333355")

    # ══════════════════════════════════════════════════════
    #  BUILD GUI
    # ══════════════════════════════════════════════════════

    def _build_gui(self):
        # LEFT sidebar container
        self.left = tk.Frame(self.root, width=270, bg=BG_SIDEBAR,
                             highlightthickness=0, bd=0)
        self.left.pack(side=tk.LEFT, fill=tk.Y)
        self.left.pack_propagate(False)

        # Two sidebar modes
        self.sidebar_scanner = tk.Frame(self.left, bg=BG_SIDEBAR)
        self.sidebar_scanner.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.sidebar_player = tk.Frame(self.left, bg=BG_SIDEBAR)
        self.sidebar_player.place(relx=0, rely=0, relwidth=1, relheight=1)

        self._build_sidebar_scanner(self.sidebar_scanner)
        self._build_sidebar_player(self.sidebar_player)

        # RIGHT main area
        right = tk.Frame(self.root, bg="#0f0f23")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Tab bar
        tab_bar = tk.Frame(right, bg=BG_BAR)
        tab_bar.pack(fill=tk.X)

        self.tab_btns = []
        self.tab_pages = []
        tab_labels = ["📋 Logi", "✅ Aktywne MAC", "🌐 Proxy",
                       "📺 Player", "👤 Profile", "ℹ️ Info",
                       "⚙️ Ustawienia"]
        for i, label in enumerate(tab_labels):
            b = self._make_btn(tab_bar, label, "#333355", "#444466",
                               lambda idx=i: self._switch_tab(idx))
            b.pack(side=tk.LEFT, padx=(10 if i == 0 else 3, 3),
                   pady=5, ipady=3, ipadx=8)
            self.tab_btns.append(b)

        # Pages container
        self.pages_frame = tk.Frame(right, bg="#0f0f23")
        self.pages_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self._build_page_logs(self.pages_frame)
        self._build_page_active(self.pages_frame)
        self._build_page_proxy(self.pages_frame)
        self._build_page_player(self.pages_frame)
        self._build_page_profiles(self.pages_frame)
        self._build_page_info(self.pages_frame)
        self._build_page_settings(self.pages_frame)

        # Progress bar at bottom
        progress_frame = tk.Frame(right, bg=BG_BAR, height=28)
        progress_frame.pack(fill=tk.X, side=tk.BOTTOM)
        progress_frame.pack_propagate(False)

        self.progress_bar = ttk.Progressbar(
            progress_frame, orient=tk.HORIZONTAL, mode="determinate",
            style="green.Horizontal.TProgressbar", maximum=100)
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True,
                               padx=(6, 4), pady=4)

        self.progress_label = tk.Label(
            progress_frame, text="Gotowy", font=("Helvetica", 10),
            bg=BG_BAR, fg=FG_DIM, anchor=tk.W)
        self.progress_label.pack(side=tk.LEFT, padx=(0, 10))

        self._switch_tab(0)

    # ── Sidebar: Scanner ───────────────────────────────────
    def _build_sidebar_scanner(self, left):
        tk.Label(left, text="⚡ FLIPPER",
                 font=("Helvetica", 22, "bold"),
                 bg=BG_SIDEBAR, fg="#00d4ff").pack(pady=(14, 1))
        tk.Label(left, text="MAC Address Scanner",
                 font=("Helvetica", 10), bg=BG_SIDEBAR,
                 fg=FG_DIM).pack(pady=(0, 8))
        self._sep(left)

        self._lbl(left, "URL serwera")
        self.url_entry = self._entry(left)

        self._lbl(left, "Pierwsze 3 bajty MAC")
        self.mac_entry = self._entry(left, "00:1B:79")

        self._lbl(left, "Proxy (opcjonalnie)")
        self.proxy_inline_entry = self._entry(left)

        self._lbl(left, "Ilość procesów")
        self.workers_entry = self._entry(left, "10")

        self._lbl(left, "Timeout (s)")
        self.timeout_entry = self._entry(left, "5")

        # Checkboxes row
        cb_frame = tk.Frame(left, bg=BG_SIDEBAR)
        cb_frame.pack(fill=tk.X, padx=16, pady=(2, 4))

        self.save_var = tk.BooleanVar(value=True)
        tk.Checkbutton(cb_frame, text="Zapisuj",
                       variable=self.save_var, bg=BG_SIDEBAR,
                       fg="#aaaaaa", selectcolor=BG_INPUT,
                       activebackground=BG_SIDEBAR,
                       activeforeground="#cccccc",
                       font=("Helvetica", 10)).pack(side=tk.LEFT)

        tk.Checkbutton(cb_frame, text="Na wierzchu",
                       variable=self.keep_on_top_var, bg=BG_SIDEBAR,
                       fg="#aaaaaa", selectcolor=BG_INPUT,
                       activebackground=BG_SIDEBAR,
                       activeforeground="#cccccc",
                       font=("Helvetica", 10),
                       command=self._toggle_keep_on_top).pack(
            side=tk.LEFT, padx=(6, 0))

        tk.Checkbutton(cb_frame, text="Proxy",
                       variable=self.use_proxy_var, bg=BG_SIDEBAR,
                       fg="#aaaaaa", selectcolor=BG_INPUT,
                       activebackground=BG_SIDEBAR,
                       activeforeground="#cccccc",
                       font=("Helvetica", 10)).pack(
            side=tk.LEFT, padx=(6, 0))

        # Min channels filter
        min_ch_frame = tk.Frame(left, bg=BG_SIDEBAR)
        min_ch_frame.pack(fill=tk.X, padx=16, pady=(0, 4))
        tk.Label(min_ch_frame, text="Min. kanałów:",
                 font=("Helvetica", 10, "bold"),
                 bg=BG_SIDEBAR, fg="#c8c8e0").pack(side=tk.LEFT)
        self.min_channels_entry = tk.Entry(
            min_ch_frame, font=("Helvetica", 10), width=6,
            bg=BG_INPUT, fg="#e0e0e0", insertbackground="#ffffff",
            relief="flat", highlightthickness=1,
            highlightcolor=ACCENT, highlightbackground="#333355")
        self.min_channels_entry.pack(side=tk.LEFT, padx=(4, 0), ipady=2)
        self.min_channels_entry.insert(0, "0")

        # Export button
        self._make_btn(left, "📁 Eksportuj wyniki", "#333355", "#444466",
                       self._export_results).pack(
            fill=tk.X, padx=16, pady=(2, 6), ipady=2)

        self._sep(left)

        self.start_btn = self._make_btn(left, "▶  START", "#00b359",
                                        "#009945", self._toggle_start)
        self.start_btn.pack(fill=tk.X, padx=16, pady=(4, 4), ipady=6)

        ps = tk.Frame(left, bg=BG_SIDEBAR)
        ps.pack(fill=tk.X, padx=16, pady=(0, 6))
        self.pause_btn = self._make_btn(ps, "⏸ PAUZA", "#c78d00",
                                        "#a87600", self._toggle_pause)
        self.pause_btn.pack(side=tk.LEFT, expand=True, fill=tk.X,
                            padx=(0, 3), ipady=4)
        self._btn_disable(self.pause_btn)

        self.stop_btn = self._make_btn(ps, "⏹ STOP", "#cc3333",
                                       "#aa2222", self._stop_scan)
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X,
                           padx=(3, 0), ipady=4)
        self._btn_disable(self.stop_btn)

        self._sep(left)
        self.stat_checked = tk.Label(left, text="Sprawdzono:  0",
                                     font=("Helvetica", 12), anchor=tk.W,
                                     bg=BG_SIDEBAR, fg="#aaaaaa")
        self.stat_checked.pack(fill=tk.X, padx=18, pady=(4, 0))
        self.stat_found = tk.Label(left, text="Znaleziono:    0",
                                   font=("Helvetica", 12), anchor=tk.W,
                                   bg=BG_SIDEBAR, fg="#00ff88")
        self.stat_found.pack(fill=tk.X, padx=18)
        self.stat_status = tk.Label(left, text="Status: Bezczynny",
                                    font=("Helvetica", 11), anchor=tk.W,
                                    bg=BG_SIDEBAR, fg="#666666")
        self.stat_status.pack(fill=tk.X, padx=18, pady=(4, 0))

    # ── Sidebar: Player (only MACs + Profiles) ────────────
    def _build_sidebar_player(self, left):
        tk.Label(left, text="📺 PLAYER",
                 font=("Helvetica", 22, "bold"),
                 bg=BG_SIDEBAR, fg="#00d4ff").pack(pady=(14, 1))
        self._sep(left)

        self.active_profile_label = tk.Label(
            left, text="Aktywny: (brak)", font=("Helvetica", 11, "bold"),
            bg=BG_SIDEBAR, fg="#ffaa00", anchor=tk.W, wraplength=240)
        self.active_profile_label.pack(fill=tk.X, padx=14, pady=(2, 4))
        self._sep(left)

        # Sub-tab buttons
        sub_frame = tk.Frame(left, bg=BG_SIDEBAR)
        sub_frame.pack(fill=tk.X, padx=10, pady=(0, 4))

        self.player_sub_btns = []
        self.player_sub_pages = []

        b_macs = self._make_btn(sub_frame, "MAC-i", ACCENT, "#1d4ed8",
                                lambda: self._switch_player_sub(0))
        b_macs.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2),
                    ipady=2)
        self.player_sub_btns.append(b_macs)

        b_prof = self._make_btn(sub_frame, "Profile", "#333355", "#444466",
                                lambda: self._switch_player_sub(1))
        b_prof.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0),
                    ipady=2)
        self.player_sub_btns.append(b_prof)

        # Sub-page container
        sub_container = tk.Frame(left, bg=BG_SIDEBAR)
        sub_container.pack(fill=tk.BOTH, expand=True, padx=6, pady=(2, 6))

        # -- Sub-page 0: MAC list (only MAC addresses, no URL) --
        sp0 = tk.Frame(sub_container, bg=BG_SIDEBAR)
        sp0.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.player_sub_pages.append(sp0)

        self.player_mac_listbox = tk.Listbox(
            sp0, font=("Menlo", 10), bg=BG_INPUT, fg="#d0d0e8",
            selectbackground=ACCENT, selectforeground="white",
            relief="flat", bd=2, highlightthickness=0)
        mac_sb = tk.Scrollbar(sp0, command=self.player_mac_listbox.yview)
        self.player_mac_listbox.configure(yscrollcommand=mac_sb.set)
        mac_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.player_mac_listbox.pack(fill=tk.BOTH, expand=True)
        self.player_mac_listbox.bind("<<ListboxSelect>>",
                                     self._on_player_mac_select)

        # -- Sub-page 1: Profile list --
        sp1 = tk.Frame(sub_container, bg=BG_SIDEBAR)
        sp1.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.player_sub_pages.append(sp1)

        self.player_profile_listbox = tk.Listbox(
            sp1, font=("Menlo", 10), bg=BG_INPUT, fg="#d0d0e8",
            selectbackground=ACCENT, selectforeground="white",
            relief="flat", bd=2, highlightthickness=0)
        prof_sb = tk.Scrollbar(sp1, command=self.player_profile_listbox.yview)
        self.player_profile_listbox.configure(yscrollcommand=prof_sb.set)
        prof_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.player_profile_listbox.pack(fill=tk.BOTH, expand=True)
        self.player_profile_listbox.bind("<<ListboxSelect>>",
                                         self._on_player_profile_select)

        self._switch_player_sub(0)

        # Bottom buttons
        bot = tk.Frame(left, bg=BG_SIDEBAR)
        bot.pack(fill=tk.X, padx=10, pady=(0, 6))
        self._make_btn(bot, "🗑 Usuń MAC", "#cc3333", "#aa2222",
                       self._delete_selected_player_mac).pack(
            fill=tk.X, ipady=3, pady=(2, 2))
        self._make_btn(bot, "✏️ Edytuj profil", "#c78d00", "#a87600",
                       self._edit_selected_player_profile).pack(
            fill=tk.X, ipady=3, pady=(0, 2))
        self._make_btn(bot, "🗑 Usuń profil", "#cc3333", "#aa2222",
                       self._delete_selected_player_profile).pack(
            fill=tk.X, ipady=3, pady=(0, 2))

    # ── Page 0: Logs ──────────────────────────────────────
    def _build_page_logs(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        self.log_text = tk.Text(page, font=("Menlo", 11), bg=BG_DARK,
                                fg="#c8c8e0", wrap=tk.WORD,
                                state=tk.DISABLED, relief="flat", bd=4,
                                insertbackground="#ffffff")
        sb = tk.Scrollbar(page, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for tag, color in [("success", "#00ff88"), ("error", "#ff4444"),
                           ("info", "#55aaff"), ("warning", "#ffaa00"),
                           ("dim", "#555577")]:
            self.log_text.tag_config(tag, foreground=color)

    # ── Page 1: Active MACs (with search) ───────────────
    def _build_page_active(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        search_frame = tk.Frame(page, bg=BG_DARK)
        search_frame.pack(fill=tk.X, padx=4, pady=(4, 2))
        tk.Label(search_frame, text="🔍", font=("Helvetica", 12),
                 bg=BG_DARK, fg="#aaaaaa").pack(side=tk.LEFT, padx=(4, 2))
        self.mac_search_var = tk.StringVar()
        self.mac_search_entry = tk.Entry(
            search_frame, textvariable=self.mac_search_var,
            font=("Helvetica", 11), bg=BG_INPUT, fg="#e0e0e0",
            insertbackground="#ffffff", relief="flat",
            highlightthickness=1, highlightcolor=ACCENT,
            highlightbackground="#333355")
        self.mac_search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True,
                                   padx=(0, 4), ipady=3)
        self.mac_search_var.trace_add("write", self._filter_active_macs)

        tf = tk.Frame(page, bg=BG_DARK)
        tf.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(
            tf, columns=("url", "mac", "expiry", "channels", "proxy"),
            show="headings")
        self.tree.heading("url", text="URL")
        self.tree.heading("mac", text="Adres MAC")
        self.tree.heading("expiry", text="Data ważności")
        self.tree.heading("channels", text="Kanały")
        self.tree.heading("proxy", text="Proxy")
        self.tree.column("url", width=220, minwidth=120)
        self.tree.column("mac", width=160, minwidth=120)
        self.tree.column("expiry", width=200, minwidth=120)
        self.tree.column("channels", width=70, minwidth=50)
        self.tree.column("proxy", width=160, minwidth=100)

        tsb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        tsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)

        bot = tk.Frame(page, bg=BG_DARK)
        bot.pack(fill=tk.X, pady=(4, 0))
        self._make_btn(bot, "📋 Kopiuj zaznaczony", ACCENT, "#1d4ed8",
                       self._copy_selected_mac).pack(
            side=tk.LEFT, padx=(4, 4), ipady=3, ipadx=6)
        self._make_btn(bot, "📋 Kopiuj wszystkie", "#333355", "#444466",
                       self._copy_all_macs).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(bot, "🧬 Klonuj MAC", "#6d28d9", "#5b21b6",
                       self._clone_selected_mac).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(bot, "🗑 Usuń MAC", "#cc3333", "#aa2222",
                       self._delete_selected_active_mac).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(bot, "💾 Zapisz profil", "#00b359", "#009945",
                       self._save_selected_as_profile).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self.mac_count_label = tk.Label(bot, text="Znaleziono: 0",
                                        font=("Helvetica", 11),
                                        bg=BG_DARK, fg=FG_DIM)
        self.mac_count_label.pack(side=tk.RIGHT, padx=8)

    # ── Page 2: Proxy ─────────────────────────────────────
    def _build_page_proxy(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        top = tk.Frame(page, bg=BG_DARK)
        top.pack(fill=tk.X, pady=(4, 4))

        self._make_btn(top, "🔄 Pobierz z API", ACCENT, "#1d4ed8",
                       self._fetch_proxies).pack(
            side=tk.LEFT, padx=(4, 4), ipady=3, ipadx=6)
        self._make_btn(top, "🗑 Wyczyść listę", "#cc3333", "#aa2222",
                       self._clear_proxies).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)

        tk.Label(top, text="Dodaj:", font=("Helvetica", 11),
                 bg=BG_DARK, fg="#aaaaaa").pack(side=tk.LEFT, padx=(10, 4))
        self.proxy_add_entry = tk.Entry(
            top, font=("Helvetica", 11), width=28,
            bg=BG_INPUT, fg="#e0e0e0", insertbackground="#ffffff",
            relief="flat", highlightthickness=1,
            highlightcolor=ACCENT, highlightbackground="#333355")
        self.proxy_add_entry.pack(side=tk.LEFT, padx=(0, 4), ipady=3)
        self._make_btn(top, "➕", "#00b359", "#009945",
                       self._add_custom_proxy).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=4)

        self.proxy_count_label = tk.Label(
            top, text="Proxy: 0", font=("Helvetica", 11),
            bg=BG_DARK, fg=FG_DIM)
        self.proxy_count_label.pack(side=tk.RIGHT, padx=8)

        tf = tk.Frame(page, bg=BG_DARK)
        tf.pack(fill=tk.BOTH, expand=True)
        self.proxy_tree = ttk.Treeview(
            tf, columns=("proxy", "status"), show="headings")
        self.proxy_tree.heading("proxy", text="Adres proxy")
        self.proxy_tree.heading("status", text="Status")
        self.proxy_tree.column("proxy", width=400, minwidth=200)
        self.proxy_tree.column("status", width=120, minwidth=80)
        psb = ttk.Scrollbar(tf, orient=tk.VERTICAL,
                            command=self.proxy_tree.yview)
        self.proxy_tree.configure(yscrollcommand=psb.set)
        psb.pack(side=tk.RIGHT, fill=tk.Y)
        self.proxy_tree.pack(fill=tk.BOTH, expand=True)

        bot = tk.Frame(page, bg=BG_DARK)
        bot.pack(fill=tk.X, pady=(4, 0))
        self._make_btn(bot, "🗑 Usuń zaznaczony", "#cc3333", "#aa2222",
                       self._remove_selected_proxy).pack(
            side=tk.LEFT, padx=(4, 4), ipady=3, ipadx=6)

    # ── Page 3: Player (embedded mpv + channel panel) ─────
    def _build_page_player(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        # RIGHT channel panel
        right_panel = tk.Frame(page, bg=BG_DARK, width=330)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y)
        right_panel.pack_propagate(False)

        # Content type buttons
        type_frame = tk.Frame(right_panel, bg=BG_DARK)
        type_frame.pack(fill=tk.X, padx=4, pady=(4, 2))

        self.content_type_btns = []
        for ctype, lbl in [("itv", "📺 TV"), ("vod", "🎬 VOD"),
                            ("series", "📚 Series")]:
            btn = self._make_btn(
                type_frame, lbl,
                ACCENT if ctype == "itv" else "#333355",
                "#1d4ed8" if ctype == "itv" else "#444466",
                lambda t=ctype: self._switch_content_type(t))
            btn.pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X,
                     ipady=2)
            self.content_type_btns.append((ctype, btn))

        # Player proxy checkbox
        proxy_player_frame = tk.Frame(right_panel, bg=BG_DARK)
        proxy_player_frame.pack(fill=tk.X, padx=4, pady=(0, 2))
        tk.Checkbutton(
            proxy_player_frame,
            text="Używaj proxy w Playerze",
            variable=self.player_use_proxy_var,
            bg=BG_DARK,
            fg="#aaaaaa",
            selectcolor=BG_INPUT,
            activebackground=BG_DARK,
            activeforeground="#cccccc",
            font=("Helvetica", 10),
        ).pack(anchor=tk.W)

        # Genre dropdown
        genre_frame = tk.Frame(right_panel, bg=BG_DARK)
        genre_frame.pack(fill=tk.X, padx=4, pady=(2, 2))
        tk.Label(genre_frame, text="Kategoria:", font=("Helvetica", 10),
                 bg=BG_DARK, fg="#aaaaaa").pack(side=tk.LEFT, padx=(0, 4))
        self.genre_var = tk.StringVar(value="Wszystkie")
        self.genre_menu = tk.OptionMenu(
            genre_frame, self.genre_var, "Wszystkie")
        self.genre_menu.configure(
            bg=BG_INPUT, fg="#e0e0e0", font=("Helvetica", 10),
            activebackground=ACCENT, activeforeground="white",
            highlightthickness=0, relief="flat", bd=1)
        self.genre_menu["menu"].configure(
            bg=BG_INPUT, fg="#e0e0e0", font=("Helvetica", 10),
            activebackground=ACCENT, activeforeground="white")
        self.genre_menu.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.genre_var.trace_add("write", self._on_genre_change)

        # Channel search bar
        ch_search_frame = tk.Frame(right_panel, bg=BG_DARK)
        ch_search_frame.pack(fill=tk.X, padx=4, pady=(2, 2))
        tk.Label(ch_search_frame, text="🔍", font=("Helvetica", 11),
                 bg=BG_DARK, fg="#aaaaaa").pack(side=tk.LEFT, padx=(0, 2))
        self.channel_search_var = tk.StringVar()
        ch_search_entry = tk.Entry(
            ch_search_frame, textvariable=self.channel_search_var,
            font=("Helvetica", 10), bg=BG_INPUT, fg="#e0e0e0",
            insertbackground="#ffffff", relief="flat",
            highlightthickness=1, highlightcolor=ACCENT,
            highlightbackground="#333355")
        ch_search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True,
                             padx=(0, 2), ipady=2)
        self.channel_search_var.trace_add("write",
                                          self._filter_channel_list)

        # Sort + Go Back buttons
        nav_frame = tk.Frame(right_panel, bg=BG_DARK)
        nav_frame.pack(fill=tk.X, padx=4, pady=(0, 2))
        self.go_back_btn = self._make_btn(
            nav_frame, "← Wróć", "#555577", "#666688",
            self._nav_go_back)
        self.go_back_btn.pack(side=tk.LEFT, padx=(0, 2), ipady=1, ipadx=4)
        self._btn_disable(self.go_back_btn)
        self._make_btn(nav_frame, "A→Z Sortuj", "#333355", "#444466",
                       self._sort_channel_list).pack(
            side=tk.LEFT, padx=2, ipady=1, ipadx=4)
        self.nav_label = tk.Label(
            nav_frame, text="", font=("Helvetica", 9),
            bg=BG_DARK, fg=FG_DIM, anchor=tk.E)
        self.nav_label.pack(side=tk.RIGHT, padx=4)

        # Channel list
        ch_frame = tk.Frame(right_panel, bg=BG_DARK)
        ch_frame.pack(fill=tk.BOTH, expand=True, padx=4)

        self.channel_tree = ttk.Treeview(
            ch_frame, columns=("num", "name"), show="headings")
        self.channel_tree.heading("num", text="#")
        self.channel_tree.heading("name", text="Kanał / Tytuł")
        self.channel_tree.column("num", width=45, minwidth=35)
        self.channel_tree.column("name", width=260, minwidth=120)
        ch_sb = ttk.Scrollbar(ch_frame, orient=tk.VERTICAL,
                              command=self.channel_tree.yview)
        self.channel_tree.configure(yscrollcommand=ch_sb.set)
        ch_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.channel_tree.pack(fill=tk.BOTH, expand=True)
        self.channel_tree.bind("<Double-1>",
                               self._on_channel_double_click)

        self.channel_count_label = tk.Label(
            right_panel, text="Kanały: 0", font=("Helvetica", 10),
            bg=BG_DARK, fg=FG_DIM)
        self.channel_count_label.pack(pady=(2, 4))

        # CENTER: embedded player + controls
        center = tk.Frame(page, bg="#000000")
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Player area
        self.player_frame = tk.Frame(center, bg="#000000")
        self.player_frame.pack(fill=tk.BOTH, expand=True)

        if not HAS_MPV:
            tk.Label(self.player_frame,
                     text="mpv niedostępny.\n"
                         "Aplikacja próbuje instalacji automatycznej (winget).\n"
                         "Jeśli nadal nie działa: zainstaluj mpv i python-mpv ręcznie.",
                     font=("Helvetica", 14), bg="#000000", fg="#555577",
                     justify=tk.CENTER).place(relx=0.5, rely=0.5,
                                              anchor=tk.CENTER)

        # Controls bar
        controls = tk.Frame(center, bg=BG_BAR, height=46)
        controls.pack(fill=tk.X, side=tk.BOTTOM)
        controls.pack_propagate(False)

        self._make_btn(controls, "⏮", "#333355", "#444466",
                       self._player_prev).pack(
            side=tk.LEFT, padx=(6, 2), ipady=2, ipadx=4)
        self.play_pause_btn = self._make_btn(
            controls, "▶", "#00b359", "#009945", self._player_play_pause)
        self.play_pause_btn.pack(side=tk.LEFT, padx=2, ipady=2, ipadx=6)
        self._make_btn(controls, "⏭", "#333355", "#444466",
                       self._player_next).pack(
            side=tk.LEFT, padx=2, ipady=2, ipadx=4)
        self._make_btn(controls, "⏹", "#cc3333", "#aa2222",
                       self._player_stop).pack(
            side=tk.LEFT, padx=2, ipady=2, ipadx=4)

        tk.Label(controls, text="🔊", font=("Helvetica", 12),
                 bg=BG_BAR, fg="#aaaaaa").pack(side=tk.LEFT, padx=(12, 2))
        self.volume_scale = tk.Scale(
            controls, from_=0, to=100, orient=tk.HORIZONTAL,
            bg=BG_BAR, fg="#ffffff", troughcolor="#333355",
            highlightthickness=0, sliderrelief="flat",
            length=100, showvalue=0,
            command=self._on_volume_change)
        self.volume_scale.set(80)
        self.volume_scale.pack(side=tk.LEFT, padx=2)

        self._make_btn(controls, "⛶ Fullscreen", "#333355", "#444466",
                       self._player_fullscreen).pack(
            side=tk.RIGHT, padx=(2, 6), ipady=2, ipadx=4)
        self._make_btn(controls, "📋 Kopiuj URL", "#333355", "#444466",
                       self._copy_channel_url).pack(
            side=tk.RIGHT, padx=2, ipady=2, ipadx=4)

        self.player_status_label = tk.Label(
            controls, text="", font=("Helvetica", 10),
            bg=BG_BAR, fg="#00ff88", anchor=tk.W)
        self.player_status_label.pack(side=tk.LEFT, padx=(12, 0),
                                      fill=tk.X, expand=True)

    # ── Page 4: Profiles (with naming + rename) ───────────
    def _build_page_profiles(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        # Add profile form
        form = tk.Frame(page, bg=BG_DARK)
        form.pack(fill=tk.X, padx=10, pady=(10, 6))

        for lbl_text, attr in [("Nazwa:", "profile_name_entry"),
                                ("MAC:", "profile_mac_entry"),
                                ("URL:", "profile_url_entry"),
                                ("Proxy:", "profile_proxy_entry")]:
            tk.Label(form, text=lbl_text, font=("Helvetica", 11),
                     bg=BG_DARK, fg="#aaaaaa").pack(side=tk.LEFT, padx=(0, 2))
            e = tk.Entry(form, font=("Helvetica", 11), width=16,
                         bg=BG_INPUT, fg="#e0e0e0",
                         insertbackground="#ffffff", relief="flat",
                         highlightthickness=1, highlightcolor=ACCENT,
                         highlightbackground="#333355")
            e.pack(side=tk.LEFT, padx=(0, 8), ipady=3)
            setattr(self, attr, e)

        self._make_btn(form, "💾 Zapisz profil", "#00b359", "#009945",
                       self._save_profile_from_form).pack(
            side=tk.LEFT, padx=4, ipady=3, ipadx=6)

        # Profile list
        tf = tk.Frame(page, bg=BG_DARK)
        tf.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))

        self.profile_tree = ttk.Treeview(
            tf, columns=("name", "mac", "url", "proxy"),
            show="headings")
        self.profile_tree.heading("name", text="Nazwa")
        self.profile_tree.heading("mac", text="MAC")
        self.profile_tree.heading("url", text="URL")
        self.profile_tree.heading("proxy", text="Proxy")
        self.profile_tree.column("name", width=150, minwidth=80)
        self.profile_tree.column("mac", width=180, minwidth=120)
        self.profile_tree.column("url", width=250, minwidth=120)
        self.profile_tree.column("proxy", width=180, minwidth=80)

        prof_sb = ttk.Scrollbar(tf, orient=tk.VERTICAL,
                                command=self.profile_tree.yview)
        self.profile_tree.configure(yscrollcommand=prof_sb.set)
        prof_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.profile_tree.pack(fill=tk.BOTH, expand=True)

        bot = tk.Frame(page, bg=BG_DARK)
        bot.pack(fill=tk.X, padx=10, pady=(0, 10))
        self._make_btn(bot, "✅ Ustaw aktywny", ACCENT, "#1d4ed8",
                       self._set_active_profile).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(bot, "✏️ Zmień nazwę", "#c78d00", "#a87600",
                       self._rename_profile).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(bot, "✏️ Edytuj profil", "#c78d00", "#a87600",
                       self._edit_profile).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(bot, "🗑 Usuń profil", "#cc3333", "#aa2222",
                       self._delete_profile).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)

    # ── Page 5: Info ──────────────────────────────────────
    def _build_page_info(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        tk.Label(page, text="ℹ️ Informacje o koncie",
                 font=("Helvetica", 16, "bold"),
                 bg=BG_DARK, fg="#00d4ff").pack(padx=14, pady=(14, 6),
                                                anchor=tk.W)

        self.info_text = tk.Text(page, font=("Menlo", 12), bg=BG_DARK,
                                 fg="#d0d0e8", wrap=tk.WORD,
                                 state=tk.DISABLED, relief="flat", bd=8,
                                 insertbackground="#ffffff")
        info_sb = tk.Scrollbar(page, command=self.info_text.yview)
        self.info_text.configure(yscrollcommand=info_sb.set)
        info_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.info_text.pack(fill=tk.BOTH, expand=True)

        for tag, color in [("label", "#55aaff"), ("value", "#e0e0e0"),
                           ("highlight", "#00ff88"), ("warning", "#ffaa00")]:
            self.info_text.tag_config(tag, foreground=color)

        bot = tk.Frame(page, bg=BG_DARK)
        bot.pack(fill=tk.X, padx=10, pady=(4, 10))
        self._make_btn(bot, "🔄 Odśwież info", ACCENT, "#1d4ed8",
                       self._fetch_account_info).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)

    # ── Page 6: Settings ──────────────────────────────────
    def _build_page_settings(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        tk.Label(page, text="⚙️ Ustawienia",
                 font=("Helvetica", 16, "bold"),
                 bg=BG_DARK, fg="#00d4ff").pack(padx=14, pady=(14, 10),
                                                anchor=tk.W)

        # Verbose logs checkbox
        cb_frame = tk.Frame(page, bg=BG_DARK)
        cb_frame.pack(fill=tk.X, padx=20, pady=(4, 6))
        tk.Checkbutton(cb_frame,
                       text="Pokaż pełne zapytania i odpowiedzi w logach",
                       variable=self.verbose_logs_var, bg=BG_DARK,
                       fg="#d0d0e8", selectcolor=BG_INPUT,
                       activebackground=BG_DARK,
                       activeforeground="#ffffff",
                       font=("Helvetica", 12)).pack(anchor=tk.W)
        tk.Label(cb_frame,
                 text="Gdy włączone, logi będą zawierać pełne URL zapytań "
                      "oraz treść odpowiedzi serwera.",
                 font=("Helvetica", 10), bg=BG_DARK, fg=FG_DIM,
                 wraplength=600, anchor=tk.W, justify=tk.LEFT).pack(
            anchor=tk.W, pady=(2, 0))

        self._sep_dark(page)

        # Use proxy checkbox
        proxy_cb_frame = tk.Frame(page, bg=BG_DARK)
        proxy_cb_frame.pack(fill=tk.X, padx=20, pady=(4, 6))
        tk.Checkbutton(proxy_cb_frame,
                       text="Używaj proxy podczas skanowania",
                       variable=self.use_proxy_var, bg=BG_DARK,
                       fg="#d0d0e8", selectcolor=BG_INPUT,
                       activebackground=BG_DARK,
                       activeforeground="#ffffff",
                       font=("Helvetica", 12)).pack(anchor=tk.W)
        tk.Label(proxy_cb_frame,
                 text="Gdy wyłączone, skaner łączy się bezpośrednio "
                      "bez proxy. Timeout automatycznie usuwa proxy.",
                 font=("Helvetica", 10), bg=BG_DARK, fg=FG_DIM,
                 wraplength=600, anchor=tk.W, justify=tk.LEFT).pack(
            anchor=tk.W, pady=(2, 0))

        self._sep_dark(page)

        # Save folder
        folder_frame = tk.Frame(page, bg=BG_DARK)
        folder_frame.pack(fill=tk.X, padx=20, pady=(4, 6))
        tk.Label(folder_frame, text="📁 Folder zapisu danych:",
                 font=("Helvetica", 12, "bold"),
                 bg=BG_DARK, fg="#d0d0e8").pack(anchor=tk.W)

        row = tk.Frame(folder_frame, bg=BG_DARK)
        row.pack(fill=tk.X, pady=(4, 0))
        self.save_folder_entry = tk.Entry(
            row, font=("Helvetica", 11), bg=BG_INPUT, fg="#e0e0e0",
            insertbackground="#ffffff", relief="flat",
            highlightthickness=1, highlightcolor=ACCENT,
            highlightbackground="#333355")
        self.save_folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True,
                                    padx=(0, 6), ipady=4)
        if self.save_folder:
            self.save_folder_entry.insert(0, self.save_folder)
        self._make_btn(row, "📂 Wybierz", ACCENT, "#1d4ed8",
                       self._choose_save_folder).pack(
            side=tk.LEFT, ipady=3, ipadx=6)

        tk.Label(folder_frame,
                 text="Puste = bieżący katalog. Sesja, wyniki i eksporty "
                      "będą zapisywane w wybranym folderze.",
                 font=("Helvetica", 10), bg=BG_DARK, fg=FG_DIM,
                 wraplength=600, anchor=tk.W, justify=tk.LEFT).pack(
            anchor=tk.W, pady=(4, 0))

        self._sep_dark(page)

        # Clear channel cache button
        cache_frame = tk.Frame(page, bg=BG_DARK)
        cache_frame.pack(fill=tk.X, padx=20, pady=(4, 6))
        self._make_btn(cache_frame, "🗑 Wyczyść cache kanałów",
                       "#cc3333", "#aa2222",
                       self._clear_channels_cache).pack(
            anchor=tk.W, ipady=3, ipadx=6)
        tk.Label(cache_frame,
                 text="Usuwa zapisane listy kanałów. Następnym razem "
                      "kanały zostaną pobrane z serwera.",
                 font=("Helvetica", 10), bg=BG_DARK, fg=FG_DIM,
                 wraplength=600, anchor=tk.W, justify=tk.LEFT).pack(
            anchor=tk.W, pady=(4, 0))

    def _sep_dark(self, parent):
        tk.Frame(parent, height=1, bg="#333355").pack(
            fill=tk.X, padx=20, pady=8)

    def _choose_save_folder(self):
        folder = filedialog.askdirectory(
            title="Wybierz folder zapisu",
            initialdir=self.save_folder or os.getcwd())
        if folder:
            self.save_folder = folder
            self.save_folder_entry.delete(0, tk.END)
            self.save_folder_entry.insert(0, folder)
            self._log(f"Folder zapisu: {folder}", "info")

    # ══════════════════════════════════════════════════════
    #  CHANNEL CACHE
    # ══════════════════════════════════════════════════════

    def _channels_cache_path(self):
        return (os.path.join(self.save_folder, CHANNELS_CACHE_FILE)
                if self.save_folder else CHANNELS_CACHE_FILE)

    def _load_channels_cache(self):
        path = self._channels_cache_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_channels_cache(self, cache):
        try:
            with open(self._channels_cache_path(), "w",
                      encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception:
            pass

    def _clear_channels_cache(self):
        path = self._channels_cache_path()
        if os.path.exists(path):
            os.remove(path)
        self._log("Cache kanałów wyczyszczony.", "info")

    # ══════════════════════════════════════════════════════
    #  WIDGET HELPERS
    # ══════════════════════════════════════════════════════

    def _entry(self, parent, default=""):
        e = tk.Entry(parent, font=("Helvetica", 11), bg=BG_INPUT,
                     fg="#e0e0e0", insertbackground="#ffffff",
                     relief="flat", highlightthickness=1,
                     highlightcolor=ACCENT, highlightbackground="#333355")
        e.pack(fill=tk.X, padx=16, pady=(2, 6), ipady=4)
        if default:
            e.insert(0, default)
        return e

    def _lbl(self, parent, text):
        tk.Label(parent, text=text, font=("Helvetica", 11, "bold"),
                 bg=BG_SIDEBAR, fg="#c8c8e0", anchor=tk.W).pack(
            fill=tk.X, padx=18, pady=(2, 0))

    def _sep(self, parent):
        tk.Frame(parent, height=1, bg="#333355").pack(
            fill=tk.X, padx=14, pady=6)

    def _make_btn(self, parent, text, bg_color, hover_color, command):
        lbl = tk.Label(parent, text=text, font=("Helvetica", 11, "bold"),
                       bg=bg_color, fg="white", cursor="hand2",
                       anchor=tk.CENTER, padx=6, pady=2)
        lbl._normal_bg = bg_color
        lbl._hover_bg = hover_color
        lbl._command = command
        lbl._enabled = True
        lbl.bind("<Button-1>",
                 lambda e: lbl._command() if lbl._enabled else None)
        lbl.bind("<Enter>",
                 lambda e: lbl.configure(bg=lbl._hover_bg)
                 if lbl._enabled else None)
        lbl.bind("<Leave>",
                 lambda e: lbl.configure(bg=lbl._normal_bg)
                 if lbl._enabled else None)
        return lbl

    def _btn_enable(self, btn):
        btn._enabled = True
        btn.configure(bg=btn._normal_bg, fg="white", cursor="hand2")

    def _btn_disable(self, btn):
        btn._enabled = False
        btn.configure(bg="#444444", fg="#888888", cursor="arrow")

    def _switch_tab(self, idx):
        self.current_tab = idx
        for i, (btn, pg) in enumerate(zip(self.tab_btns, self.tab_pages)):
            if i == idx:
                btn._normal_bg = ACCENT
                btn.configure(bg=ACCENT)
                pg.lift()
            else:
                btn._normal_bg = "#333355"
                btn.configure(bg="#333355")
        if idx == 3:
            self.sidebar_player.lift()
            self._refresh_player_mac_list()
            self._refresh_player_profile_list()
        else:
            self.sidebar_scanner.lift()

    def _switch_player_sub(self, idx):
        for i, (btn, pg) in enumerate(
                zip(self.player_sub_btns, self.player_sub_pages)):
            if i == idx:
                btn._normal_bg = ACCENT
                btn.configure(bg=ACCENT)
                pg.lift()
            else:
                btn._normal_bg = "#333355"
                btn.configure(bg="#333355")

    # ══════════════════════════════════════════════════════
    #  PROGRESS BAR
    # ══════════════════════════════════════════════════════

    def _set_progress(self, value, text=""):
        self.root.after(0, self._do_set_progress, value, text)

    def _do_set_progress(self, value, text):
        self.progress_bar["value"] = min(max(value, 0), 100)
        if text:
            self.progress_label.configure(text=text)

    # ══════════════════════════════════════════════════════
    #  KEEP ON TOP
    # ══════════════════════════════════════════════════════

    def _toggle_keep_on_top(self):
        self.root.attributes("-topmost", self.keep_on_top_var.get())

    # ══════════════════════════════════════════════════════
    #  LOGGING
    # ══════════════════════════════════════════════════════

    def _log(self, message, tag="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{ts}] {message}"
        self.log_history.append((full_msg, tag))
        if len(self.log_history) > MAX_LOG_SAVE:
            self.log_history = self.log_history[-MAX_LOG_SAVE:]
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{ts}] ", "dim")
        self.log_text.insert(tk.END, f"{message}\n", tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _log_safe(self, message, tag="info"):
        self.root.after(0, self._log, message, tag)

    # ══════════════════════════════════════════════════════
    #  STATS
    # ══════════════════════════════════════════════════════

    def _update_stats(self):
        self.stat_checked.configure(text=f"Sprawdzono:  {self.checked_count}")
        self.stat_found.configure(text=f"Znaleziono:    {self.found_count}")

    def _update_stats_safe(self):
        self.root.after(0, self._update_stats)

    def _set_status(self, text, color="#666666"):
        self.root.after(0, lambda: self.stat_status.configure(
            text=f"Status: {text}", fg=color))

    # ══════════════════════════════════════════════════════
    #  ACTIVE MAC MANAGEMENT
    # ══════════════════════════════════════════════════════

    def _filter_active_macs(self, *args):
        query = self.mac_search_var.get().strip().lower()
        for item in self.tree.get_children():
            self.tree.delete(item)
        for m in self.active_macs:
            if query:
                haystack = f"{m['url']} {m['mac']} {m['expiry']} " \
                           f"{m.get('proxy', '')}".lower()
                if query not in haystack:
                    continue
            self.tree.insert("", tk.END,
                             values=(m["url"], m["mac"],
                                     m["expiry"],
                                     m.get("channels", "?"),
                                     m.get("proxy", "")))

    def _add_active_mac(self, url, mac, expiry, proxy=None, channels=0):
        entry = {"url": url, "mac": mac, "expiry": expiry,
                 "proxy": proxy or "", "channels": channels}
        self.active_macs.append(entry)
        if proxy:
            self.mac_proxy_map[mac] = proxy
        self.root.after(0, self._insert_mac_row, entry)

    def _insert_mac_row(self, entry):
        self.tree.insert("", tk.END,
                         values=(entry["url"], entry["mac"],
                                 entry["expiry"],
                                 entry.get("channels", "?"),
                                 entry["proxy"]))
        self.mac_count_label.configure(
            text=f"Znaleziono: {len(self.active_macs)}")

    def _copy_selected_mac(self):
        sel = self.tree.selection()
        if not sel:
            self._log("Nie zaznaczono wiersza.", "warning")
            return
        mac = self.tree.item(sel[0], "values")[1]
        self.root.clipboard_clear()
        self.root.clipboard_append(mac)
        self._log(f"Skopiowano MAC: {mac}", "info")

    def _copy_all_macs(self):
        if not self.active_macs:
            self._log("Brak aktywnych MAC.", "warning")
            return
        text = "\n".join(f"{m['mac']} | {m['expiry']} | {m['url']}"
                         for m in self.active_macs)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._log(f"Skopiowano {len(self.active_macs)} MAC.", "info")

    def _delete_selected_active_mac(self):
        sel = self.tree.selection()
        if not sel:
            self._log("Zaznacz MAC do usunięcia.", "warning")
            return
        vals = self.tree.item(sel[0], "values")
        if len(vals) < 2:
            return
        url = vals[0]
        mac = vals[1]

        before = len(self.active_macs)
        self.active_macs = [m for m in self.active_macs
                            if not (m.get("mac") == mac and
                                    m.get("url") == url)]
        after = len(self.active_macs)
        if after == before:
            self._log("Nie znaleziono rekordu do usunięcia.", "warning")
            return

        self.mac_proxy_map.pop(mac, None)
        self.tree.delete(sel[0])
        self.mac_count_label.configure(text=f"Znaleziono: {after}")
        self._refresh_player_mac_list()
        self._auto_save()
        self._log(f"Usunięto MAC: {mac}", "info")

    def _clone_selected_mac(self):
        sel = self.tree.selection()
        if not sel:
            self._log("Zaznacz MAC do sklonowania.", "warning")
            return
        mac = self.tree.item(sel[0], "values")[1]
        self.root.clipboard_clear()
        self.root.clipboard_append(mac)
        self._log(f"🧬 Sklonowano MAC: {mac}", "success")

    def _save_selected_as_profile(self):
        """Save selected MAC as profile — ask for name via dialog."""
        sel = self.tree.selection()
        if not sel:
            self._log("Zaznacz MAC do zapisania jako profil.", "warning")
            return
        vals = self.tree.item(sel[0], "values")
        url, mac, expiry, proxy = vals[0], vals[1], vals[2], vals[3]

        name = simpledialog.askstring(
            "Nazwa profilu", "Podaj nazwę dla profilu:",
            initialvalue=f"Profil {len(self.profiles) + 1}",
            parent=self.root)
        if not name:
            return
        self.profiles.append({"name": name, "mac": mac, "url": url,
                              "proxy": proxy})
        self._refresh_profile_tree()
        self._log(f"Zapisano profil: {name} ({mac})", "success")

    # ══════════════════════════════════════════════════════
    #  EXPORT
    # ══════════════════════════════════════════════════════

    def _export_results(self):
        if not self.active_macs:
            self._log("Brak wyników do eksportu.", "warning")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Tekst", "*.txt"), ("CSV", "*.csv"),
                       ("Wszystkie", "*.*")],
            initialfile="flipper_results.txt")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Flipper — wyniki skanowania\n")
            f.write(f"# {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            for m in self.active_macs:
                f.write(f"{m['mac']} | {m['expiry']} | {m['url']} | "
                        f"ch={m.get('channels', '?')} | "
                        f"{m.get('proxy', '')}\n")
        self._log(f"Wyeksportowano {len(self.active_macs)} wyników.",
                  "success")

    def _auto_save(self):
        if not self.save_var.get() or not self.active_macs:
            return
        try:
            save_path = (os.path.join(self.save_folder, RESULTS_FILE)
                         if self.save_folder else RESULTS_FILE)
            with open(save_path, "w", encoding="utf-8") as f:
                f.write("# Flipper — auto-zapis\n")
                for m in self.active_macs:
                    f.write(f"{m['mac']} | {m['expiry']} | {m['url']} | "
                            f"ch={m.get('channels', '?')} | "
                            f"{m.get('proxy', '')}\n")
        except Exception:
            pass

    # ══════════════════════════════════════════════════════
    #  SESSION PERSISTENCE
    # ══════════════════════════════════════════════════════

    def _save_session(self):
        data = {
            "url": self.url_entry.get(),
            "mac_prefix": self.mac_entry.get(),
            "workers": self.workers_entry.get(),
            "timeout": self.timeout_entry.get(),
            "save_results": self.save_var.get(),
            "proxy_inline": self.proxy_inline_entry.get(),
            "active_macs": self.active_macs,
            "mac_proxy_map": self.mac_proxy_map,
            "logs": self.log_history[-MAX_LOG_SAVE:],
            "proxies": get_proxy_list(),
            "profiles": self.profiles,
            "active_profile": self.active_profile,
            "checked_count": self.checked_count,
            "found_count": self.found_count,
            "verbose_logs": self.verbose_logs_var.get(),
            "save_folder": self.save_folder,
            "use_proxy": self.use_proxy_var.get(),
            "player_use_proxy": self.player_use_proxy_var.get(),
            "min_channels": self.min_channels_entry.get(),
        }
        try:
            save_path = (os.path.join(self.save_folder, SESSION_FILE)
                         if self.save_folder else SESSION_FILE)
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _load_session(self):
        session_paths = []
        if self.save_folder:
            session_paths.append(os.path.join(self.save_folder, SESSION_FILE))
        session_paths.append(SESSION_FILE)

        session_path = None
        for path in session_paths:
            if os.path.exists(path):
                session_path = path
                break

        if not session_path:
            return
        try:
            with open(session_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        for key, widget in [("url", self.url_entry),
                            ("mac_prefix", self.mac_entry),
                            ("workers", self.workers_entry),
                            ("timeout", self.timeout_entry),
                            ("proxy_inline", self.proxy_inline_entry)]:
            val = data.get(key, "")
            if val:
                widget.delete(0, tk.END)
                widget.insert(0, val)
        if "save_results" in data:
            self.save_var.set(data["save_results"])

        self.checked_count = data.get("checked_count", 0)
        self.found_count = data.get("found_count", 0)
        self._update_stats()

        for m in data.get("active_macs", []):
            self.active_macs.append(m)
            self._insert_mac_row(m)

        self.mac_proxy_map = data.get("mac_proxy_map", {})

        for msg, tag in data.get("logs", []):
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, f"{msg}\n", tag)
            self.log_text.configure(state=tk.DISABLED)
            self.log_history.append((msg, tag))
        if self.log_history:
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END,
                                 "── Sesja przywrócona ──\n", "warning")
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)

        saved_proxies = data.get("proxies", [])
        if saved_proxies:
            set_proxy_list(saved_proxies)
            self._refresh_proxy_tree()

        self.profiles = data.get("profiles", [])
        self._refresh_profile_tree()

        self.active_profile = data.get("active_profile", None)
        if self.active_profile:
            self.active_profile_label.configure(
                text=f"Aktywny: {self.active_profile.get('name', '?')}")

        if "verbose_logs" in data:
            self.verbose_logs_var.set(data["verbose_logs"])
        if "use_proxy" in data:
            self.use_proxy_var.set(data["use_proxy"])
        if "player_use_proxy" in data:
            self.player_use_proxy_var.set(data["player_use_proxy"])
        if "min_channels" in data:
            self.min_channels_entry.delete(0, tk.END)
            self.min_channels_entry.insert(0, data["min_channels"])
        saved_folder = data.get("save_folder", "")
        if saved_folder:
            self.save_folder = saved_folder
            if hasattr(self, 'save_folder_entry'):
                self.save_folder_entry.delete(0, tk.END)
                self.save_folder_entry.insert(0, saved_folder)

    # ══════════════════════════════════════════════════════
    #  PROXY TAB LOGIC
    # ══════════════════════════════════════════════════════

    def _get_active_proxy(self):
        if not self.use_proxy_var.get():
            return None
        inline = self.proxy_inline_entry.get().strip()
        if inline:
            if not inline.startswith("http"):
                inline = "http://" + inline
            return inline
        return get_current_proxy()

    def _get_proxy_for_mac(self, mac):
        return self.mac_proxy_map.get(mac)

    def _auto_fetch_proxies_on_startup(self):
        if not get_proxy_list():
            self._log("Auto-pobieranie proxy przy starcie...", "info")
            threading.Thread(target=self._fetch_proxies_worker,
                             daemon=True).start()
        else:
            self._log(f"Załadowano {len(get_proxy_list())} proxy z sesji.",
                      "info")

    def _fetch_proxies(self):
        self._log("Pobieranie listy proxy z API...", "info")
        self._set_progress(20, "Pobieranie proxy...")
        threading.Thread(target=self._fetch_proxies_worker,
                         daemon=True).start()

    def _fetch_proxies_worker(self):
        proxies = fetch_free_proxies()
        if proxies:
            set_proxy_list(proxies)
            self._log_safe(f"Pobrano {len(proxies)} proxy.", "success")
            self.root.after(0, self._refresh_proxy_tree)
            self._set_progress(100, f"Pobrano {len(proxies)} proxy")
        else:
            self._log_safe("Nie udało się pobrać proxy.", "error")
            self._set_progress(100, "Błąd pobierania proxy")

    def _refresh_proxy_tree(self):
        for item in self.proxy_tree.get_children():
            self.proxy_tree.delete(item)
        for p in get_proxy_list():
            self.proxy_tree.insert("", tk.END, values=(p, "OK"))
        self.proxy_count_label.configure(
            text=f"Proxy: {len(get_proxy_list())}")

    def _clear_proxies(self):
        set_proxy_list([])
        self._refresh_proxy_tree()
        self._log("Wyczyszczono listę proxy.", "info")

    def _add_custom_proxy(self):
        val = self.proxy_add_entry.get().strip()
        if not val:
            return
        if not val.startswith("http"):
            val = "http://" + val
        add_proxy(val)
        self.proxy_add_entry.delete(0, tk.END)
        self._refresh_proxy_tree()
        self._log(f"Dodano proxy: {val}", "info")

    def _remove_selected_proxy(self):
        sel = self.proxy_tree.selection()
        if not sel:
            self._log("Zaznacz proxy do usunięcia.", "warning")
            return
        val = self.proxy_tree.item(sel[0], "values")[0]
        remove_proxy(val)
        self._refresh_proxy_tree()
        self._log(f"Usunięto proxy: {val}", "info")

    def _handle_proxy_fail(self, proxy, status_code=0):
        if not proxy:
            return
        if status_code and should_remove_proxy(status_code):
            remove_proxy(proxy)
            self._log_safe(f"Proxy usunięty (HTTP {status_code}): {proxy}",
                           "warning")
            self.root.after(0, self._refresh_proxy_tree)
        else:
            removed = report_proxy_fail(proxy)
            if removed:
                self._log_safe(
                    f"Proxy usunięty (zbyt wiele błędów): {proxy}", "warning")
                self.root.after(0, self._refresh_proxy_tree)
        new_proxy = rotate_proxy()
        if new_proxy:
            self._log_safe(f"Zmiana proxy → {new_proxy}", "info")

    # ══════════════════════════════════════════════════════
    #  PROXY RETRY HELPER — try all proxies before giving up
    # ══════════════════════════════════════════════════════

    def _find_endpoint_with_proxy_retry(self, server_address, timeout):
        """Try to find a responding endpoint, cycling through all proxies.
        Returns (endpoint, proxy_used) or (None, None).
        """
        proxy = self._get_active_proxy()
        endpoint, ep_code = get_responding_endpoint(
            server_address, timeout=timeout, proxy=proxy)

        if endpoint:
            return endpoint, proxy

        # First proxy failed — iterate through all available proxies
        tried = {proxy} if proxy else set()
        for attempt in range(MAX_PROXY_RETRIES):
            if proxy:
                self._handle_proxy_fail(proxy, ep_code)
            proxy = self._get_active_proxy()

            if not proxy or proxy in tried:
                # Try rotating to get a fresh one
                proxy = rotate_proxy()
            if not proxy or proxy in tried:
                break

            tried.add(proxy)
            self._log_safe(
                f"Próba {attempt + 2} z proxy: {proxy}", "info")
            endpoint, ep_code = get_responding_endpoint(
                server_address, timeout=timeout, proxy=proxy)
            if endpoint:
                return endpoint, proxy

        # Last resort: try without proxy
        self._log_safe("Próba bez proxy...", "warning")
        endpoint, ep_code = get_responding_endpoint(
            server_address, timeout=timeout, proxy=None)
        if endpoint:
            return endpoint, None

        self._log_safe(
            f"Serwer nie odpowiada (HTTP {ep_code})!", "error")
        return None, None

    # ══════════════════════════════════════════════════════
    #  PROFILES
    # ══════════════════════════════════════════════════════

    def _refresh_profile_tree(self):
        for item in self.profile_tree.get_children():
            self.profile_tree.delete(item)
        for p in self.profiles:
            self.profile_tree.insert("", tk.END,
                                     values=(p["name"], p["mac"],
                                             p["url"], p.get("proxy", "")))

    def _save_profile_from_form(self):
        name = self.profile_name_entry.get().strip()
        mac = self.profile_mac_entry.get().strip()
        url = self.profile_url_entry.get().strip()
        proxy = self.profile_proxy_entry.get().strip()
        if not name or not mac:
            self._log("Podaj nazwę i MAC dla profilu.", "warning")
            return
        self.profiles.append({"name": name, "mac": mac, "url": url,
                              "proxy": proxy})
        self._refresh_profile_tree()
        self.profile_name_entry.delete(0, tk.END)
        self.profile_mac_entry.delete(0, tk.END)
        self.profile_url_entry.delete(0, tk.END)
        self.profile_proxy_entry.delete(0, tk.END)
        self._log(f"Zapisano profil: {name}", "success")

    def _set_active_profile(self):
        sel = self.profile_tree.selection()
        if not sel:
            self._log("Zaznacz profil.", "warning")
            return
        idx = self.profile_tree.index(sel[0])
        if idx < len(self.profiles):
            self.active_profile = self.profiles[idx]
            self.active_profile_label.configure(
                text=f"Aktywny: {self.active_profile['name']}")
            self._log(f"Aktywny profil: {self.active_profile['name']}",
                      "info")

    def _edit_profile(self):
        sel = self.profile_tree.selection()
        if not sel:
            self._log("Zaznacz profil do edycji.", "warning")
            return
        idx = self.profile_tree.index(sel[0])
        if idx >= len(self.profiles):
            return

        profile = self.profiles[idx]
        old_name = profile.get("name", "")

        name = simpledialog.askstring(
            "Edytuj profil", "Nazwa:",
            initialvalue=profile.get("name", ""), parent=self.root)
        if name is None:
            return

        mac = simpledialog.askstring(
            "Edytuj profil", "MAC:",
            initialvalue=profile.get("mac", ""), parent=self.root)
        if mac is None:
            return

        url = simpledialog.askstring(
            "Edytuj profil", "URL:",
            initialvalue=profile.get("url", ""), parent=self.root)
        if url is None:
            return

        proxy = simpledialog.askstring(
            "Edytuj profil", "Proxy (puste = brak):",
            initialvalue=profile.get("proxy", ""), parent=self.root)
        if proxy is None:
            return

        profile["name"] = name.strip() or old_name
        profile["mac"] = mac.strip()
        profile["url"] = url.strip()
        profile["proxy"] = proxy.strip()

        if (self.active_profile and
                self.active_profile.get("name") == old_name and
                self.active_profile.get("mac") == self.profiles[idx].get("mac")):
            self.active_profile = profile
            self.active_profile_label.configure(
                text=f"Aktywny: {profile['name']}")

        self._refresh_profile_tree()
        self._refresh_player_profile_list()
        self._log(f"Zaktualizowano profil: {profile['name']}", "success")

    def _rename_profile(self):
        """Rename selected profile via dialog."""
        sel = self.profile_tree.selection()
        if not sel:
            self._log("Zaznacz profil do zmiany nazwy.", "warning")
            return
        idx = self.profile_tree.index(sel[0])
        if idx >= len(self.profiles):
            return
        old_name = self.profiles[idx]["name"]
        new_name = simpledialog.askstring(
            "Zmień nazwę", "Nowa nazwa profilu:",
            initialvalue=old_name, parent=self.root)
        if not new_name or new_name == old_name:
            return
        self.profiles[idx]["name"] = new_name
        if (self.active_profile and
                self.active_profile.get("mac") == self.profiles[idx]["mac"]):
            self.active_profile["name"] = new_name
            self.active_profile_label.configure(
                text=f"Aktywny: {new_name}")
        self._refresh_profile_tree()
        self._log(f"Zmieniono nazwę: {old_name} → {new_name}", "info")

    def _delete_profile(self):
        sel = self.profile_tree.selection()
        if not sel:
            self._log("Zaznacz profil do usunięcia.", "warning")
            return
        idx = self.profile_tree.index(sel[0])
        if idx < len(self.profiles):
            removed = self.profiles.pop(idx)
            if (self.active_profile and
                    self.active_profile.get("name") == removed["name"] and
                    self.active_profile.get("mac") == removed["mac"]):
                self.active_profile = None
                self.active_profile_label.configure(
                    text="Aktywny: (brak)")
            self._refresh_profile_tree()
            self._refresh_player_profile_list()
            self._log(f"Usunięto profil: {removed['name']}", "info")

    # ══════════════════════════════════════════════════════
    #  PLAYER SIDEBAR HELPERS (only MAC, no URL)
    # ══════════════════════════════════════════════════════

    def _refresh_player_mac_list(self):
        self.player_mac_listbox.delete(0, tk.END)
        for m in self.active_macs:
            self.player_mac_listbox.insert(tk.END, m["mac"])

    def _refresh_player_profile_list(self):
        self.player_profile_listbox.delete(0, tk.END)
        for p in self.profiles:
            text = f"{p['name']}  ({p['mac'][:17]})"
            self.player_profile_listbox.insert(tk.END, text)

    def _delete_selected_player_mac(self):
        sel = self.player_mac_listbox.curselection()
        if not sel:
            self._log("Zaznacz MAC w panelu Player.", "warning")
            return
        idx = sel[0]
        if idx >= len(self.active_macs):
            return
        removed = self.active_macs.pop(idx)
        self.mac_proxy_map.pop(removed.get("mac", ""), None)
        self._filter_active_macs()
        self._refresh_player_mac_list()
        self.mac_count_label.configure(text=f"Znaleziono: {len(self.active_macs)}")
        self._auto_save()
        self._log(f"Usunięto MAC: {removed.get('mac', '?')}", "info")

    def _delete_selected_player_profile(self):
        sel = self.player_profile_listbox.curselection()
        if not sel:
            self._log("Zaznacz profil w panelu Player.", "warning")
            return
        idx = sel[0]
        if idx >= len(self.profiles):
            return
        removed = self.profiles.pop(idx)
        if (self.active_profile and
                self.active_profile.get("name") == removed.get("name") and
                self.active_profile.get("mac") == removed.get("mac")):
            self.active_profile = None
            self.active_profile_label.configure(text="Aktywny: (brak)")
        self._refresh_profile_tree()
        self._refresh_player_profile_list()
        self._log(f"Usunięto profil: {removed.get('name', '?')}", "info")

    def _edit_selected_player_profile(self):
        sel = self.player_profile_listbox.curselection()
        if not sel:
            self._log("Zaznacz profil do edycji.", "warning")
            return
        idx = sel[0]
        if idx >= len(self.profiles):
            return
        self.profile_tree.selection_set(self.profile_tree.get_children()[idx])
        self._edit_profile()

    def _on_player_mac_select(self, event):
        sel = self.player_mac_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < len(self.active_macs):
            m = self.active_macs[idx]
            self.active_profile = {"name": m["mac"][:17], "mac": m["mac"],
                                   "url": m["url"],
                                   "proxy": m.get("proxy", "")}
            self.active_profile_label.configure(
                text=f"Aktywny: {m['mac']}")
            self._fetch_channels()

    def _on_player_profile_select(self, event):
        sel = self.player_profile_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < len(self.profiles):
            self.active_profile = self.profiles[idx]
            self.active_profile_label.configure(
                text=f"Aktywny: {self.active_profile['name']}")
            self._fetch_channels()

    def _get_player_mac_url_proxy(self):
        if self.active_profile:
            mac = self.active_profile.get("mac", "")
            url = self.active_profile.get("url", "")
            proxy = self.active_profile.get("proxy", "")
            if self.player_use_proxy_var.get():
                if not proxy:
                    proxy = self._get_proxy_for_mac(mac)
            else:
                proxy = None
            if not url:
                url = self.url_entry.get().strip()
            return mac, url, proxy or None
        return None, None, None

    # ══════════════════════════════════════════════════════
    #  PLAYER CONTENT TYPE + GENRES
    # ══════════════════════════════════════════════════════

    def _switch_content_type(self, ctype):
        self.player_content_type = ctype
        self.nav_stack.clear()
        self._update_nav_ui()
        for ct, btn in self.content_type_btns:
            if ct == ctype:
                btn._normal_bg = ACCENT
                btn._hover_bg = "#1d4ed8"
                btn.configure(bg=ACCENT)
            else:
                btn._normal_bg = "#333355"
                btn._hover_bg = "#444466"
                btn.configure(bg="#333355")
        self._fetch_channels()

    def _on_genre_change(self, *args):
        self._fetch_channels_for_genre()

    # ══════════════════════════════════════════════════════
    #  NAVIGATION STACK
    # ══════════════════════════════════════════════════════

    def _update_nav_ui(self):
        if self.nav_stack:
            self._btn_enable(self.go_back_btn)
            trail = " → ".join(s.get("label", "?") for s in self.nav_stack)
            self.nav_label.configure(text=trail)
        else:
            self._btn_disable(self.go_back_btn)
            self.nav_label.configure(text="")

    def _nav_go_back(self):
        if not self.nav_stack:
            return
        self.nav_stack.pop()
        if self.nav_stack:
            prev = self.nav_stack[-1]
            self.player_channels = prev.get("channels", [])
            self.root.after(0, self._populate_channel_tree)
        else:
            self._fetch_channels()
        self._update_nav_ui()

    def _on_channel_double_click(self, event):
        sel = self.channel_tree.selection()
        if not sel:
            return
        idx = self.channel_tree.index(sel[0])
        if idx >= len(self.player_channels):
            return

        ch = self.player_channels[idx]

        if ch.get("cmd"):
            self._play_channel_entry(ch)
            return

        genre_id = ch.get("id")
        genre_name = ch.get("name", ch.get("title", "?"))
        if genre_id:
            self.nav_stack.append({
                "label": genre_name,
                "channels": list(self.player_channels),
                "genre_id": str(genre_id),
            })
            self._update_nav_ui()
            self._fetch_genre_channels(str(genre_id))

    # ══════════════════════════════════════════════════════
    #  CHANNEL SEARCH / FILTER / SORT
    # ══════════════════════════════════════════════════════

    def _filter_channel_list(self, *args):
        query = self.channel_search_var.get().strip().lower()
        for item in self.channel_tree.get_children():
            self.channel_tree.delete(item)
        count = 0
        for ch in self.player_channels:
            num = ch.get("number", ch.get("id", ""))
            name = ch.get("name", ch.get("o_name", "?"))
            if query and query not in str(name).lower() \
                    and query not in str(num).lower():
                continue
            self.channel_tree.insert("", tk.END, values=(num, name))
            count += 1
        self.channel_count_label.configure(text=f"Kanały: {count}")

    def _sort_channel_list(self):
        self.player_channels.sort(
            key=lambda c: c.get("name", c.get("o_name", "")).lower())
        self._populate_channel_tree()
        self._log("Posortowano kanały A→Z.", "info")

    # ══════════════════════════════════════════════════════
    #  CHANNEL FETCHING — uses URL directly (no endpoint scan)
    # ══════════════════════════════════════════════════════

    def _fetch_channels(self):
        mac, url_raw, proxy = self._get_player_mac_url_proxy()
        if not mac:
            self._log("Wybierz MAC lub profil w panelu Player.", "error")
            return
        if not url_raw:
            url_raw = self.url_entry.get().strip()
        if not url_raw:
            self._log("Podaj URL serwera.", "error")
            return

        self._log(f"Pobieranie kanałów ({self.player_content_type}) "
                  f"dla {mac}...", "info")
        self.player_status_label.configure(text="Pobieranie kanałów...")
        self._set_progress(10, "Łączenie z serwerem...")

        threading.Thread(target=self._fetch_channels_worker,
                         args=(url_raw, mac, proxy),
                         daemon=True).start()

    def _fetch_channels_worker(self, url_raw, mac, proxy):
        timeout = self._get_timeout()
        url = parse_url(url_raw)
        base_cache_key = f"{url}|{mac}|{self.player_content_type}"
        genres_cache_key = f"{base_cache_key}|genres"

        # Try genres cache first
        cache = self._load_channels_cache()
        cached_genres = cache.get(genres_cache_key)
        if cached_genres:
            self.player_genres = cached_genres
            self.player_channels = list(cached_genres)
            count = len(cached_genres)
            self._log_safe(
                f"Załadowano {count} kategorii z cache "
                f"({self.player_content_type}).", "info")
            self._set_progress(100, f"Cache: {count} kategorii")
            self.root.after(0, self._populate_genre_menu)
            self.root.after(0, self._populate_channel_tree)
            self.root.after(0, lambda: self.player_status_label.configure(
                text=f"{count} kategorii (cache)"))

            # Still do handshake for token
            self._set_progress(90, "Handshake...")
            token, _ = get_handshake(
                url, mac, timeout=timeout, proxy=proxy)
            if token:
                self.player_token = token
                self._fetch_account_info_worker(url, mac, token, proxy)
            return

        self._set_progress(30, "Handshake...")
        token, hs_code = get_handshake(url, mac, timeout=timeout, proxy=proxy)
        if not token:
            self._log_safe(f"Handshake failed (HTTP {hs_code}).", "error")
            self._set_progress(100, "Błąd handshake")
            self.root.after(0, lambda: self.player_status_label.configure(
                text="Błąd połączenia"))
            return
        self.player_token = token

        # Fetch genres
        self._set_progress(50, "Pobieranie kategorii...")
        genres = get_genres(url, mac, token,
                            content_type=self.player_content_type,
                            timeout=timeout, proxy=proxy)
        self.player_genres = genres
        self.player_channels = list(genres)
        self.root.after(0, self._populate_genre_menu)
        self.root.after(0, self._populate_channel_tree)

        # Save genres to cache
        cache[genres_cache_key] = genres
        self._save_channels_cache(cache)

        self._log_safe(f"Załadowano {len(genres)} kategorii "
                       f"({self.player_content_type}).", "success")
        self._set_progress(100, f"Wybierz kategorię ({len(genres)})")
        self.root.after(0, lambda: self.player_status_label.configure(
            text=f"{len(genres)} kategorii — wybierz kategorię"))

        # Also fetch account info
        self._fetch_account_info_worker(url, mac, token, proxy)

    def _genre_channels_cache_key(self, url, mac, content_type, genre_id):
        return f"{url}|{mac}|{content_type}|genre|{genre_id}"

    def _fetch_genre_channels(self, genre_id):
        mac, url_raw, proxy = self._get_player_mac_url_proxy()
        if not mac or not self.player_token:
            return
        if not url_raw:
            url_raw = self.url_entry.get().strip()
        if not url_raw:
            return
        self._set_progress(30, "Pobieranie kategorii...")
        threading.Thread(
            target=self._fetch_genre_worker,
            args=(url_raw, mac, proxy, genre_id), daemon=True).start()

    def _fetch_channels_for_genre(self):
        mac, url_raw, proxy = self._get_player_mac_url_proxy()
        if not mac or not self.player_token:
            return
        genre_name = self.genre_var.get()
        if genre_name == "Wszystkie":
            self.player_channels = list(self.player_genres)
            self.root.after(0, self._populate_channel_tree)
            self.root.after(0, lambda: self.player_status_label.configure(
                text=f"{len(self.player_genres)} kategorii"))
            return
        else:
            genre_id = "*"
            for g in self.player_genres:
                name = g.get("title", g.get("name", ""))
                if name == genre_name:
                    genre_id = str(g.get("id", "*"))
                    break
        if not url_raw:
            url_raw = self.url_entry.get().strip()
        if not url_raw:
            return
        threading.Thread(
            target=self._fetch_genre_worker,
            args=(url_raw, mac, proxy, genre_id), daemon=True).start()

    def _fetch_genre_worker(self, url_raw, mac, proxy, genre_id):
        timeout = self._get_timeout()
        url = parse_url(url_raw)

        cache = self._load_channels_cache()
        genre_cache_key = self._genre_channels_cache_key(
            url, mac, self.player_content_type, genre_id)
        cached_items = cache.get(genre_cache_key)
        if cached_items is not None:
            self.player_channels = cached_items
            self._set_progress(100, f"Cache: {len(cached_items)} kanałów")
            self.root.after(0, self._populate_channel_tree)
            self.root.after(0, lambda: self.player_status_label.configure(
                text=f"{len(cached_items)} kanałów (cache)"))
            return

        items = []
        page = 1
        while True:
            batch = get_channels(url, mac, self.player_token,
                                 genre_id=genre_id,
                                 content_type=self.player_content_type,
                                 page=page, timeout=timeout, proxy=proxy)
            if not batch:
                break
            items.extend(batch)
            if len(batch) < 10:
                break
            page += 1
            if page > 50:
                break
        self.player_channels = items

        cache[genre_cache_key] = items
        self._save_channels_cache(cache)

        self._set_progress(100, f"{len(items)} kanałów")
        self.root.after(0, self._populate_channel_tree)

    def _populate_genre_menu(self):
        menu = self.genre_menu["menu"]
        menu.delete(0, "end")
        menu.add_command(label="Wszystkie",
                         command=lambda: self.genre_var.set("Wszystkie"))
        for g in self.player_genres:
            name = g.get("title", g.get("name", "?"))
            menu.add_command(label=name,
                             command=lambda n=name: self.genre_var.set(n))

    def _populate_channel_tree(self):
        query = self.channel_search_var.get().strip().lower()
        for item in self.channel_tree.get_children():
            self.channel_tree.delete(item)
        count = 0
        for ch in self.player_channels:
            num = ch.get("number", ch.get("id", ""))
            name = ch.get("name", ch.get("o_name", "?"))
            if query and query not in str(name).lower() \
                    and query not in str(num).lower():
                continue
            self.channel_tree.insert("", tk.END, values=(num, name))
            count += 1
        self.channel_count_label.configure(text=f"Kanały: {count}")

    # ══════════════════════════════════════════════════════
    #  ACCOUNT INFO (Info tab)
    # ══════════════════════════════════════════════════════

    def _fetch_account_info(self):
        mac, url_raw, proxy = self._get_player_mac_url_proxy()
        if not mac:
            self._log("Wybierz MAC lub profil aby pobrać info.", "warning")
            return
        if not url_raw:
            url_raw = self.url_entry.get().strip()
        if not url_raw:
            self._log("Podaj URL serwera.", "error")
            return
        self._log("Pobieranie informacji o koncie...", "info")
        self._set_progress(20, "Pobieranie info...")
        threading.Thread(target=self._fetch_account_info_thread,
                         args=(url_raw, mac, proxy),
                         daemon=True).start()

    def _fetch_account_info_thread(self, url_raw, mac, proxy):
        timeout = self._get_timeout()
        url = parse_url(url_raw)
        token, _ = get_handshake(url, mac, timeout=timeout, proxy=proxy)
        if not token:
            self._log_safe("Handshake failed.", "error")
            self._set_progress(100, "Błąd")
            return
        self._fetch_account_info_worker(url, mac, token, proxy)

    def _fetch_account_info_worker(self, url, mac, token, proxy):
        try:
            timeout = self._get_timeout()
            cookies = make_cookies(mac)
            params = make_params(mac, "get_main_info", "account_info")
            headers = {
                "User-Agent": random_user_agent(),
                "Accept": "*/*",
                "Authorization": f"Bearer {token}",
            }
            res = _request_get(url, params=params, headers=headers,
                               cookies=cookies, timeout=timeout, proxy=proxy)
            if res.status_code != 200:
                self._log_safe(f"Account info HTTP {res.status_code}",
                               "error")
                return

            js = res.json().get("js", {})
            if not js:
                return

            profile = {}
            try:
                params2 = make_params(mac, "get_profile", "stb")
                res2 = _request_get(url, params=params2, headers=headers,
                                    cookies=cookies, timeout=timeout,
                                    proxy=proxy)
                if res2.status_code == 200:
                    profile = res2.json().get("js", {})
            except Exception:
                pass

            info_lines = []
            info_lines.append(("URL:", url))
            info_lines.append(("MAC:", mac))
            info_lines.append(("─" * 40, ""))

            phone = js.get("phone", "?")
            info_lines.append(("Wygasa:", phone))

            for key, label in [
                ("mac", "MAC (serwer)"),
                ("ip", "IP"),
                ("login", "Login"),
                ("status", "Status"),
            ]:
                val = js.get(key, "")
                if val:
                    info_lines.append((f"{label}:", str(val)))

            if profile:
                info_lines.append(("─" * 40, ""))
                for key, label in [
                    ("name", "Nazwa profilu"),
                    ("sname", "Nazwa STB"),
                    ("stb_type", "Typ STB"),
                    ("timezone", "Strefa czasowa"),
                    ("locale", "Język"),
                ]:
                    val = profile.get(key, "")
                    if val:
                        info_lines.append((f"{label}:", str(val)))

            self.root.after(0, self._display_account_info, info_lines)
            self._set_progress(100, "Info pobrane")

        except Exception as e:
            self._log_safe(f"Błąd pobierania info: {e}", "error")
            self._set_progress(100, "Błąd")

    def _display_account_info(self, info_lines):
        self.info_text.configure(state=tk.NORMAL)
        self.info_text.delete("1.0", tk.END)
        for label, value in info_lines:
            if label.startswith("─"):
                self.info_text.insert(tk.END, f"{label}\n", "label")
            else:
                self.info_text.insert(tk.END, f"{label} ", "label")
                self.info_text.insert(tk.END, f"{value}\n",
                                      "highlight" if "Wygasa" in label
                                      else "value")
        self.info_text.configure(state=tk.DISABLED)

    # ══════════════════════════════════════════════════════
    #  MPV EMBEDDED PLAYER
    # ══════════════════════════════════════════════════════

    def _init_mpv(self):
        """Initialize mpv player embedded in the player_frame."""
        if not HAS_MPV:
            return
        try:
            wid = str(int(self.player_frame.winfo_id()))
            if sys.platform == "win32":
                vo = "gpu"
            elif sys.platform == "darwin":
                vo = "libmpv"
            else:
                vo = "gpu"
            self.mpv_player = mpv.MPV(
                wid=wid,
                vo=vo,
                input_default_bindings=True,
                input_vo_keyboard=True,
                osc=True,
            )
            self.mpv_player.volume = self.volume_scale.get()
        except Exception as e:
            self._log(f"Błąd inicjalizacji mpv: {e}", "error")
            self.mpv_player = None

    def _ensure_mpv(self):
        """Lazy-init mpv when first needed (needs visible window)."""
        if not HAS_MPV:
            return False
        if self.mpv_player is None:
            self._init_mpv()
        return self.mpv_player is not None

    def _mpv_play_url(self, stream_url):
        if not self._ensure_mpv():
            return False
        try:
            self.mpv_player.play(stream_url)
            return True
        except Exception as e:
            self._log_safe(f"mpv error: {e}", "error")
            return False

    def _player_play_pause(self):
        if self.mpv_player:
            try:
                paused = self.mpv_player.pause
                self.mpv_player.pause = not paused
                self.play_pause_btn.configure(
                    text="▶" if not paused else "⏸")
            except Exception:
                self._play_selected_channel()
        else:
            self._play_selected_channel()

    def _player_stop(self):
        if self.mpv_player:
            try:
                self.mpv_player.stop()
            except Exception:
                pass
        self.play_pause_btn.configure(text="▶")
        self.player_status_label.configure(text="Zatrzymano")
        self.current_stream_url = None

    def _player_prev(self):
        sel = self.channel_tree.selection()
        if not sel:
            return
        idx = self.channel_tree.index(sel[0])
        if idx > 0:
            children = self.channel_tree.get_children()
            self.channel_tree.selection_set(children[idx - 1])
            self.channel_tree.see(children[idx - 1])
            self._play_selected_channel()

    def _player_next(self):
        sel = self.channel_tree.selection()
        if not sel:
            children = self.channel_tree.get_children()
            if children:
                self.channel_tree.selection_set(children[0])
                self._play_selected_channel()
            return
        idx = self.channel_tree.index(sel[0])
        children = self.channel_tree.get_children()
        if idx < len(children) - 1:
            self.channel_tree.selection_set(children[idx + 1])
            self.channel_tree.see(children[idx + 1])
            self._play_selected_channel()

    def _on_volume_change(self, val):
        if self.mpv_player:
            try:
                self.mpv_player.volume = int(float(val))
            except Exception:
                pass

    def _player_fullscreen(self):
        if self.mpv_player:
            try:
                self.mpv_player.fullscreen = not bool(self.mpv_player.fullscreen)
                return
            except Exception:
                pass
        is_fs = self.root.attributes("-fullscreen")
        self.root.attributes("-fullscreen", not is_fs)

    # ══════════════════════════════════════════════════════
    #  PLAY / STREAM — uses URL directly
    # ══════════════════════════════════════════════════════

    def _play_selected_channel(self):
        sel = self.channel_tree.selection()
        if not sel:
            self._log("Zaznacz kanał do odtworzenia.", "warning")
            return
        idx = self.channel_tree.index(sel[0])
        if idx >= len(self.player_channels):
            return
        ch = self.player_channels[idx]
        self._play_channel_entry(ch)

    def _play_channel_entry(self, ch):
        cmd = ch.get("cmd", "")
        name = ch.get("name", ch.get("o_name", "?"))
        if not cmd:
            self._log(f"Brak strumienia: {name}", "error")
            return

        self._log(f"Odtwarzanie: {name}...", "info")
        self.player_status_label.configure(text=f"▶ {name}")
        self.play_pause_btn.configure(text="⏸")
        self._set_progress(30, f"Odtwarzanie: {name}")

        threading.Thread(target=self._play_stream_worker,
                         args=(cmd, name), daemon=True).start()

    def _play_stream_worker(self, cmd, name):
        mac, url_raw, proxy = self._get_player_mac_url_proxy()
        if not mac or not url_raw:
            self._log_safe("Brak MAC/URL. Wybierz profil.", "error")
            return

        timeout = self._get_timeout()
        url = parse_url(url_raw)

        if not self.player_token:
            self.player_token, _ = get_handshake(
                url, mac, timeout=timeout, proxy=proxy)
        if not self.player_token:
            self._log_safe("Nie udało się uzyskać tokena.", "error")
            self._set_progress(100, "Błąd")
            return

        stream_url = get_stream_url(
            url, mac, self.player_token, cmd,
            content_type=self.player_content_type,
            timeout=timeout, proxy=proxy)
        if not stream_url:
            self._log_safe(f"Nie udało się pobrać URL: {name}", "error")
            self._set_progress(100, "Błąd")
            return

        self._log_safe(f"Stream: {stream_url}", "success")
        self._set_progress(100, f"▶ {name}")
        self.current_stream_url = stream_url

        # Play in embedded mpv on UI thread
        self.root.after(0, self._play_stream_on_ui, stream_url)

    def _play_stream_on_ui(self, stream_url):
        ok = self._mpv_play_url(stream_url)
        if ok:
            self._log("Odtwarzanie w wbudowanym mpv.", "success")
        else:
            self._log(
                "mpv niedostępny. Sprawdź libmpv-2.dll i python-mpv (Windows).",
                "error")

    def _copy_channel_url(self):
        sel = self.channel_tree.selection()
        if not sel:
            self._log("Zaznacz kanał.", "warning")
            return
        idx = self.channel_tree.index(sel[0])
        if idx >= len(self.player_channels):
            return
        ch = self.player_channels[idx]
        cmd = ch.get("cmd", "")
        name = ch.get("name", "?")
        if not cmd:
            self._log(f"Brak strumienia: {name}", "error")
            return
        self._log(f"Pobieranie URL: {name}...", "info")
        threading.Thread(target=self._copy_url_worker,
                         args=(cmd, name), daemon=True).start()

    def _copy_url_worker(self, cmd, name):
        mac, url_raw, proxy = self._get_player_mac_url_proxy()
        if not mac or not url_raw:
            self._log_safe("Brak MAC/URL.", "error")
            return
        timeout = self._get_timeout()
        url = parse_url(url_raw)
        if not self.player_token:
            self.player_token, _ = get_handshake(
                url, mac, timeout=timeout, proxy=proxy)
        if not self.player_token:
            return
        stream_url = get_stream_url(
            url, mac, self.player_token, cmd,
            content_type=self.player_content_type,
            timeout=timeout, proxy=proxy)
        if not stream_url:
            self._log_safe(f"Nie udało się pobrać URL: {name}", "error")
            return
        self.root.after(0, lambda: self._do_copy(stream_url, name))

    def _do_copy(self, url, name):
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self._log(f"Skopiowano URL: {name} → {url}", "success")

    # ══════════════════════════════════════════════════════
    #  SCANNING (with full proxy retry)
    # ══════════════════════════════════════════════════════

    def _toggle_start(self):
        if self.is_running:
            return
        self._start_scan()

    def _start_scan(self):
        url_raw = self.url_entry.get().strip()
        mac_prefix = self.mac_entry.get().strip()
        workers_str = self.workers_entry.get().strip()
        timeout = self._get_timeout()

        if not url_raw:
            self._log("Podaj adres URL serwera!", "error")
            return
        if len(mac_prefix) < 8:
            self._log("Pierwsze 3 bajty MAC: XX:XX:XX", "error")
            return

        try:
            workers = int(workers_str)
        except ValueError:
            workers = 10

        self.is_running = True
        self.is_paused = False
        self.stop_event.clear()
        self.pause_event.set()
        self.checked_count = 0
        self.found_count = 0
        self._update_stats()

        self._btn_disable(self.start_btn)
        self._btn_enable(self.pause_btn)
        self._btn_enable(self.stop_btn)
        self._set_status("Uruchamianie...", "#ffaa00")
        self._set_progress(5, "Uruchamianie skanera...")

        server_address = parse_url(url_raw)
        self.scan_thread = threading.Thread(
            target=self._scan_worker,
            args=(server_address, mac_prefix, workers, timeout),
            daemon=True)
        self.scan_thread.start()

    def _scan_worker(self, server_address, mac_prefix, workers, timeout):
        self._log_safe(f"Szukam endpoint-u na {server_address}...", "info")
        self._set_status("Szukanie endpoint-u...", "#55aaff")
        self._set_progress(15, "Szukanie endpoint-u...")

        # Use the full proxy retry helper
        endpoint, proxy = self._find_endpoint_with_proxy_retry(
            server_address, timeout)

        if self.stop_event.is_set():
            self._scan_finished()
            return

        if not endpoint:
            self._set_progress(100, "Serwer nie odpowiada")
            self._scan_finished()
            return

        url = server_address + endpoint
        self._log_safe(f"Endpoint: {url}", "success")
        if proxy:
            self._log_safe(f"Proxy: {proxy}", "info")
        self._set_status("Skanowanie...", "#00ff88")
        self._set_progress(30, "Skanowanie...")

        self.executor = ThreadPoolExecutor(max_workers=workers)
        futures = []
        try:
            while not self.stop_event.is_set():
                self.pause_event.wait()
                if self.stop_event.is_set():
                    break
                for _ in range(workers * 2):
                    if self.stop_event.is_set():
                        break
                    futures.append(self.executor.submit(
                        self._check_single_mac, url, mac_prefix, timeout))
                remaining = []
                for f in futures:
                    if f.done():
                        try:
                            f.result()
                        except Exception:
                            pass
                    else:
                        remaining.append(f)
                futures = remaining
                time.sleep(0.05)
        except Exception as e:
            self._log_safe(f"Błąd: {e}", "error")
        finally:
            self.executor.shutdown(wait=False, cancel_futures=True)
            self._scan_finished()

    def _check_single_mac(self, url, mac_prefix, timeout):
        if self.stop_event.is_set():
            return
        self.pause_event.wait()

        mac = generate_random_mac(mac_prefix)
        proxy = self._get_active_proxy()

        result = check_mac(url, mac, timeout=timeout, proxy=proxy)
        codes = result.get("codes", [])
        # Show only the last (most relevant) HTTP code
        last_code = codes[-1] if codes else "?"
        elapsed = result.get("elapsed_ms", 0)
        time_tag = f"{elapsed:.1f}ms"
        error_msg = result.get("error", "")

        self.checked_count += 1
        self._update_stats_safe()

        # Verbose logging
        if self.verbose_logs_var.get():
            req_info = result.get("request_info", "")
            res_info = result.get("response_info", "")
            if req_info:
                self._log_safe(f"  ➡ {req_info}", "dim")
            if res_info:
                self._log_safe(f"  ⬅ {res_info[:300]}", "dim")

        # Timeout → remove proxy and rotate
        if error_msg == "Timeout" and proxy:
            self._log_safe(
                f"⏱ Timeout {time_tag} → usuwam proxy: {proxy}",
                "warning")
            remove_proxy(proxy)
            self.root.after(0, self._refresh_proxy_tree)
            new_proxy = rotate_proxy()
            if new_proxy:
                self._log_safe(f"Zmiana proxy → {new_proxy}", "info")
            return

        if result["found"]:
            if proxy:
                report_proxy_success(proxy)

            # Check channel count
            ch_count = 0
            try:
                ch_count = count_channels_quick(
                    url, mac, timeout=timeout, proxy=proxy)
            except Exception:
                pass

            # Min channels filter
            try:
                min_ch = int(self.min_channels_entry.get().strip())
            except (ValueError, AttributeError):
                min_ch = 0

            if min_ch > 0 and ch_count < min_ch:
                self._log_safe(
                    f"⚠ [{last_code}] {time_tag} {mac} → "
                    f"{ch_count} kanałów (min: {min_ch}), pomijam",
                    "warning")
                return

            self.found_count += 1
            self._update_stats_safe()
            self._log_safe(
                f"✅ [{last_code}] {time_tag} ZNALEZIONO: {mac} → "
                f"{result['expiry']} ({ch_count} kanałów)", "success")
            self._add_active_mac(url, mac, result["expiry"], proxy,
                                 channels=ch_count)
            self._auto_save()
        else:
            # Handle proxy failures for bad codes
            for code in codes:
                if code and should_remove_proxy(code) and proxy:
                    self._handle_proxy_fail(proxy, code)
                    break

            if self.checked_count % 25 == 0:
                self._log_safe(
                    f"[{last_code}] {time_tag} Sprawdzono "
                    f"{self.checked_count}, "
                    f"znaleziono {self.found_count}...", "info")
            else:
                self._log_safe(
                    f"[{last_code}] {time_tag} {mac}", "dim")

    def _scan_finished(self):
        self.is_running = False
        self.is_paused = False
        self._set_status("Zakończono", "#888888")
        self._set_progress(100, "Skanowanie zakończone")
        self.root.after(0, self._reset_buttons)
        self._log_safe(
            f"Zakończono. Sprawdzono: {self.checked_count}, "
            f"Znaleziono: {self.found_count}", "info")
        self._auto_save()

    def _reset_buttons(self):
        self._btn_enable(self.start_btn)
        self._btn_disable(self.pause_btn)
        self.pause_btn.configure(text="⏸ PAUZA")
        self._btn_disable(self.stop_btn)

    def _toggle_pause(self):
        if not self.is_running:
            return
        if self.is_paused:
            self.is_paused = False
            self.pause_event.set()
            self.pause_btn._normal_bg = "#c78d00"
            self.pause_btn._hover_bg = "#a87600"
            self.pause_btn.configure(text="⏸ PAUZA", bg="#c78d00")
            self._set_status("Skanowanie...", "#00ff88")
            self._log("▶  Wznowiono.", "info")
        else:
            self.is_paused = True
            self.pause_event.clear()
            self.pause_btn._normal_bg = "#00b359"
            self.pause_btn._hover_bg = "#009945"
            self.pause_btn.configure(text="▶ WZNÓW", bg="#00b359")
            self._set_status("Wstrzymano", "#ffaa00")
            self._log("⏸  Pauza.", "warning")

    def _stop_scan(self):
        if not self.is_running:
            return
        self._log("⏹  Zatrzymywanie...", "warning")
        self.stop_event.set()
        self.pause_event.set()

    # ══════════════════════════════════════════════════════
    #  CLOSE
    # ══════════════════════════════════════════════════════

    def _on_close(self):
        self.stop_event.set()
        self.pause_event.set()
        if self.mpv_player:
            try:
                self.mpv_player.terminate()
            except Exception:
                pass
        self._save_session()
        self._auto_save()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = App()
    app.run()
