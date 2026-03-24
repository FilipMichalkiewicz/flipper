# Module Reference

Detailed documentation for every source file in the project.

---

## main.py (~4800 lines)

The monolithic application file containing the `App` class, all GUI code, and orchestration logic.

### Top-Level Functions (before `App` class)

| Function | Lines | Purpose |
|---|---|---|
| `_read_debug_console_flag()` | 33-55 | Reads debug console setting from env var `FLIPPER_DEBUG` or `session.json` before GUI init |
| `_dpapi_protect_bytes(raw)` | 58-91 | Encrypts bytes using Windows DPAPI with entropy `"FlipperPATv1"` |
| `_dpapi_unprotect_bytes(raw)` | 94-131 | Decrypts DPAPI-protected bytes |
| `_xor_encrypt(data, key)` | ~135 | XOR-based encryption fallback for non-Windows platforms |
| `_xor_decrypt(data, key)` | ~140 | XOR-based decryption fallback |
| `_get_flipper_mpv_dir()` | ~210 | Returns the mpv DLL storage directory (`%APPDATA%\Flipper\mpv`) |
| `_pe_machine(path)` | ~250 | Reads PE header to determine DLL architecture (x86/x64) |
| `_process_expected_machine()` | ~270 | Returns the expected PE machine type for the running Python |
| `_set_dll_directory(path)` | ~290 | Calls `SetDllDirectoryW` to add a DLL search path |
| `_add_windows_dll_directory(path)` | ~310 | Calls `os.add_dll_directory()` (Python 3.8+) |
| `_prepend_to_path(path)` | ~330 | Adds directory to beginning of `PATH` env var |
| `_ensure_mpv_dll()` | ~350 | Downloads, extracts, and validates the mpv DLL on Windows |
| `_monkey_patch_find_library()` | ~700 | Patches `ctypes.util.find_library` to find mpv in custom locations |
| `_diagnose_mpv_availability()` | 793-925 | Returns diagnostic string about mpv DLL loading status |

### App Class - Section Map

| Lines (approx.) | Section | Key Methods |
|---|---|---|
| 943-1066 | **Initialization** | `__init__`, state variable setup, GUI construction, session load |
| 1067-1340 | **Sidebar** | `_build_scanner_sidebar()`, `_build_player_sidebar()` |
| 1354-2008 | **Tab Pages** | `_build_logs_tab()`, `_build_macs_tab()`, `_build_proxy_tab()`, `_build_player_tab()`, `_build_profiles_tab()`, `_build_info_tab()`, `_build_settings_tab()` |
| 2030-2321 | **Cache & Auto-Update** | `_load_channel_cache()`, `_save_channel_cache()`, `_check_for_updates()`, `_download_and_update()` |
| 2322-2398 | **Widget Helpers** | `_make_entry()`, `_make_label()`, `_make_button()`, `_switch_tab()` |
| 2418-2472 | **Logging** | `_log(msg, tag)`, `_debug_print()` |
| 2478-2870 | **MAC Management** | `_add_mac()`, `_remove_mac()`, `_copy_mac()`, `_clone_mac()`, `_import_macs()`, `_export_macs()`, `_recover_results()` |
| 2872-3358 | **Proxy System** | `_fetch_proxies()`, `_test_proxies()`, `_add_proxy_manual()`, `_import_proxies()`, `_remove_proxy()`, proxy rotation logic |
| 3360-3544 | **Profile Management** | `_add_profile()`, `_remove_profile()`, `_rename_profile()`, `_select_profile()`, `_update_profile_list()` |
| 3546-3649 | **Player Sidebar** | `_on_mac_select()`, `_on_profile_select()`, status indicator coloring |
| 3662-3967 | **Channel System** | `_load_genres()`, `_load_channels()`, `_on_genre_select()`, `_search_channels()`, `_switch_content_type()`, navigation stack |
| 3974-4085 | **Account Info** | `_load_account_info()`, display formatting, expiry date parsing |
| 4087-4482 | **mpv Player** | `_init_mpv()`, `_play_channel()`, `_stop_playback()`, `_pause_playback()`, `_next_channel()`, `_prev_channel()`, `_set_volume()`, `_toggle_fullscreen()`, stream URL resolution with fallbacks |
| 4484-4751 | **Scanning Engine** | `_start_scan()`, `_stop_scan()`, `_pause_scan()`, `_scan_worker()`, ThreadPoolExecutor management, channel count filtering |
| 4753-4799 | **Shutdown** | `_on_closing()`, `_save_session()`, cleanup |

### Constants (module-level in main.py)

