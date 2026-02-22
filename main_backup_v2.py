"""
Flipper — MAC Address Scanner with GUI (Plain Tkinter — macOS compatible)
"""

import os
import sys

if sys.platform == "darwin":
    os.environ["TK_SILENCE_DEPRECATION"] = "1"

import tkinter as tk
from tkinter import ttk, filedialog
import threading
import time
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, Future
from scanner import generate_random_mac, check_mac, get_responding_endpoint, parse_url
from constants import RESULTS_FILE, SESSION_FILE


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Flipper — MAC Scanner")
        self.root.geometry("1100x700")
        self.root.minsize(900, 550)

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
        self.active_macs = []

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
        # -------- LEFT SIDEBAR (pack side=left, fixed width) --------
        left = tk.Frame(self.root, width=270, bg="#1a1a2e",
                        highlightthickness=0, bd=0)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)

        # Logo
        tk.Label(left, text="⚡ FLIPPER",
                 font=("Helvetica", 24, "bold"),
                 bg="#1a1a2e", fg="#00d4ff").pack(pady=(20, 2))
        tk.Label(left, text="MAC Address Scanner",
                 font=("Helvetica", 11),
                 bg="#1a1a2e", fg="#888888").pack(pady=(0, 14))

        self._sep(left)

        # URL
        self._label(left, "URL serwera")
        self.url_entry = tk.Entry(left, font=("Helvetica", 12),
                                  bg="#12122a", fg="#e0e0e0",
                                  insertbackground="#ffffff",
                                  relief="flat", highlightthickness=1,
                                  highlightcolor="#2563eb",
                                  highlightbackground="#333355")
        self.url_entry.pack(fill=tk.X, padx=16, pady=(2, 10), ipady=5)

        # MAC prefix
        self._label(left, "Pierwsze 3 bajty MAC")
        self.mac_entry = tk.Entry(left, font=("Helvetica", 12),
                                  bg="#12122a", fg="#e0e0e0",
                                  insertbackground="#ffffff",
                                  relief="flat", highlightthickness=1,
                                  highlightcolor="#2563eb",
                                  highlightbackground="#333355")
        self.mac_entry.pack(fill=tk.X, padx=16, pady=(2, 10), ipady=5)
        self.mac_entry.insert(0, "00:1B:79")

        # Workers
        self._label(left, "Ilość procesów")
        self.workers_entry = tk.Entry(left, font=("Helvetica", 12),
                                      bg="#12122a", fg="#e0e0e0",
                                      insertbackground="#ffffff",
                                      relief="flat", highlightthickness=1,
                                      highlightcolor="#2563eb",
                                      highlightbackground="#333355")
        self.workers_entry.pack(fill=tk.X, padx=16, pady=(2, 10), ipady=5)
        self.workers_entry.insert(0, "10")

        # Timeout
        self._label(left, "Timeout (s)")
        self.timeout_entry = tk.Entry(left, font=("Helvetica", 12),
                                      bg="#12122a", fg="#e0e0e0",
                                      insertbackground="#ffffff",
                                      relief="flat", highlightthickness=1,
                                      highlightcolor="#2563eb",
                                      highlightbackground="#333355")
        self.timeout_entry.pack(fill=tk.X, padx=16, pady=(2, 10), ipady=5)
        self.timeout_entry.insert(0, "5")

        # Save checkbox
        self.save_var = tk.BooleanVar(value=True)
        cb = tk.Checkbutton(left, text="Zapisuj do pliku",
                            variable=self.save_var,
                            bg="#1a1a2e", fg="#aaaaaa",
                            selectcolor="#12122a",
                            activebackground="#1a1a2e",
                            activeforeground="#cccccc",
                            font=("Helvetica", 11))
        cb.pack(anchor=tk.W, padx=16, pady=(2, 6))

        # Export button
        self._make_btn(left, "📁  Eksportuj wyniki", "#333355", "#444466",
                       self._export_results).pack(fill=tk.X, padx=16, pady=(2, 10))

        self._sep(left)

        # START
        self.start_btn = self._make_btn(left, "▶  START", "#00b359", "#009945",
                                        self._toggle_start)
        self.start_btn.pack(fill=tk.X, padx=16, pady=(4, 4), ipady=6)

        # Pause + Stop row
        ps_frame = tk.Frame(left, bg="#1a1a2e")
        ps_frame.pack(fill=tk.X, padx=16, pady=(0, 6))

        self.pause_btn = self._make_btn(ps_frame, "⏸ PAUZA", "#c78d00", "#a87600",
                                        self._toggle_pause)
        self.pause_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 3), ipady=4)
        self.pause_btn._enabled = False
        self.pause_btn.config(bg="#444444", fg="#888888")

        self.stop_btn = self._make_btn(ps_frame, "⏹ STOP", "#cc3333", "#aa2222",
                                       self._stop_scan)
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(3, 0), ipady=4)
        self.stop_btn._enabled = False
        self.stop_btn.config(bg="#444444", fg="#888888")

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

        # -------- RIGHT MAIN AREA --------
        right = tk.Frame(self.root, bg="#0f0f23")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Tab bar
        tab_bar = tk.Frame(right, bg="#16162a")
        tab_bar.pack(fill=tk.X)

        self.tab_btns = []
        self.tab_pages = []

        b1 = self._make_btn(tab_bar, "📋  Logi", "#2563eb", "#1d4ed8",
                            lambda: self._switch_tab(0))
        b1.pack(side=tk.LEFT, padx=(10, 3), pady=5, ipady=3, ipadx=10)
        self.tab_btns.append(b1)

        b2 = self._make_btn(tab_bar, "✅  Aktywne MAC", "#333355", "#444466",
                            lambda: self._switch_tab(1))
        b2.pack(side=tk.LEFT, padx=(3, 3), pady=5, ipady=3, ipadx=10)
        self.tab_btns.append(b2)

        # Pages container
        pages = tk.Frame(right, bg="#0f0f23")
        pages.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # -- Page 0: Logs --
        page0 = tk.Frame(pages, bg="#0a0a1e")
        page0.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page0)

        self.log_text = tk.Text(page0, font=("Menlo", 12),
                                bg="#0a0a1e", fg="#c8c8e0",
                                wrap=tk.WORD, state=tk.DISABLED,
                                relief="flat", bd=4,
                                insertbackground="#ffffff")
        log_sb = tk.Scrollbar(page0, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        log_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.log_text.tag_config("success", foreground="#00ff88")
        self.log_text.tag_config("error", foreground="#ff4444")
        self.log_text.tag_config("info", foreground="#55aaff")
        self.log_text.tag_config("warning", foreground="#ffaa00")
        self.log_text.tag_config("dim", foreground="#555577")

        # -- Page 1: Active MACs --
        page1 = tk.Frame(pages, bg="#0a0a1e")
        page1.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page1)

        tree_frame = tk.Frame(page1, bg="#0a0a1e")
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

        # Bottom copy bar
        bot = tk.Frame(page1, bg="#0a0a1e")
        bot.pack(fill=tk.X, pady=(4, 0))

        self._make_btn(bot, "📋 Kopiuj zaznaczony", "#2563eb", "#1d4ed8",
                       self._copy_selected_mac).pack(side=tk.LEFT, padx=(4, 4), ipady=3, ipadx=6)
        self._make_btn(bot, "📋 Kopiuj wszystkie", "#333355", "#444466",
                       self._copy_all_macs).pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self.mac_count_label = tk.Label(bot, text="Znaleziono: 0",
                                        font=("Helvetica", 11),
                                        bg="#0a0a1e", fg="#888888")
        self.mac_count_label.pack(side=tk.RIGHT, padx=8)

        # Show first tab
        self._switch_tab(0)

    # ── Widget helpers ─────────────────────────────────────
    def _label(self, parent, text):
        tk.Label(parent, text=text, font=("Helvetica", 12, "bold"),
                 bg="#1a1a2e", fg="#c8c8e0",
                 anchor=tk.W).pack(fill=tk.X, padx=18, pady=(2, 0))

    def _sep(self, parent):
        tk.Frame(parent, height=1, bg="#333355").pack(fill=tk.X, padx=14, pady=8)

    def _make_btn(self, parent, text, bg_color, hover_color, command):
        """Create a Label-based button (works reliably on macOS)."""
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
        for i, (btn, page) in enumerate(zip(self.tab_btns, self.tab_pages)):
            if i == idx:
                btn._normal_bg = "#2563eb"
                btn.configure(bg="#2563eb")
                page.lift()
            else:
                btn._normal_bg = "#333355"
                btn.configure(bg="#333355")

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
        self.mac_count_label.configure(text=f"Znaleziono: {len(self.active_macs)}")

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
        except Exception:
            pass

    # ── Scanning ───────────────────────────────────────────
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

        endpoint = get_responding_endpoint(server_address, timeout=timeout)
        if self.stop_event.is_set():
            self._scan_finished()
            return
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
        self._log_safe(f"Sprawdzam: {mac}", "dim")
        result = check_mac(url, mac, timeout=timeout)

        self.checked_count += 1
        self._update_stats_safe()

        if result:
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
