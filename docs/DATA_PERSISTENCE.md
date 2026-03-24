# Data Persistence

## Overview

Flipper uses JSON file-based persistence. There is no database. All application state is serialized to files in a platform-specific data directory.

## Data Directory

| Platform | Path |
|---|---|
| Windows | `%APPDATA%\Flipper\` |
| macOS | `~/Desktop/flipper-config/` |

Legacy migration: the code handles migration from older storage locations (`%LOCALAPPDATA%\Flipper`, `Desktop\flipper-config`) to the canonical paths above.

---

## Session File (`session.json`)

The primary persistence file. Loaded on startup, saved on shutdown and periodically during operations.

### Schema

```json
{
  "url": "http://portal.example.com",
  "mac_prefix": "00:1A:79",
  "workers": 5,
  "timeout": 10,
  "save_results": true,
  "proxy_inline": false,
  "use_proxy": true,
  "player_use_proxy": true,
  "verbose_logs": false,
  "debug_console": false,
  "save_folder": "/path/to/saves",
  "min_channels": 0,
  "max_proxy_latency": 4.0,
  "checked_count": 1234,
  "found_count": 12,
  "active_macs": [
    {
      "url": "http://portal.example.com",
      "mac": "00:1A:79:AB:CD:EF",
      "expiry": "2025-12-31",
      "proxy": "http://1.2.3.4:8080"
    }
  ],
  "mac_proxy_map": {
    "00:1A:79:AB:CD:EF": "http://1.2.3.4:8080"
  },
  "mac_status": {
    "00:1A:79:AB:CD:EF": "active"
  },
  "proxies": ["http://1.2.3.4:8080", "http://5.6.7.8:3128"],
  "profiles": [
    {
      "name": "My Profile",
      "mac": "00:1A:79:AB:CD:EF",
      "url": "http://portal.example.com",
      "proxy": "http://1.2.3.4:8080"
    }
  ],
  "active_profile": { "...same as profile object..." },
  "logs": [
    ["[12:34:56] Found MAC 00:1A:79:AB:CD:EF", "success"]
  ],
  "github_token_enc": "base64_encoded_encrypted_token"
}
```

### Field Reference

| Field | Type | Description |
|---|---|---|
| `url` | string | Last used portal URL |
| `mac_prefix` | string | Last used MAC prefix (e.g., `"00:1A:79"`) |
| `workers` | int | Thread pool worker count |
| `timeout` | int | HTTP request timeout in seconds |
| `save_results` | bool | Whether to auto-save results to file |
| `proxy_inline` | bool | Show proxy in MAC list display |
| `use_proxy` | bool | Use proxies for scanning |
| `player_use_proxy` | bool | Use proxies for stream playback |
| `verbose_logs` | bool | Enable detailed logging |
| `debug_console` | bool | Enable Windows debug console |
| `save_folder` | string | Directory for result file output |
| `min_channels` | int | Minimum channel count filter |
| `max_proxy_latency` | float | Maximum accepted proxy latency (seconds) |
| `checked_count` | int | Cumulative MACs checked |
| `found_count` | int | Cumulative valid MACs found |
| `active_macs` | array | List of found MAC objects |
| `mac_proxy_map` | object | MAC-to-proxy mapping |
| `mac_status` | object | MAC validation status |
| `proxies` | array | Current working proxy list |
| `profiles` | array | Saved profile objects |
| `active_profile` | object/null | Currently active profile |
| `logs` | array | Log history (max 500 entries) |
| `github_token_enc` | string | Encrypted GitHub PAT (base64) |

---

## Results File (`results.txt`)

Auto-saved text file containing found MACs. Written when `save_results` is enabled.

### Format

```
00:1A:79:AB:CD:EF | 2025-12-31 | http://portal.example.com | ch=150 | http://1.2.3.4:8080
00:1A:79:11:22:33 | 2025-06-15 | http://portal.example.com | ch=200 | http://5.6.7.8:3128
```

Each line: `MAC | expiry | portal_url | ch=channel_count | proxy`

Used as a fallback recovery source if `session.json` is missing or corrupted.

---

## Channel Cache (`channels_cache.json`)

Caches fetched channel and genre data to avoid redundant portal requests.

### Key Format

```
{url}|{mac}|{content_type}|{genre_id}
```

Example: `http://portal.example.com|00:1A:79:AB:CD:EF|itv|3`

### Lifecycle

- Loaded on startup
- Updated when channels/genres are fetched from a portal
- Saved after each fetch
- No automatic expiration (stale data is overwritten on next fetch)

---

## Proxy File (`proxy.txt`)

Periodically saved list of working proxies, one per line. Written every 10 accepted proxies during proxy testing.

```
http://1.2.3.4:8080
http://5.6.7.8:3128
http://9.10.11.12:80
```

---

## Encryption

### GitHub Personal Access Token

The GitHub PAT used for auto-update API calls is encrypted at rest in `session.json`.

#### Windows: DPAPI

- Uses `CryptProtectData` / `CryptUnprotectData` from `crypt32.dll`
- Entropy: `b"FlipperPATv1"`
- Encrypted bytes are base64-encoded for JSON storage
- Tied to the Windows user account — cannot be decrypted by another user or on another machine

#### Non-Windows: XOR Obfuscation

- XOR key derived from: `USERNAME` environment variable + `socket.gethostname()`
- Provides obfuscation, not cryptographic security
- Base64-encoded for JSON storage

### Security Notes

- DPAPI provides genuine encryption tied to the user's Windows credentials
- The XOR fallback is a deterrent against casual inspection, not a security boundary
- No other sensitive data is encrypted — portal URLs, MACs, and proxies are stored in plaintext
- The `session.json` file should be treated as sensitive