```python
APP_VERSION = "1.2.0"
BG_DARK = "#0a0a1e"           # Main background color
BG_SIDEBAR = "#1a1a2e"        # Sidebar background
BG_INPUT = "#12122a"          # Input field background
BG_BAR = "#16162a"            # Status bar background
FG_DIM = "#888888"            # Dimmed text color
ACCENT = "#2563eb"            # Accent color (blue)
MAX_PROXY_RETRIES = 15        # Max proxy retries per MAC check
PROXY_TEST_BATCH_SIZE = 5     # Proxies tested simultaneously
MAX_LOG_SAVE = 500            # Max log entries persisted
DEFAULT_UPDATE_REPO = "FilipMichalkiewicz/flipper"
DEFAULT_UPDATE_BRANCH = "main"
```

---

## scanner.py (~620 lines)

The network protocol layer. Handles all communication with Stalker portal servers and proxy management.

### Proxy Pool Management

Thread-safe proxy pool using a module-level `threading.Lock`.

| Function | Purpose |
|---|---|
| `set_proxy_list(proxies)` | Replace entire proxy list, reset index and fail counts |
| `get_proxy_list()` | Return copy of current proxy list |
| `add_proxy(proxy)` | Add a proxy if not already present |
| `remove_proxy(proxy)` | Remove a proxy, adjust index |
| `get_current_proxy()` | Return proxy at current index |
| `rotate_proxy()` | Advance index, return next proxy |
| `report_proxy_fail(proxy)` | Increment fail count; auto-remove after 3 failures. Returns `True` if removed. |
| `report_proxy_success(proxy)` | Reset fail count to 0 |
| `should_remove_proxy(status_code)` | Returns `True` for HTTP codes: 403, 404, 407, 500-504 |

### Proxy Fetching & Testing

| Function | Purpose |
|---|---|
| `fetch_free_proxies()` | Scrapes 35+ public proxy list APIs (proxyscrape, GitHub raw, openproxylist, etc.). Returns deduplicated list. |
| `test_proxy_latency(proxy, timeout)` | Tests proxy against `http://httpbin.org/ip`. Returns latency in seconds or `None`. |
| `test_and_filter_proxies(proxies, max_latency, callback)` | Batch-tests proxies, filters by latency threshold, calls back with results. |

### MAC Address Operations

| Function | Purpose |
|---|---|
| `generate_random_mac(prefix)` | Generates a random MAC with the given 3-byte prefix (e.g., `"00:1A:79"`). Returns format `"00:1A:79:XX:XX:XX"`. |

### Stalker Portal Protocol

| Function | Purpose |
|---|---|
| `make_cookies(mac, token, user_agent)` | Builds cookie dict for portal requests (MAC, stb_lang, timezone) |
| `make_params(action, type_, **kwargs)` | Builds query params for portal API calls |
| `check_portal(url, endpoint, proxy, timeout)` | Tests if a portal endpoint responds (GET request) |
| `get_responding_endpoint(url, proxy, timeout)` | Tries 9 known endpoints, returns the first that responds |
| `get_handshake(url, endpoint, mac, proxy, timeout, user_agent)` | Performs STB handshake, returns bearer token |
| `check_mac(url, endpoint, mac, proxy, timeout, user_agent)` | Full MAC validation: handshake + `get_main_info`. Returns `(True, expiry_str)` or `(False, reason)`. |
| `count_channels_quick(url, endpoint, mac, token, proxy, timeout)` | Counts channels; handles portals with inflated `total_items` by fetching actual page data. |
| `get_genres(url, endpoint, mac, token, proxy, timeout, content_type)` | Fetches genre/category list. Handles both list and dict response formats. |
| `get_channels(url, endpoint, mac, token, proxy, timeout, genre_id, content_type, page)` | Fetches paginated channel list for a given genre. |
| `get_stream_url(url, endpoint, mac, token, proxy, timeout, cmd, content_type)` | Resolves actual stream URL from a channel `cmd` via `create_link` action. |

### Internal Helpers

| Function | Purpose |
|---|---|
| `_make_proxies_dict(proxy)` | Converts proxy string to `requests`-compatible dict |
| `_parse_expiry(raw)` | Parses expiry date string, translates months to Polish |

---

## constants.py (~40 lines)

Static configuration data used by `scanner.py`.

### `USER_AGENTS` (list of 6 strings)

MAG set-top-box user agent strings for portal authentication:
- MAG200 (2 variants)
- MAG250
- MAG254
- MAG322
- MAG420

### `ENDPOINTS` (list of 9 strings)

Known Stalker portal endpoint paths tried during portal discovery:
```
/portal.php, /c/portal.php, /c/, /c/server/load.php,
/stalker_portal/server/load.php, /stalker_portal/c/,
/server/load.php, /portal/, /
```

### `MONTHS_PL` (dict)

English-to-Polish month name mapping for expiry date display.

### File Constants

- `RESULTS_FILE = "results.txt"`
- `SESSION_FILE = "session.json"`

---

## test_gui.py (~23 lines)

Abandoned test script that was used to verify CustomTkinter rendering. No longer relevant since the project migrated to plain Tkinter. Listed in `.gitignore`.
