# Architecture

## High-Level Overview

Flipper follows a monolithic single-class architecture. The `App` class in `main.py` (~4800 lines) owns the entire GUI, application state, and orchestration logic. Network protocol concerns are separated into `scanner.py`, and static configuration lives in `constants.py`.

```
┌──────────────────────────────────────────────────┐
│                    main.py                       │
│                                                  │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐ │
│  │  GUI Layer  │  │ App State  │  │  Threads   │ │
│  │  (Tkinter)  │  │ (in-memory)│  │ (executor) │ │
│  └──────┬─────┘  └──────┬─────┘  └──────┬─────┘ │
│         │               │               │        │
│         └───────────┬───┘───────────────┘        │
│                     │                             │
│              ┌──────▼──────┐                      │
│              │  App class  │                      │
│              └──────┬──────┘                      │
│                     │                             │
├─────────────────────┼─────────────────────────────┤
│                     │                             │
│              ┌──────▼──────┐                      │
│              │ scanner.py  │                      │
│              │ (HTTP/API)  │                      │
│              └──────┬──────┘                      │
│                     │                             │
│              ┌──────▼──────┐                      │
│              │constants.py │                      │
│              │ (static)    │                      │
│              └─────────────┘                      │
└──────────────────────────────────────────────────┘
```

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `main.py` | GUI construction, scan orchestration, player control, session persistence, proxy management UI, profile management, auto-update logic |
| `scanner.py` | Stalker portal API protocol, proxy pool management (thread-safe), MAC generation, handshake, channel fetching, stream URL resolution |
| `constants.py` | User agent strings, endpoint paths, Polish month translations, file name constants |

## Threading Model

The application runs a single Tkinter `mainloop()` on the main thread. All I/O-bound operations execute in background daemon threads to keep the UI responsive.

### Thread Types

1. **Scan Thread** - Spawns a `ThreadPoolExecutor` with configurable worker count. Each worker checks a MAC address through a proxy, performing handshake + account info lookup. Coordinated via `threading.Event` for pause/stop.

2. **Proxy Fetch Thread** - Downloads proxy lists from 35+ public APIs sequentially.

3. **Proxy Test Thread** - Tests proxy latency in batches of 5 (`PROXY_TEST_BATCH_SIZE`), with pause/stop support.

4. **Channel Load Thread** - Fetches genres and channel lists from the portal.

5. **Stream Resolution Thread** - Resolves the actual stream URL before passing it to mpv.

6. **Account Info Thread** - Fetches account details for display in the Info tab.

### Thread Safety

- **Proxy state** (`scanner.py`): Protected by `threading.Lock` (`_proxy_lock`). All reads/writes to the shared proxy list, index, and failure counts go through lock-guarded functions.
- **UI updates from threads**: All GUI mutations from background threads use `self.root.after(0, callback)` to schedule execution on the main thread.
- **Pause/Stop signals**: `threading.Event` objects (`stop_event`, `pause_event`) allow cooperative cancellation and pausing of long-running operations.

```
Main Thread (Tkinter mainloop)
│
├── Scan Thread
│   └── ThreadPoolExecutor (N workers)
│       ├── Worker 1 → scanner.check_mac()
│       ├── Worker 2 → scanner.check_mac()
│       └── ...
│
├── Proxy Fetch Thread → scanner.fetch_free_proxies()
├── Proxy Test Thread  → scanner.test_and_filter_proxies()
├── Channel Load Thread → scanner.get_genres() / get_channels()
├── Stream Resolution   → scanner.get_stream_url()
└── Account Info Thread → scanner.get_handshake() + get_main_info()
```

## State Management

All application state is held as instance variables on the `App` class. There is no external state management library or pattern (no Redux, no MVC separation).

### Key State Variables

| Variable | Type | Purpose |
|---|---|---|
| `active_macs` | `list[dict]` | Found MACs with url, mac, expiry, proxy |
| `mac_proxy_map` | `dict` | Maps MAC addresses to their working proxy |
| `profiles` | `list[dict]` | Saved profiles with name, mac, url, proxy |
| `active_profile` | `dict` or `None` | Currently selected profile |
| `log_history` | `list[tuple]` | Log messages with color tags |
| `player_channels` | `list` | Channels loaded for current genre |
| `player_genres` | `list` | Genre/category list from portal |
| `player_token` | `str` or `None` | Auth token from handshake |
| `is_running` | `bool` | Whether a scan is in progress |
| `is_paused` | `bool` | Whether the scan is paused |
| `checked_count` | `int` | Total MACs checked in session |
| `found_count` | `int` | Total valid MACs found in session |

### State Lifecycle

1. **Startup**: `App.__init__` initializes defaults, then `_load_session()` restores persisted state from `session.json`.
2. **Runtime**: State is mutated directly by UI callbacks and background thread results (via `root.after()`).
3. **Shutdown**: `_on_closing()` saves full state to `session.json`, stops threads, and cleans up mpv.

## GUI Layout

The application uses a fixed sidebar + tabbed content area layout.

```
┌──────────────────────────────────────────────────────┐
│                   Title Bar                          │
├──────────┬───────────────────────────────────────────┤
│          │  Tab Bar: Logi | MAC | Proxy | Player |  │
│          │          Profile | Info | Ustawienia      │
│ Sidebar  ├───────────────────────────────────────────┤
│          │                                           │
│ Scanner  │            Tab Content Area               │
│ inputs   │                                           │
│   or     │  (varies by selected tab)                 │
│ Player   │                                           │
│ controls │                                           │
│          │                                           │
├──────────┴───────────────────────────────────────────┤
│                  Status Bar                          │
└──────────────────────────────────────────────────────┘
```

### Tabs

| Tab | Content |
|---|---|
| Logi (Logs) | Timestamped, color-coded log output |
| Active MACs | List of found MACs with actions (copy, clone, remove, export) |
| Proxy | Proxy list management, testing, import |
| Player | Embedded mpv player with channel list, genre filter, search |
| Profile | Named profile CRUD, activation |
| Info | Account details for selected MAC (expiry, IP, status) |
| Ustawienia (Settings) | Verbose logs, debug console, proxy settings, auto-update, save folder |

### Sidebar

The sidebar content changes based on context:
- **Scanner mode**: Portal URL, MAC prefix, worker count, timeout, scan controls (start/stop/pause)
- **Player mode**: MAC selection dropdown, profile selection, player transport controls

## Design Decisions

1. **Monolithic `App` class**: All logic in one class trades modularity for simplicity. The codebase is a single-developer project where rapid iteration was prioritized over architectural purity.

2. **Plain Tkinter over CustomTkinter**: The project initially used CustomTkinter but migrated away due to rendering issues on macOS (empty window bug). Plain Tkinter with manual styling provides cross-platform reliability.

3. **Proxy-only scanning**: The scanner never makes direct connections to portals. All requests go through proxies to protect the user's IP address. This is a core design constraint, not optional.

4. **No database**: JSON file persistence was chosen over SQLite or similar to keep the dependency footprint minimal and the data format human-readable/debuggable.

5. **DLL management complexity**: ~400 lines of `main.py` handle libmpv DLL discovery and loading on Windows. This complexity exists because mpv's DLL loading is fragile across different Windows configurations, Python versions, and architecture mismatches.
