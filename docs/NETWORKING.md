# Networking & Protocol Reference

## Stalker Portal Protocol

Flipper communicates with Stalker Portal IPTV middleware — the same protocol used by MAG set-top-boxes. All communication is HTTP GET-based with cookies for authentication context.

### Authentication Flow

```
1. Portal Discovery
   GET {url}{endpoint}
   Try 9 known endpoints until one responds (200 OK)

2. Handshake
   GET {url}{endpoint}?action=handshake&type=stb&token=&...
   Cookies: mac={MAC}, stb_lang=en, timezone=Europe/London
   User-Agent: MAG200/250/254/322/420
   → Returns: { "js": { "token": "BEARER_TOKEN" } }

3. Account Validation
   GET {url}{endpoint}?action=get_main_info&type=account_info
   Authorization: Bearer {token}
   Cookies: mac={MAC}
   → Returns: { "js": { "mac": "...", "phone": "expiry_date", ... } }
```

### Content Fetching

```
4. Get Genres/Categories
   GET {url}{endpoint}?action=get_genres&type=itv
   (or type=vod / type=series)
   Authorization: Bearer {token}
   → Returns: { "js": [ { "id": "1", "title": "News" }, ... ] }

5. Get Channel List
   GET {url}{endpoint}?action=get_ordered_list&type=itv
       &genre={genre_id}&p={page}&sortby=name
   Authorization: Bearer {token}
   → Returns: { "js": { "data": [...], "total_items": N } }

6. Get Stream URL
   GET {url}{endpoint}?action=create_link&type=itv&cmd={cmd}
   Authorization: Bearer {token}
   → Returns: { "js": { "cmd": "http://stream.example.com/live/..." } }
```

### Request Headers

Every request includes:
- `Cookie`: `mac={url_encoded_MAC}; stb_lang=en; timezone=Europe/London`
- `User-Agent`: Randomly selected from 6 MAG STB user agent strings
- `Authorization`: `Bearer {token}` (after handshake)

### Content Types

| Type | API `type` param | Description |
|---|---|---|
| Live TV | `itv` | Live IPTV channels |
| Video on Demand | `vod` | Movie catalog |
| Series | `series` | TV series catalog |

### Known Endpoints

The scanner tries these paths in order until one responds:

1. `/portal.php`
2. `/c/portal.php`
3. `/c/`
4. `/c/server/load.php`
5. `/stalker_portal/server/load.php`
6. `/stalker_portal/c/`
7. `/server/load.php`
8. `/portal/`
9. `/`

---

## Proxy System

### Design Principle

All scanning traffic is routed through proxies — the scanner never connects directly to portals. This is a hard requirement, not a configuration toggle (though the player has an option to disable proxy use for stream playback).

### Proxy Sources

Proxies are fetched from 35+ public proxy list APIs including:
- proxyscrape.com
- GitHub raw proxy list repositories
- openproxylist
- Various community-maintained lists

All sources return HTTP/HTTPS proxies in `ip:port` format.

### Proxy Lifecycle

```
1. Fetch       → fetch_free_proxies() scrapes 35+ APIs
                  Deduplicates results
                  
2. Test        → test_and_filter_proxies()
                  Tests each proxy against httpbin.org/ip
                  Filters by max latency (default 4.0s)
                  Saves working proxies to proxy.txt every 10 accepted
                  
3. Use         → get_current_proxy() / rotate_proxy()
                  Round-robin rotation through working list
                  
4. Health      → report_proxy_fail() / report_proxy_success()
                  3 consecutive failures → auto-remove
                  HTTP 403/404/407/500-504 → immediate flag for removal
                  Success → reset fail count
```

### Proxy Pool (Thread Safety)

The proxy pool in `scanner.py` uses a module-level `threading.Lock` to protect:
- `_proxy_list` — the list of active proxies
- `_proxy_index` — current round-robin position
- `_proxy_fail_counts` — per-proxy failure counter

All access goes through lock-guarded functions. This is critical because multiple scan workers read/write the proxy pool concurrently.

### Retry Logic

When a scan worker fails to check a MAC:
1. Report the proxy failure via `report_proxy_fail()`
2. Rotate to the next proxy via `rotate_proxy()`
3. Retry the MAC check (up to `MAX_PROXY_RETRIES = 15` times)
4. If all retries exhausted, skip the MAC

### Proxy Format

Proxies are stored and used as plain strings: `http://ip:port`

Converted to `requests` format:
```python
{"http": "http://ip:port", "https": "http://ip:port"}
```

---

## GitHub API (Auto-Update)

The auto-update system (Windows only) uses the GitHub API:

| Endpoint | Purpose |
|---|---|
| `GET /repos/{owner}/{repo}/contents/version.txt` | Check remote version |
| `GET /repos/{owner}/{repo}/contents/changes.txt` | Fetch changelog for update dialog |
| `GET /repos/{owner}/{repo}/zipball/{branch}` | Download source archive |

Authentication uses a GitHub Personal Access Token (stored encrypted at rest). The token is optional — public repos work without it, but rate limits are lower.

### Update Flow (Windows)

```
1. Startup → GET version.txt from GitHub
2. Compare remote version with local APP_VERSION
3. If newer → GET changes.txt, show dialog
4. User confirms → GET zipball, extract to Desktop (hidden dir)
5. Generate runner.bat that calls build_windows.bat
6. Launch runner.bat, exit current app
7. Runner builds new .exe, cleans up source, copies to Desktop
```

---

## Error Handling

### Network Errors

- All HTTP requests use configurable timeouts (default varies by operation)
- `requests.exceptions.RequestException` is caught broadly for resilience
- Connection errors trigger proxy rotation, not application crashes
- Portal protocol errors (malformed JSON, missing fields) are logged and skipped

### HTTP Status Code Handling

| Code | Action |
|---|---|
| 200 | Success — process response |
| 403 | Forbidden — flag proxy for removal |
| 404 | Not found — flag proxy for removal |
| 407 | Proxy auth required — flag proxy for removal |
| 500-504 | Server error — flag proxy for removal |
| Other | Log error, retry with next proxy |
