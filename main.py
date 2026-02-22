"""
Flipper — MAC Address Scanner + IPTV Player with Proxy support
Plain Tkinter — macOS compatible
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

from scanner import (
    generate_random_mac, check_mac, get_responding_endpoint, parse_url,
    get_handshake, get_genres, get_channels, get_stream_url,
    fetch_free_proxies, set_proxy_list, get_proxy_list, add_proxy,
    remove_proxy, get_current_proxy, rotate_proxy, report_proxy_fail,
    report_proxy_success,
)
from constants import RESULTS_FILE, SESSION_FILE


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Flipper — MAC Scanner & Player")
        self.root.geometry("1200x750")
        self.root.minsize(1000, 600)

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
        self.active_macs = []  # [{url, mac, expiry}, ...]

        # Player state
        self.player_token = None
        self.player_channels = []
        self.current_tab = 0

        self._setup_styles()
        self._build_gui()
        self._load_session()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Styles ─────────────────────────────────────────────
    def _setup_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("Treeview",
                        background="#1e1e3a",
                        foreground="#d0d0e8",
                        fieldbackground="#1e1e3a",
                        rowheight=28,
                        font=("Menlo", 11))
        style.configure("Treeview.Heading",
                        background="#2a2a4a",
                        foreground="#ffffff",
                        font=("Menlo", 11, "bold"))
        style.map("Treeview",
                  background=[("selected", "#2563eb")])

    # ── Build GUI ──────────────────────────────────────────
    def _build_gui(self):
        # We use a container so we can swap sidebar content per tab
        self.left = tk.Frame(self.root, width=270, bg="#1a1a2e",
                             highlightthickness=0, bd=0)
        self.left.pack(side=tk.LEFT, fill=tk.Y)
        self.left.pack_propagate(False)

        # We'll store sidebar frames per-mode and lift them
        self.sidebar_scanner = tk.Frame(self.left, bg="#1a1a2e")
        self.sidebar_scanner.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.sidebar_player = tk.Frame(self.left, bg="#1a1a2e")
        self.sidebar_player.place(relx=0, rely=0, relwidth=1, relheight=1)

        self._build_sidebar_scanner(self.sidebar_scanner)
        self._build_sidebar_player(self.sidebar_player)

        # -------- RIGHT MAIN AREA --------
        right = tk.Frame(self.root, bg="#0f0f23")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Tab bar
        tab_bar = tk.Frame(right, bg="#16162a")
        tab_bar.pack(fill=tk.X)

        self.tab_btns = []
        self.tab_pages = []

        for i, label in enumerate(["📋 Logi", "✅ Aktywne MAC",
                                    "🌐 Proxy", "📺 Player"]):
            b = self._make_btn(tab_bar, label, "#333355", "#444466",
                               lambda idx=i: self._switch_tab(idx))
            b.pack(side=tk.LEFT, padx=(10 if i == 0 else 3, 3),
                   pady=5, ipady=3, ipadx=10)
            self.tab_btns.append(b)

        # Pages container
        pages = tk.Frame(right, bg="#0f0f23")
        pages.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self._build_page_logs(pages)
        self._build_page_active(pages)
        self._build_page_proxy(pages)
        self._build_page_player(pages)

        # Show first tab
        self._switch_tab(0)

    # ── Sidebar: Scanner ───────────────────────────────────
    def _build_sidebar_scanner(self, left):
        # Logo
        tk.Label(left, text="⚡ FLIPPER",
                 font=("Helvetica", 24, "bold"),
                 bg="#1a1a2e", fg="#00d4ff").pack(pady=(18, 2))
        tk.Label(left, text="MAC Address Scanner",
                 font=("Helvetica", 11),
                 bg="#1a1a2e", fg="#888888").pack(pady=(0, 10))

        self._sep(left)

        # URL
        self._label(left, "URL serwera")
        self.url_entry = self._entry(left)

        # MAC prefix
        self._label(left, "Pierwsze 3 bajty MAC")
        self.mac_entry = self._entry(left, default="00:1B:79")

        # Proxy (inline)
        self._label(left, "Proxy (opcjonalnie)")
        self.proxy_inline_entry = self._entry(left, default="")

        # Workers
        self._label(left, "Ilość procesów")
        self.workers_entry = self._entry(left, default="10")

        # Timeout
        self._label(left, "Timeout (s)")
        self.timeout_entry = self._entry(left, default="5")

        # Save checkbox
        self.save_var = tk.BooleanVar(value=True)
        cb = tk.Checkbutton(left, text="Zapisuj do pliku",
                            variable=self.save_var,
                            bg="#1a1a2e", fg="#aaaaaa",
                            selectcolor="#12122a",
                            activebackground="#1a1a2e",
                            activeforeground="#cccccc",
                            font=("Helvetica", 11))
        cb.pack(anchor=tk.W, padx=16, pady=(2, 4))

        # Export
        self._make_btn(left, "📁  Eksportuj wyniki", "#333355", "#444466",
                       self._export_results).pack(fill=tk.X, padx=16, pady=(2, 8))

        self._sep(left)

        # START
        self.start_btn = self._make_btn(left, "▶  START", "#00b359", "#009945",
                                        self._toggle_start)
        self.start_btn.pack(fill=tk.X, padx=16, pady=(4, 4), ipady=6)

        # Pause + Stop row
        ps_frame = tk.Frame(left, bg="#1a1a2e")
        ps_frame.pack(fill=tk.X, padx=16, pady=(0, 6))

        self.pause_btn = self._make_btn(ps_frame, "⏸ PAUZA", "#c78d00",
                                        "#a87600", self._toggle_pause)
        self.pause_btn.pack(side=tk.LEFT, expand=True, fill=tk.X,
                            padx=(0, 3), ipady=4)
        self._btn_disable(self.pause_btn)

        self.stop_btn = self._make_btn(ps_frame, "⏹ STOP", "#cc3333",
                                       "#aa2222", self._stop_scan)
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X,
                           padx=(3, 0), ipady=4)
        self._btn_disable(self.stop_btn)

        self._sep(left)

        # Stats
        self.stat_checked = tk.Label(left, text="Sprawdzono:  0",
                                     font=("Helvetica", 12), anchor=tk.W,
                                     bg="#1a1a2e", fg="#aaaaaa")
        self.stat_checked.pack(fill=tk.X, padx=18, pady=(4, 0))

        self.stat_found = tk.Label(left, text="Znaleziono:    0",
                                   font=("Helvetica", 12), anchor=tk.W,
                                   bg="#1a1a2e", fg="#00ff88")
        self.stat_found.pack(fill=tk.X, padx=18)

        self.stat_status = tk.Label(left, text="Status: Bezczynny",
                                    font=("Helvetica", 11), anchor=tk.W,
                                    bg="#1a1a2e", fg="#666666")
        self.stat_status.pack(fill=tk.X, padx=18, pady=(4, 0))

    # ── Sidebar: Player ───────────────────────────────────
    def _build_sidebar_player(self, left):
        tk.Label(left, text="📺 PLAYER",
                 font=("Helvetica", 24, "bold"),
                 bg="#1a1a2e", fg="#00d4ff").pack(pady=(18, 2))
        tk.Label(left, text="IPTV Channel Viewer",
                 font=("Helvetica", 11),
                 bg="#1a1a2e", fg="#888888").pack(pady=(0, 10))

        self._sep(left)

        self._label(left, "URL serwera")
        self.player_url_entry = self._entry(left)

        self._label(left, "MAC do użycia")
        # Dropdown (OptionMenu) populated from active_macs
        self.player_mac_var = tk.StringVar(value="(brak)")
        self.player_mac_menu = tk.OptionMenu(
            left, self.player_mac_var, "(brak)")
        self.player_mac_menu.configure(
            bg="#12122a", fg="#e0e0e0", font=("Helvetica", 11),
            activebackground="#2563eb", activeforeground="white",
            highlightthickness=0, relief="flat", bd=1)
        self.player_mac_menu["menu"].configure(
            bg="#12122a", fg="#e0e0e0", font=("Helvetica", 11),
            activebackground="#2563eb", activeforeground="white")
        self.player_mac_menu.pack(fill=tk.X, padx=16, pady=(2, 10))

        self._make_btn(left, "🔄 Odśwież MAC-i", "#333355", "#444466",
                       self._refresh_player_mac_list).pack(
            fill=tk.X, padx=16, pady=(2, 4))

        self._sep(left)

        self._make_btn(left, "📡 Pobierz kanały", "#2563eb", "#1d4ed8",
                       self._fetch_channels).pack(
            fill=tk.X, padx=16, pady=(4, 4), ipady=4)

        self._make_btn(left, "▶ Odtwórz kanał", "#00b359", "#009945",
                       self._play_selected_channel).pack(
            fill=tk.X, padx=16, pady=(2, 4), ipady=4)

        self._sep(left)

        self.player_status = tk.Label(left, text="Status: Bezczynny",
                                      font=("Helvetica", 11), anchor=tk.W,
                                      bg="#1a1a2e", fg="#666666")
        self.player_status.pack(fill=tk.X, padx=18, pady=(4, 0))

    # ── Page 0: Logs ──────────────────────────────────────
    def _build_page_logs(self, pages):
        page = tk.Frame(pages, bg="#0a0a1e")
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        self.log_text = tk.Text(page, font=("Menlo", 12),
                                bg="#0a0a1e", fg="#c8c8e0",
                                wrap=tk.WORD, state=tk.DISABLED,
                                relief="flat", bd=4,
                                insertbackground="#ffffff")
        log_sb = tk.Scrollbar(page, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        log_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.log_text.tag_config("success", foreground="#00ff88")
        self.log_text.tag_config("error", foreground="#ff4444")
        self.log_text.tag_config("info", foreground="#55aaff")
        self.log_text.tag_config("warning", foreground="#ffaa00")
        self.log_text.tag_config("dim", foreground="#555577")

    # ── Page 1: Active MACs ───────────────────────────────
    def _build_page_active(self, pages):
        page = tk.Frame(pages, bg="#0a0a1e")
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        tree_frame = tk.Frame(page, bg="#0a0a1e")
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(tree_frame,
                                 columns=("url", "mac", "expiry"),
                                 show="headings")
        self.tree.heading("url", text="URL")
        self.tree.heading("mac", text="Adres MAC")
        self.tree.heading("expiry", text="Data ważności")
        self.tree.column("url", width=320, minwidth=160)
        self.tree.column("mac", width=200, minwidth=140)
        self.tree.column("expiry", width=260, minwidth=140)

        tree_sb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,
                                command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_sb.set)
        tree_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)

        # Bottom bar
        bot = tk.Frame(page, bg="#0a0a1e")
        bot.pack(fill=tk.X, pady=(4, 0))

        self._make_btn(bot, "📋 Kopiuj zaznaczony", "#2563eb", "#1d4ed8",
                       self._copy_selected_mac).pack(
            side=tk.LEFT, padx=(4, 4), ipady=3, ipadx=6)
        self._make_btn(bot, "📋 Kopiuj wszystkie", "#333355", "#444466",
                       self._copy_all_macs).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(bot, "🧬 Klonuj MAC", "#6d28d9", "#5b21b6",
                       self._clone_selected_mac).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)

        self.mac_count_label = tk.Label(bot, text="Znaleziono: 0",
                                        font=("Helvetica", 11),
                                        bg="#0a0a1e", fg="#888888")
        self.mac_count_label.pack(side=tk.RIGHT, padx=8)

    # ── Page 2: Proxy ─────────────────────────────────────
    def _build_page_proxy(self, pages):
        page = tk.Frame(pages, bg="#0a0a1e")
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        # Top controls
        top = tk.Frame(page, bg="#0a0a1e")
        top.pack(fill=tk.X, pady=(4, 4))

        self._make_btn(top, "🔄 Pobierz z API", "#2563eb", "#1d4ed8",
                       self._fetch_proxies).pack(
            side=tk.LEFT, padx=(4, 4), ipady=3, ipadx=6)
        self._make_btn(top, "🗑 Wyczyść listę", "#cc3333", "#aa2222",
                       self._clear_proxies).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)

        # Add custom proxy inline
        tk.Label(top, text="Dodaj:", font=("Helvetica", 11),
                 bg="#0a0a1e", fg="#aaaaaa").pack(side=tk.LEFT, padx=(10, 4))
        self.proxy_add_entry = tk.Entry(
            top, font=("Helvetica", 11), width=30,
            bg="#12122a", fg="#e0e0e0", insertbackground="#ffffff",
            relief="flat", highlightthickness=1,
            highlightcolor="#2563eb", highlightbackground="#333355")
        self.proxy_add_entry.pack(side=tk.LEFT, padx=(0, 4), ipady=3)
        self._make_btn(top, "➕", "#00b359", "#009945",
                       self._add_custom_proxy).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=4)

        self.proxy_count_label = tk.Label(
            top, text="Proxy: 0", font=("Helvetica", 11),
            bg="#0a0a1e", fg="#888888")
        self.proxy_count_label.pack(side=tk.RIGHT, padx=8)

        # Proxy list (Treeview)
        tree_frame = tk.Frame(page, bg="#0a0a1e")
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self.proxy_tree = ttk.Treeview(
            tree_frame, columns=("proxy", "status"),
            show="headings")
        self.proxy_tree.heading("proxy", text="Adres proxy")
        self.proxy_tree.heading("status", text="Status")
        self.proxy_tree.column("proxy", width=400, minwidth=200)
        self.proxy_tree.column("status", width=120, minwidth=80)

        proxy_sb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,
                                 command=self.proxy_tree.yview)
        self.proxy_tree.configure(yscrollcommand=proxy_sb.set)
        proxy_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.proxy_tree.pack(fill=tk.BOTH, expand=True)

        # Bottom
        bot = tk.Frame(page, bg="#0a0a1e")
        bot.pack(fill=tk.X, pady=(4, 0))
        self._make_btn(bot, "🗑 Usuń zaznaczony", "#cc3333", "#aa2222",
                       self._remove_selected_proxy).pack(
            side=tk.LEFT, padx=(4, 4), ipady=3, ipadx=6)

    # ── Page 3: Player ────────────────────────────────────
    def _build_page_player(self, pages):
        page = tk.Frame(pages, bg="#0a0a1e")
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        # Channel list
        tree_frame = tk.Frame(page, bg="#0a0a1e")
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self.channel_tree = ttk.Treeview(
            tree_frame, columns=("num", "name", "genre"),
            show="headings")
        self.channel_tree.heading("num", text="#")
        self.channel_tree.heading("name", text="Kanał")
        self.channel_tree.heading("genre", text="Gatunek")
        self.channel_tree.column("num", width=50, minwidth=40)
        self.channel_tree.column("name", width=400, minwidth=200)
        self.channel_tree.column("genre", width=200, minwidth=100)

        ch_sb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,
                               command=self.channel_tree.yview)
        self.channel_tree.configure(yscrollcommand=ch_sb.set)
        ch_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.channel_tree.pack(fill=tk.BOTH, expand=True)

        # Double-click to play
        self.channel_tree.bind("<Double-1>", lambda e: self._play_selected_channel())

        # Bottom bar
        bot = tk.Frame(page, bg="#0a0a1e")
        bot.pack(fill=tk.X, pady=(4, 0))

        self._make_btn(bot, "▶ Odtwórz", "#00b359", "#009945",
                       self._play_selected_channel).pack(
            side=tk.LEFT, padx=(4, 4), ipady=3, ipadx=6)
        self._make_btn(bot, "📋 Kopiuj URL", "#2563eb", "#1d4ed8",
                       self._copy_channel_url).pack(
            side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)

        self.channel_count_label = tk.Label(
            bot, text="Kanały: 0", font=("Helvetica", 11),
            bg="#0a0a1e", fg="#888888")
        self.channel_count_label.pack(side=tk.RIGHT, padx=8)

    # ── Widget helpers ─────────────────────────────────────
    def _entry(self, parent, default=""):
        e = tk.Entry(parent, font=("Helvetica", 12),
                     bg="#12122a", fg="#e0e0e0",
                     insertbackground="#ffffff",
                     relief="flat", highlightthickness=1,
                     highlightcolor="#2563eb",
                     highlightbackground="#333355")
        e.pack(fill=tk.X, padx=16, pady=(2, 8), ipady=5)
        if default:
            e.insert(0, default)
        return e

    def _label(self, parent, text):
        tk.Label(parent, text=text, font=("Helvetica", 12, "bold"),
                 bg="#1a1a2e", fg="#c8c8e0",
                 anchor=tk.W).pack(fill=tk.X, padx=18, pady=(2, 0))

    def _sep(self, parent):
        tk.Frame(parent, height=1, bg="#333355").pack(fill=tk.X, padx=14, pady=8)

    def _make_btn(self, parent, text, bg_color, hover_color, command):
        """Label-based button (works reliably on macOS)."""
        lbl = tk.Label(parent, text=text, font=("Helvetica", 12, "bold"),
                       bg=bg_color, fg="white", cursor="hand2",
                       anchor=tk.CENTER, padx=6, pady=2)
        lbl._normal_bg = bg_color
        lbl._hover_bg = hover_color
        lbl._command = command
        lbl._enabled = True

        def on_click(event):
            if lbl._enabled:
                lbl._command()

        def on_enter(event):
            if lbl._enabled:
                lbl.configure(bg=lbl._hover_bg)

        def on_leave(event):
            if lbl._enabled:
                lbl.configure(bg=lbl._normal_bg)

        lbl.bind("<Button-1>", on_click)
        lbl.bind("<Enter>", on_enter)
        lbl.bind("<Leave>", on_leave)
        return lbl

    def _btn_enable(self, btn):
        btn._enabled = True
        btn.configure(bg=btn._normal_bg, fg="white", cursor="hand2")

    def _btn_disable(self, btn):
        btn._enabled = False
        btn.configure(bg="#444444", fg="#888888", cursor="arrow")

    def _switch_tab(self, idx):
        self.current_tab = idx
        for i, (btn, page) in enumerate(zip(self.tab_btns, self.tab_pages)):
            if i == idx:
                btn._normal_bg = "#2563eb"
                btn.configure(bg="#2563eb")
                page.lift()
            else:
                btn._normal_bg = "#333355"
                btn.configure(bg="#333355")
        # Show appropriate sidebar
        if idx == 3:  # Player tab
            self.sidebar_player.lift()
        else:
            self.sidebar_scanner.lift()

    # ── Logging ────────────────────────────────────────────
    def _log(self, message, tag="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{ts}] ", "dim")
        self.log_text.insert(tk.END, f"{message}\n", tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _log_safe(self, message, tag="info"):
        self.root.after(0, self._log, message, tag)

    # ── Stats ──────────────────────────────────────────────
    def _update_stats(self):
        self.stat_checked.configure(text=f"Sprawdzono:  {self.checked_count}")
        self.stat_found.configure(text=f"Znaleziono:    {self.found_count}")

    def _update_stats_safe(self):
        self.root.after(0, self._update_stats)

    def _set_status(self, text, color="#666666"):
        self.root.after(0, lambda: self.stat_status.configure(
            text=f"Status: {text}", fg=color))

    # ── Active MAC management ──────────────────────────────
    def _add_active_mac(self, url, mac, expiry):
        entry = {"url": url, "mac": mac, "expiry": expiry}
        self.active_macs.append(entry)
        self.root.after(0, self._insert_mac_row, entry)

    def _insert_mac_row(self, entry):
        self.tree.insert("", tk.END,
                         values=(entry["url"], entry["mac"], entry["expiry"]))
        self.mac_count_label.configure(
            text=f"Znaleziono: {len(self.active_macs)}")

    # ── Copy ───────────────────────────────────────────────
    def _copy_selected_mac(self):
        sel = self.tree.selection()
        if not sel:
            self._log("Nie zaznaczono żadnego wiersza.", "warning")
            return
        values = self.tree.item(sel[0], "values")
        mac = values[1]
        self.root.clipboard_clear()
        self.root.clipboard_append(mac)
        self._log(f"Skopiowano MAC: {mac}", "info")

    def _copy_all_macs(self):
        if not self.active_macs:
            self._log("Brak aktywnych adresów MAC.", "warning")
            return
        text = "\n".join(
            f"{m['mac']}  |  {m['expiry']}  |  {m['url']}"
            for m in self.active_macs)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._log(f"Skopiowano {len(self.active_macs)} adresów MAC.", "info")

    # ── Clone MAC ──────────────────────────────────────────
    def _clone_selected_mac(self):
        sel = self.tree.selection()
        if not sel:
            self._log("Zaznacz MAC do sklonowania.", "warning")
            return
        values = self.tree.item(sel[0], "values")
        mac = values[1]
        self.root.clipboard_clear()
        self.root.clipboard_append(mac)
        self._log(f"🧬 Sklonowano MAC: {mac}", "success")
        self._log(f"   MAC skopiowany do schowka — użyj w ustawieniach urządzenia.", "info")

    # ── Export ─────────────────────────────────────────────
    def _export_results(self):
        if not self.active_macs:
            self._log("Brak wyników do eksportu.", "warning")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Plik tekstowy", "*.txt"),
                       ("CSV", "*.csv"),
                       ("Wszystkie", "*.*")],
            initialfile="flipper_results.txt")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Flipper — wyniki skanowania\n")
            f.write(f"# {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            for m in self.active_macs:
                f.write(f"{m['mac']} | {m['expiry']} | {m['url']}\n")
        self._log(f"Wyeksportowano {len(self.active_macs)} wyników.", "success")

    # ── Auto Save ──────────────────────────────────────────
    def _auto_save(self):
        if not self.save_var.get() or not self.active_macs:
            return
        try:
            with open(RESULTS_FILE, "w", encoding="utf-8") as f:
                f.write("# Flipper — auto-zapis\n")
                for m in self.active_macs:
                    f.write(f"{m['mac']} | {m['expiry']} | {m['url']}\n")
        except Exception:
            pass

    # ── Session ────────────────────────────────────────────
    def _save_session(self):
        data = {
            "url": self.url_entry.get(),
            "mac_prefix": self.mac_entry.get(),
            "workers": self.workers_entry.get(),
            "timeout": self.timeout_entry.get(),
            "save_results": self.save_var.get(),
            "proxy_inline": self.proxy_inline_entry.get(),
            "player_url": self.player_url_entry.get(),
        }
        try:
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _load_session(self):
        if not os.path.exists(SESSION_FILE):
            return
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("url"):
                self.url_entry.delete(0, tk.END)
                self.url_entry.insert(0, data["url"])
            if data.get("mac_prefix"):
                self.mac_entry.delete(0, tk.END)
                self.mac_entry.insert(0, data["mac_prefix"])
            if data.get("workers"):
                self.workers_entry.delete(0, tk.END)
                self.workers_entry.insert(0, data["workers"])
            if data.get("timeout"):
                self.timeout_entry.delete(0, tk.END)
                self.timeout_entry.insert(0, data["timeout"])
            if "save_results" in data:
                self.save_var.set(data["save_results"])
            if data.get("proxy_inline"):
                self.proxy_inline_entry.delete(0, tk.END)
                self.proxy_inline_entry.insert(0, data["proxy_inline"])
            if data.get("player_url"):
                self.player_url_entry.delete(0, tk.END)
                self.player_url_entry.insert(0, data["player_url"])
        except Exception:
            pass

    # ══════════════════════════════════════════════════════
    #  PROXY TAB LOGIC
    # ══════════════════════════════════════════════════════

    def _get_active_proxy(self):
        """Return the proxy to use: inline field first, then rotating list."""
        inline = self.proxy_inline_entry.get().strip()
        if inline:
            if not inline.startswith("http"):
                inline = "http://" + inline
            return inline
        return get_current_proxy()

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
        proxies = get_proxy_list()
        for p in proxies:
            self.proxy_tree.insert("", tk.END, values=(p, "OK"))
        self.proxy_count_label.configure(text=f"Proxy: {len(proxies)}")

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

    def _handle_proxy_fail(self, proxy):
        """Handle a proxy failure — rotate and possibly remove."""
        if proxy:
            removed = report_proxy_fail(proxy)
            if removed:
                self._log_safe(f"Proxy usunięty (zbyt wiele błędów): {proxy}", "warning")
                self.root.after(0, self._refresh_proxy_tree)
            new_proxy = rotate_proxy()
            if new_proxy:
                self._log_safe(f"Zmiana proxy → {new_proxy}", "info")

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
            self._log_safe(f"Używam proxy: {proxy}", "info")

        endpoint = get_responding_endpoint(server_address, timeout=timeout,
                                           proxy=proxy)
        if self.stop_event.is_set():
            self._scan_finished()
            return
        if not endpoint:
            # If using proxy, try rotating
            if proxy:
                self._handle_proxy_fail(proxy)
                proxy = self._get_active_proxy()
                if proxy:
                    endpoint = get_responding_endpoint(
                        server_address, timeout=timeout, proxy=proxy)
            if not endpoint:
                self._log_safe("Serwer nie odpowiada!", "error")
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
                    futures.append(
                        self.executor.submit(
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

        self._log_safe(f"Sprawdzam: {mac}", "dim")

        try:
            result = check_mac(url, mac, timeout=timeout, proxy=proxy)
        except Exception:
            if proxy:
                self._handle_proxy_fail(proxy)
            result = None

        self.checked_count += 1
        self._update_stats_safe()

        if result:
            if proxy:
                report_proxy_success(proxy)
            _, expiry_str = result
            self.found_count += 1
            self._update_stats_safe()
            self._log_safe(
                f"✅ ZNALEZIONO: {mac}  →  {expiry_str}", "success")
            self._add_active_mac(url, mac, expiry_str)
            self._auto_save()
        else:
            if self.checked_count % 25 == 0:
                self._log_safe(
                    f"Sprawdzono {self.checked_count}, "
                    f"znaleziono {self.found_count}...", "info")

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
    #  PLAYER TAB LOGIC
    # ══════════════════════════════════════════════════════

    def _refresh_player_mac_list(self):
        """Refresh the MAC dropdown from active_macs list."""
        menu = self.player_mac_menu["menu"]
        menu.delete(0, "end")
        if not self.active_macs:
            menu.add_command(label="(brak)",
                             command=lambda: self.player_mac_var.set("(brak)"))
            self.player_mac_var.set("(brak)")
            return
        for m in self.active_macs:
            label = f"{m['mac']}  ({m['expiry'][:20]})"
            menu.add_command(
                label=label,
                command=lambda v=m['mac']: self.player_mac_var.set(v))
        # Auto-select first
        self.player_mac_var.set(self.active_macs[0]["mac"])

    def _fetch_channels(self):
        url_raw = self.player_url_entry.get().strip()
        mac = self.player_mac_var.get().strip()

        if not url_raw:
            # Try scanner URL
            url_raw = self.url_entry.get().strip()
        if not url_raw:
            self._log("Podaj URL serwera (w panelu Player lub Scanner).", "error")
            return
        if not mac or mac == "(brak)":
            self._log("Wybierz adres MAC w panelu gracza.", "error")
            return

        self.player_status.configure(text="Status: Pobieranie kanałów...",
                                     fg="#ffaa00")
        self._log(f"Pobieranie kanałów dla {mac}...", "info")

        threading.Thread(target=self._fetch_channels_worker,
                         args=(url_raw, mac), daemon=True).start()

    def _fetch_channels_worker(self, url_raw, mac):
        server = parse_url(url_raw)
        proxy = self._get_active_proxy()

        endpoint = get_responding_endpoint(server, timeout=5, proxy=proxy)
        if not endpoint:
            self._log_safe("Serwer nie odpowiada.", "error")
            self.root.after(0, lambda: self.player_status.configure(
                text="Status: Błąd", fg="#ff4444"))
            return

        url = server + endpoint
        token = get_handshake(url, mac, timeout=5, proxy=proxy)
        if not token:
            self._log_safe("Nie udało się uzyskać tokena.", "error")
            self.root.after(0, lambda: self.player_status.configure(
                text="Status: Błąd tokena", fg="#ff4444"))
            return

        self.player_token = token

        # Fetch all channels (paginated)
        all_channels = []
        page = 1
        while True:
            channels = get_channels(url, mac, token, genre_id="*",
                                    page=page, timeout=5, proxy=proxy)
            if not channels:
                break
            all_channels.extend(channels)
            if len(channels) < 10:
                break
            page += 1
            if page > 50:  # safety limit
                break

        self.player_channels = all_channels
        self._log_safe(f"Pobrano {len(all_channels)} kanałów.", "success")

        self.root.after(0, self._populate_channel_tree)
        self.root.after(0, lambda: self.player_status.configure(
            text=f"Status: {len(all_channels)} kanałów", fg="#00ff88"))

    def _populate_channel_tree(self):
        for item in self.channel_tree.get_children():
            self.channel_tree.delete(item)
        for ch in self.player_channels:
            num = ch.get("number", "")
            name = ch.get("name", "")
            genre = ch.get("tv_genre_id", "")
            self.channel_tree.insert("", tk.END, values=(num, name, genre))
        self.channel_count_label.configure(
            text=f"Kanały: {len(self.player_channels)}")

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
        name = ch.get("name", "?")

        if not cmd:
            self._log(f"Brak komendy strumienia dla: {name}", "error")
            return

        self._log(f"Odtwarzanie: {name}...", "info")
        self.player_status.configure(text=f"Odtwarzanie: {name}",
                                     fg="#00ff88")

        threading.Thread(target=self._play_stream_worker,
                         args=(cmd, name), daemon=True).start()

    def _play_stream_worker(self, cmd, name):
        url_raw = self.player_url_entry.get().strip()
        if not url_raw:
            url_raw = self.url_entry.get().strip()
        mac = self.player_mac_var.get().strip()
        proxy = self._get_active_proxy()

        server = parse_url(url_raw)
        endpoint = get_responding_endpoint(server, timeout=5, proxy=proxy)
        if not endpoint:
            self._log_safe("Serwer nie odpowiada.", "error")
            return

        url = server + endpoint

        if not self.player_token:
            self.player_token = get_handshake(url, mac, timeout=5,
                                              proxy=proxy)
        if not self.player_token:
            self._log_safe("Nie udało się uzyskać tokena.", "error")
            return

        stream_url = get_stream_url(url, mac, self.player_token, cmd,
                                    timeout=5, proxy=proxy)
        if not stream_url:
            self._log_safe(f"Nie udało się pobrać URL strumienia: {name}",
                           "error")
            return

        self._log_safe(f"Stream URL: {stream_url}", "success")

        # Try to open in default player
        try:
            if platform.system() == "Darwin":
                # Try VLC first, then IINA, then default
                vlc_path = "/Applications/VLC.app/Contents/MacOS/VLC"
                if os.path.exists(vlc_path):
                    subprocess.Popen([vlc_path, stream_url])
                else:
                    subprocess.Popen(["open", stream_url])
            elif platform.system() == "Windows":
                os.startfile(stream_url)
            else:
                subprocess.Popen(["xdg-open", stream_url])
            self._log_safe(f"Otworzono strumień w zewnętrznym odtwarzaczu.",
                           "success")
        except Exception as e:
            self._log_safe(f"Nie udało się otworzyć odtwarzacza: {e}", "error")
            self._log_safe(f"Skopiuj URL ręcznie: {stream_url}", "info")

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
            self._log(f"Brak komendy strumienia dla: {name}", "error")
            return

        self._log(f"Pobieranie URL dla: {name}...", "info")
        threading.Thread(target=self._copy_channel_url_worker,
                         args=(cmd, name), daemon=True).start()

    def _copy_channel_url_worker(self, cmd, name):
        url_raw = self.player_url_entry.get().strip()
        if not url_raw:
            url_raw = self.url_entry.get().strip()
        mac = self.player_mac_var.get().strip()
        proxy = self._get_active_proxy()

        server = parse_url(url_raw)
        endpoint = get_responding_endpoint(server, timeout=5, proxy=proxy)
        if not endpoint:
            self._log_safe("Serwer nie odpowiada.", "error")
            return

        url = server + endpoint
        if not self.player_token:
            self.player_token = get_handshake(url, mac, timeout=5,
                                              proxy=proxy)
        if not self.player_token:
            self._log_safe("Nie udało się uzyskać tokena.", "error")
            return

        stream_url = get_stream_url(url, mac, self.player_token, cmd,
                                    timeout=5, proxy=proxy)
        if not stream_url:
            self._log_safe(f"Nie udało się pobrać URL: {name}", "error")
            return

        self.root.after(0, lambda: self._do_copy_stream(stream_url, name))

    def _do_copy_stream(self, stream_url, name):
        self.root.clipboard_clear()
        self.root.clipboard_append(stream_url)
        self._log(f"Skopiowano URL: {name} → {stream_url}", "success")

    # ── Close ──────────────────────────────────────────────
    def _on_close(self):
        self.stop_event.set()
        self.pause_event.set()
        self._save_session()
        self._auto_save()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = App()
    app.run()
