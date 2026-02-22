"""
Flipper — MAC Address Scanner + IPTV Player
Plain Tkinter — macOS compatible.
Features: proxy rotation, session persistence, profiles, embedded VLC player.
"""

import os
import sys
import subprocess
import platform

if sys.platform == "darwin":
    os.environ["TK_SILENCE_DEPRECATION"] = "1"

import tkinter as tk
from tkinter import ttk, filedialog
import threading
import time
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

try:
    import vlc
    HAS_VLC = True
except (ImportError, OSError):
    HAS_VLC = False

from scanner import (
    generate_random_mac, check_mac, get_responding_endpoint, parse_url,
    get_handshake, get_genres, get_channels, get_stream_url,
    fetch_free_proxies, set_proxy_list, get_proxy_list, add_proxy,
    remove_proxy, get_current_proxy, rotate_proxy, report_proxy_fail,
    report_proxy_success, should_remove_proxy,
)
from constants import RESULTS_FILE, SESSION_FILE

MAX_LOG_SAVE = 500
BG_DARK = "#0a0a1e"
BG_SIDEBAR = "#1a1a2e"
BG_INPUT = "#12122a"
BG_BAR = "#16162a"
FG_DIM = "#888888"
ACCENT = "#2563eb"


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
        self.vlc_instance = None
        self.vlc_player = None
        self.current_tab = 0

        self._setup_styles()
        self._build_gui()
        self._load_session()
        self._auto_fetch_proxies_on_startup()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

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

    # ══════════════════════════════════════════════════════
    #  BUILD GUI
    # ══════════════════════════════════════════════════════

    def _build_gui(self):
        # LEFT sidebar container
        self.left = tk.Frame(self.root, width=270, bg=BG_SIDEBAR,
                             highlightthickness=0, bd=0)
        self.left.pack(side=tk.LEFT, fill=tk.Y)
        self.left.pack_propagate(False)

        # Two sidebar modes (placed on top of each other)
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
                       "📺 Player", "👤 Profile"]
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

        self.save_var = tk.BooleanVar(value=True)
        tk.Checkbutton(left, text="Zapisuj do pliku",
                       variable=self.save_var, bg=BG_SIDEBAR,
                       fg="#aaaaaa", selectcolor=BG_INPUT,
                       activebackground=BG_SIDEBAR,
                       activeforeground="#cccccc",
                       font=("Helvetica", 11)).pack(
            anchor=tk.W, padx=16, pady=(2, 4))

        self._make_btn(left, "📁 Eksportuj wyniki", "#333355", "#444466",
                       self._export_results).pack(
            fill=tk.X, padx=16, pady=(2, 6))
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

    # ── Sidebar: Player ───────────────────────────────────
    def _build_sidebar_player(self, left):
        tk.Label(left, text="📺 PLAYER",
                 font=("Helvetica", 22, "bold"),
                 bg=BG_SIDEBAR, fg="#00d4ff").pack(pady=(14, 1))
        self._sep(left)

        # Active profile label
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

        # -- Sub-page 0: MAC list --
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
        self._make_btn(bot, "📡 Pobierz kanały", ACCENT, "#1d4ed8",
                       self._fetch_channels).pack(
            fill=tk.X, ipady=4, pady=(2, 2))

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

    # ── Page 1: Active MACs ───────────────────────────────
    def _build_page_active(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        tf = tk.Frame(page, bg=BG_DARK)
        tf.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(
            tf, columns=("url", "mac", "expiry", "proxy"),
            show="headings")
        self.tree.heading("url", text="URL")
        self.tree.heading("mac", text="Adres MAC")
        self.tree.heading("expiry", text="Data ważności")
        self.tree.heading("proxy", text="Proxy")
        self.tree.column("url", width=260, minwidth=120)
        self.tree.column("mac", width=180, minwidth=120)
        self.tree.column("expiry", width=220, minwidth=120)
        self.tree.column("proxy", width=180, minwidth=100)

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

    # ── Page 3: Player ────────────────────────────────────
    def _build_page_player(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        # RIGHT channel panel (pack first for fixed width)
        right_panel = tk.Frame(page, bg=BG_DARK, width=320)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y)
        right_panel.pack_propagate(False)

        # Content type buttons at top of right panel
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

        # Genre dropdown
        genre_frame = tk.Frame(right_panel, bg=BG_DARK)
        genre_frame.pack(fill=tk.X, padx=4, pady=(2, 4))
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

        # Channel list
        ch_frame = tk.Frame(right_panel, bg=BG_DARK)
        ch_frame.pack(fill=tk.BOTH, expand=True, padx=4)

        self.channel_tree = ttk.Treeview(
            ch_frame, columns=("num", "name"), show="headings")
        self.channel_tree.heading("num", text="#")
        self.channel_tree.heading("name", text="Kanał / Tytuł")
        self.channel_tree.column("num", width=45, minwidth=35)
        self.channel_tree.column("name", width=250, minwidth=120)
        ch_sb = ttk.Scrollbar(ch_frame, orient=tk.VERTICAL,
                              command=self.channel_tree.yview)
        self.channel_tree.configure(yscrollcommand=ch_sb.set)
        ch_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.channel_tree.pack(fill=tk.BOTH, expand=True)
        self.channel_tree.bind("<Double-1>",
                               lambda e: self._play_selected_channel())

        self.channel_count_label = tk.Label(
            right_panel, text="Kanały: 0", font=("Helvetica", 10),
            bg=BG_DARK, fg=FG_DIM)
        self.channel_count_label.pack(pady=(2, 4))

        # CENTER player + controls
        center = tk.Frame(page, bg="#000000")
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Player area
        self.player_frame = tk.Frame(center, bg="#000000")
        self.player_frame.pack(fill=tk.BOTH, expand=True)

        if HAS_VLC:
            self._init_vlc()
        else:
            tk.Label(self.player_frame,
                     text="Zainstaluj VLC aby odtwarzać tutaj\n"
                          "Strumienie otworzą się w zewnętrznym odtwarzaczu",
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

    # ── Page 4: Profiles ──────────────────────────────────
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
        self._make_btn(bot, "🗑 Usuń profil", "#cc3333", "#aa2222",
                       self._delete_profile).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)

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
        lbl.bind("<Button-1>", lambda e: lbl._command() if lbl._enabled else None)
        lbl.bind("<Enter>", lambda e: lbl.configure(bg=lbl._hover_bg)
                 if lbl._enabled else None)
        lbl.bind("<Leave>", lambda e: lbl.configure(bg=lbl._normal_bg)
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
        # Sidebar
        if idx == 3:  # Player
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

    def _add_active_mac(self, url, mac, expiry, proxy=None):
        entry = {"url": url, "mac": mac, "expiry": expiry,
                 "proxy": proxy or ""}
        self.active_macs.append(entry)
        if proxy:
            self.mac_proxy_map[mac] = proxy
        self.root.after(0, self._insert_mac_row, entry)

    def _insert_mac_row(self, entry):
        self.tree.insert("", tk.END,
                         values=(entry["url"], entry["mac"],
                                 entry["expiry"], entry["proxy"]))
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
        sel = self.tree.selection()
        if not sel:
            self._log("Zaznacz MAC do zapisania jako profil.", "warning")
            return
        vals = self.tree.item(sel[0], "values")
        url, mac, expiry, proxy = vals[0], vals[1], vals[2], vals[3]
        name = f"Profil {len(self.profiles) + 1}"
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
                        f"{m.get('proxy', '')}\n")
        self._log(f"Wyeksportowano {len(self.active_macs)} wyników.", "success")

    def _auto_save(self):
        if not self.save_var.get() or not self.active_macs:
            return
        try:
            with open(RESULTS_FILE, "w", encoding="utf-8") as f:
                f.write("# Flipper — auto-zapis\n")
                for m in self.active_macs:
                    f.write(f"{m['mac']} | {m['expiry']} | {m['url']} | "
                            f"{m.get('proxy', '')}\n")
        except Exception:
            pass

    # ══════════════════════════════════════════════════════
    #  SESSION PERSISTENCE (logs, macs, proxies, profiles)
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
        }
        try:
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _load_session(self):
        if not os.path.exists(SESSION_FILE):
            return
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        # Settings
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

        # Counters
        self.checked_count = data.get("checked_count", 0)
        self.found_count = data.get("found_count", 0)
        self._update_stats()

        # Active MACs
        for m in data.get("active_macs", []):
            self.active_macs.append(m)
            self._insert_mac_row(m)

        # MAC-proxy map
        self.mac_proxy_map = data.get("mac_proxy_map", {})

        # Logs
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

        # Proxies
        saved_proxies = data.get("proxies", [])
        if saved_proxies:
            set_proxy_list(saved_proxies)
            self._refresh_proxy_tree()

        # Profiles
        self.profiles = data.get("profiles", [])
        self._refresh_profile_tree()

        self.active_profile = data.get("active_profile", None)
        if self.active_profile:
            self.active_profile_label.configure(
                text=f"Aktywny: {self.active_profile.get('name', '?')}")

    # ══════════════════════════════════════════════════════
    #  PROXY TAB LOGIC
    # ══════════════════════════════════════════════════════

    def _get_active_proxy(self):
        """Inline field first, then rotating list."""
        inline = self.proxy_inline_entry.get().strip()
        if inline:
            if not inline.startswith("http"):
                inline = "http://" + inline
            return inline
        return get_current_proxy()

    def _get_proxy_for_mac(self, mac):
        """Get the proxy that was used to find this MAC."""
        return self.mac_proxy_map.get(mac)

    def _auto_fetch_proxies_on_startup(self):
        """Fetch proxies on startup if none loaded from session."""
        if not get_proxy_list():
            self._log("Auto-pobieranie proxy przy starcie...", "info")
            threading.Thread(target=self._fetch_proxies_worker,
                             daemon=True).start()
        else:
            self._log(f"Załadowano {len(get_proxy_list())} proxy z sesji.",
                      "info")

    def _fetch_proxies(self):
        self._log("Pobieranie listy proxy z API...", "info")
        threading.Thread(target=self._fetch_proxies_worker,
                         daemon=True).start()

    def _fetch_proxies_worker(self):
        proxies = fetch_free_proxies()
        if proxies:
            set_proxy_list(proxies)
            self._log_safe(f"Pobrano {len(proxies)} proxy.", "success")
            self.root.after(0, self._refresh_proxy_tree)
        else:
            self._log_safe("Nie udało się pobrać proxy.", "error")

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
        """Handle proxy failure — rotate and possibly remove."""
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
            self._log(f"Aktywny profil: {self.active_profile['name']}", "info")

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
            self._log(f"Usunięto profil: {removed['name']}", "info")

    # ══════════════════════════════════════════════════════
    #  PLAYER SIDEBAR HELPERS
    # ══════════════════════════════════════════════════════

    def _refresh_player_mac_list(self):
        self.player_mac_listbox.delete(0, tk.END)
        for m in self.active_macs:
            text = f"{m['mac']}  [{m['url'][:30]}]"
            self.player_mac_listbox.insert(tk.END, text)

    def _refresh_player_profile_list(self):
        self.player_profile_listbox.delete(0, tk.END)
        for p in self.profiles:
            text = f"{p['name']}  ({p['mac']})"
            self.player_profile_listbox.insert(tk.END, text)

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

    def _on_player_profile_select(self, event):
        sel = self.player_profile_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < len(self.profiles):
            self.active_profile = self.profiles[idx]
            self.active_profile_label.configure(
                text=f"Aktywny: {self.active_profile['name']}")

    def _get_player_mac_url_proxy(self):
        """Get MAC, URL, proxy for the currently selected player source."""
        if self.active_profile:
            mac = self.active_profile.get("mac", "")
            url = self.active_profile.get("url", "")
            proxy = self.active_profile.get("proxy", "")
            if not proxy:
                proxy = self._get_proxy_for_mac(mac)
            if not url:
                url = self.url_entry.get().strip()
            return mac, url, proxy or None
        return None, None, None

    # ══════════════════════════════════════════════════════
    #  PLAYER CONTENT TYPE + GENRES
    # ══════════════════════════════════════════════════════

    def _switch_content_type(self, ctype):
        self.player_content_type = ctype
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
        """When genre dropdown changes, re-fetch channels for that genre."""
        self._fetch_channels_for_genre()

    # ══════════════════════════════════════════════════════
    #  CHANNEL FETCHING
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

        threading.Thread(target=self._fetch_channels_worker,
                         args=(url_raw, mac, proxy),
                         daemon=True).start()

    def _fetch_channels_worker(self, url_raw, mac, proxy):
        server = parse_url(url_raw)
        endpoint, ep_code = get_responding_endpoint(
            server, timeout=5, proxy=proxy)
        if not endpoint:
            self._log_safe(f"Serwer nie odpowiada (HTTP {ep_code}).", "error")
            if proxy and ep_code and should_remove_proxy(ep_code):
                self._handle_proxy_fail(proxy, ep_code)
            self.root.after(0, lambda: self.player_status_label.configure(
                text="Błąd połączenia"))
            return

        url = server + endpoint
        token, hs_code = get_handshake(url, mac, timeout=5, proxy=proxy)
        if not token:
            self._log_safe(f"Handshake failed (HTTP {hs_code}).", "error")
            return
        self.player_token = token

        # Fetch genres
        genres = get_genres(url, mac, token,
                            content_type=self.player_content_type,
                            timeout=5, proxy=proxy)
        self.player_genres = genres
        self.root.after(0, self._populate_genre_menu)

        # Fetch all channels (first load = all)
        all_items = []
        page = 1
        while True:
            items = get_channels(url, mac, token, genre_id="*",
                                 content_type=self.player_content_type,
                                 page=page, timeout=5, proxy=proxy)
            if not items:
                break
            all_items.extend(items)
            if len(items) < 10:
                break
            page += 1
            if page > 50:
                break

        self.player_channels = all_items
        self._log_safe(f"Pobrano {len(all_items)} elementów "
                       f"({self.player_content_type}).", "success")
        self.root.after(0, self._populate_channel_tree)
        self.root.after(0, lambda: self.player_status_label.configure(
            text=f"{len(all_items)} kanałów"))

    def _fetch_channels_for_genre(self):
        """Re-fetch channels for selected genre (from dropdown)."""
        mac, url_raw, proxy = self._get_player_mac_url_proxy()
        if not mac or not self.player_token:
            return
        genre_name = self.genre_var.get()
        if genre_name == "Wszystkie":
            genre_id = "*"
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
        server = parse_url(url_raw)
        endpoint, _ = get_responding_endpoint(server, timeout=5, proxy=proxy)
        if not endpoint:
            return
        url = server + endpoint
        items = []
        page = 1
        while True:
            batch = get_channels(url, mac, self.player_token,
                                 genre_id=genre_id,
                                 content_type=self.player_content_type,
                                 page=page, timeout=5, proxy=proxy)
            if not batch:
                break
            items.extend(batch)
            if len(batch) < 10:
                break
            page += 1
            if page > 50:
                break
        self.player_channels = items
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
        for item in self.channel_tree.get_children():
            self.channel_tree.delete(item)
        for ch in self.player_channels:
            num = ch.get("number", ch.get("id", ""))
            name = ch.get("name", ch.get("o_name", "?"))
            self.channel_tree.insert("", tk.END, values=(num, name))
        self.channel_count_label.configure(
            text=f"Kanały: {len(self.player_channels)}")

    # ══════════════════════════════════════════════════════
    #  VLC PLAYER
    # ══════════════════════════════════════════════════════

    def _init_vlc(self):
        try:
            self.vlc_instance = vlc.Instance("--no-xlib")
            self.vlc_player = self.vlc_instance.media_player_new()
        except Exception:
            pass

    def _vlc_play_url(self, stream_url):
        if not HAS_VLC or not self.vlc_player:
            return False
        try:
            media = self.vlc_instance.media_new(stream_url)
            self.vlc_player.set_media(media)
            # Set the window for video output
            if sys.platform == "darwin":
                self.vlc_player.set_nsobject(
                    self.player_frame.winfo_id())
            elif sys.platform == "win32":
                self.vlc_player.set_hwnd(
                    self.player_frame.winfo_id())
            else:
                self.vlc_player.set_xwindow(
                    self.player_frame.winfo_id())
            self.vlc_player.play()
            self.vlc_player.audio_set_volume(self.volume_scale.get())
            return True
        except Exception:
            return False

    def _player_play_pause(self):
        if HAS_VLC and self.vlc_player:
            if self.vlc_player.is_playing():
                self.vlc_player.pause()
                self.play_pause_btn.configure(text="▶")
            else:
                self.vlc_player.play()
                self.play_pause_btn.configure(text="⏸")
        else:
            self._play_selected_channel()

    def _player_stop(self):
        if HAS_VLC and self.vlc_player:
            self.vlc_player.stop()
            self.play_pause_btn.configure(text="▶")
            self.player_status_label.configure(text="Zatrzymano")

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
        if HAS_VLC and self.vlc_player:
            try:
                self.vlc_player.audio_set_volume(int(float(val)))
            except Exception:
                pass

    def _player_fullscreen(self):
        if HAS_VLC and self.vlc_player:
            self.vlc_player.toggle_fullscreen()
        else:
            # Toggle root window fullscreen
            is_fs = self.root.attributes("-fullscreen")
            self.root.attributes("-fullscreen", not is_fs)

    # ══════════════════════════════════════════════════════
    #  PLAY / STREAM
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
        cmd = ch.get("cmd", "")
        name = ch.get("name", ch.get("o_name", "?"))
        if not cmd:
            self._log(f"Brak strumienia: {name}", "error")
            return

        self._log(f"Odtwarzanie: {name}...", "info")
        self.player_status_label.configure(text=f"▶ {name}")
        self.play_pause_btn.configure(text="⏸")

        threading.Thread(target=self._play_stream_worker,
                         args=(cmd, name), daemon=True).start()

    def _play_stream_worker(self, cmd, name):
        mac, url_raw, proxy = self._get_player_mac_url_proxy()
        if not mac or not url_raw:
            self._log_safe("Brak MAC/URL. Wybierz profil.", "error")
            return

        server = parse_url(url_raw)
        endpoint, ep_code = get_responding_endpoint(
            server, timeout=5, proxy=proxy)
        if not endpoint:
            self._log_safe(f"Serwer nie odpowiada (HTTP {ep_code}).", "error")
            return
        url = server + endpoint

        if not self.player_token:
            self.player_token, _ = get_handshake(
                url, mac, timeout=5, proxy=proxy)
        if not self.player_token:
            self._log_safe("Nie udało się uzyskać tokena.", "error")
            return

        stream_url = get_stream_url(
            url, mac, self.player_token, cmd,
            content_type=self.player_content_type,
            timeout=5, proxy=proxy)
        if not stream_url:
            self._log_safe(f"Nie udało się pobrać URL: {name}", "error")
            return

        self._log_safe(f"Stream: {stream_url}", "success")

        # Try VLC first
        if HAS_VLC and self.vlc_player:
            ok = self._vlc_play_url(stream_url)
            if ok:
                self._log_safe("Odtwarzanie w wbudowanym VLC.", "success")
                return

        # Fallback: external player
        try:
            if platform.system() == "Darwin":
                vlc_path = "/Applications/VLC.app/Contents/MacOS/VLC"
                if os.path.exists(vlc_path):
                    subprocess.Popen([vlc_path, stream_url])
                else:
                    subprocess.Popen(["open", stream_url])
            elif platform.system() == "Windows":
                os.startfile(stream_url)
            else:
                subprocess.Popen(["xdg-open", stream_url])
            self._log_safe("Otworzono w zewnętrznym odtwarzaczu.", "success")
        except Exception as e:
            self._log_safe(f"Błąd odtwarzacza: {e}", "error")
            self._log_safe(f"URL: {stream_url}", "info")

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
        server = parse_url(url_raw)
        endpoint, _ = get_responding_endpoint(
            server, timeout=5, proxy=proxy)
        if not endpoint:
            self._log_safe("Serwer nie odpowiada.", "error")
            return
        url = server + endpoint
        if not self.player_token:
            self.player_token, _ = get_handshake(
                url, mac, timeout=5, proxy=proxy)
        if not self.player_token:
            return
        stream_url = get_stream_url(
            url, mac, self.player_token, cmd,
            content_type=self.player_content_type,
            timeout=5, proxy=proxy)
        if not stream_url:
            self._log_safe(f"Nie udało się pobrać URL: {name}", "error")
            return
        self.root.after(0, lambda: self._do_copy(stream_url, name))

    def _do_copy(self, url, name):
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self._log(f"Skopiowano URL: {name} → {url}", "success")

    # ══════════════════════════════════════════════════════
    #  SCANNING
    # ══════════════════════════════════════════════════════

    def _toggle_start(self):
        if self.is_running:
            return
        self._start_scan()

    def _start_scan(self):
        url_raw = self.url_entry.get().strip()
        mac_prefix = self.mac_entry.get().strip()
        workers_str = self.workers_entry.get().strip()
        timeout_str = self.timeout_entry.get().strip()

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
        try:
            timeout = int(timeout_str)
        except ValueError:
            timeout = 5

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

        server_address = parse_url(url_raw)
        self.scan_thread = threading.Thread(
            target=self._scan_worker,
            args=(server_address, mac_prefix, workers, timeout),
            daemon=True)
        self.scan_thread.start()

    def _scan_worker(self, server_address, mac_prefix, workers, timeout):
        self._log_safe(f"Szukam endpoint-u na {server_address}...", "info")
        self._set_status("Szukanie endpoint-u...", "#55aaff")

        proxy = self._get_active_proxy()
        if proxy:
            self._log_safe(f"Proxy: {proxy}", "info")

        endpoint, ep_code = get_responding_endpoint(
            server_address, timeout=timeout, proxy=proxy)
        self._log_safe(f"Endpoint scan (HTTP {ep_code})", "dim")

        if self.stop_event.is_set():
            self._scan_finished()
            return
        if not endpoint:
            if proxy and ep_code and should_remove_proxy(ep_code):
                self._handle_proxy_fail(proxy, ep_code)
                proxy = self._get_active_proxy()
                if proxy:
                    endpoint, ep_code = get_responding_endpoint(
                        server_address, timeout=timeout, proxy=proxy)
            if not endpoint:
                self._log_safe(f"Serwer nie odpowiada (HTTP {ep_code})!",
                               "error")
                self._scan_finished()
                return

        url = server_address + endpoint
        self._log_safe(f"Endpoint: {url}", "success")
        self._set_status("Skanowanie...", "#00ff88")

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
        codes_str = "/".join(str(c) for c in codes) if codes else "?"

        self.checked_count += 1
        self._update_stats_safe()

        if result["found"]:
            if proxy:
                report_proxy_success(proxy)
            self.found_count += 1
            self._update_stats_safe()
            self._log_safe(
                f"✅ [{codes_str}] ZNALEZIONO: {mac} → "
                f"{result['expiry']}", "success")
            self._add_active_mac(url, mac, result["expiry"], proxy)
            self._auto_save()
        else:
            # Check proxy bad status
            for code in codes:
                if code and should_remove_proxy(code) and proxy:
                    self._handle_proxy_fail(proxy, code)
                    break

            if self.checked_count % 25 == 0:
                self._log_safe(
                    f"[{codes_str}] Sprawdzono {self.checked_count}, "
                    f"znaleziono {self.found_count}...", "info")
            else:
                self._log_safe(f"[{codes_str}] {mac}", "dim")

    def _scan_finished(self):
        self.is_running = False
        self.is_paused = False
        self._set_status("Zakończono", "#888888")
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
        if HAS_VLC and self.vlc_player:
            try:
                self.vlc_player.stop()
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
