"""
Scanner module — portal API logic for MAC address scanning.
Based on Stalker middleware portal protocol.
Supports proxy rotation.
"""

import hashlib
import requests
import threading
from random import randint
from datetime import datetime
from typing import Optional, List, Dict
from constants import USER_AGENTS, ENDPOINTS, MONTHS_PL


# ── Shared proxy state ────────────────────────────────────
_proxy_lock = threading.Lock()
_proxy_list: List[str] = []            # e.g. ["http://1.2.3.4:8080", ...]
_proxy_index: int = 0
_proxy_fail_counts: Dict[str, int] = {}
_PROXY_MAX_FAILS = 3


def set_proxy_list(proxies: List[str]):
    global _proxy_list, _proxy_index, _proxy_fail_counts
    with _proxy_lock:
        _proxy_list = list(proxies)
        _proxy_index = 0
        _proxy_fail_counts = {}


def get_proxy_list() -> List[str]:
    with _proxy_lock:
        return list(_proxy_list)


def add_proxy(proxy: str):
    with _proxy_lock:
        if proxy not in _proxy_list:
            _proxy_list.append(proxy)


def remove_proxy(proxy: str):
    global _proxy_index
    with _proxy_lock:
        if proxy in _proxy_list:
            _proxy_list.remove(proxy)
            if _proxy_index >= len(_proxy_list):
                _proxy_index = 0
        _proxy_fail_counts.pop(proxy, None)


def get_current_proxy() -> Optional[str]:
    """Return the current proxy or None if list is empty."""
    with _proxy_lock:
        if not _proxy_list:
            return None
        return _proxy_list[_proxy_index % len(_proxy_list)]


def rotate_proxy() -> Optional[str]:
    """Move to the next proxy and return it."""
    global _proxy_index
    with _proxy_lock:
        if not _proxy_list:
            return None
        _proxy_index = (_proxy_index + 1) % len(_proxy_list)
        return _proxy_list[_proxy_index]


def report_proxy_fail(proxy: str) -> bool:
    """Report a proxy failure. Returns True if proxy was removed from list."""
    global _proxy_index
    with _proxy_lock:
        _proxy_fail_counts[proxy] = _proxy_fail_counts.get(proxy, 0) + 1
        if _proxy_fail_counts[proxy] >= _PROXY_MAX_FAILS:
            if proxy in _proxy_list:
                _proxy_list.remove(proxy)
                if _proxy_index >= len(_proxy_list) and _proxy_list:
                    _proxy_index = 0
                _proxy_fail_counts.pop(proxy, None)
                return True
    return False


def report_proxy_success(proxy: str):
    """Reset failure counter on success."""
    with _proxy_lock:
        _proxy_fail_counts[proxy] = 0


def _make_proxies_dict(proxy: Optional[str] = None) -> Optional[dict]:
    if proxy:
        return {"http": proxy, "https": proxy}
    return None


def fetch_free_proxies() -> List[str]:
    """Fetch free HTTP proxies from public APIs."""
    proxies = []
    urls = [
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=5000&country=&ssl=all&anonymity=all",
        "https://www.proxy-list.download/api/v1/get?type=http",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    ]
    for api_url in urls:
        try:
            r = requests.get(api_url, timeout=8)
            if r.status_code == 200:
                lines = r.text.strip().split("\n")
                for line in lines:
                    line = line.strip()
                    if ":" in line and len(line) > 7:
                        proxy = f"http://{line}" if not line.startswith("http") else line
                        if proxy not in proxies:
                            proxies.append(proxy)
        except Exception:
            continue
        if len(proxies) > 50:
            break
    return proxies[:200]  # cap at 200


# ── Core helpers ──────────────────────────────────────────

def random_user_agent() -> str:
    return USER_AGENTS[randint(0, len(USER_AGENTS) - 1)]


def generate_random_mac(first_bytes: str = "00:1B:79") -> str:
    """Generate a random MAC address with given first 3 bytes."""
    tail = [randint(0, 255) for _ in range(3)]
    return first_bytes.upper() + ":" + ":".join(f"{b:02X}" for b in tail)


def make_cookies(mac: str) -> dict:
    return {
        "mac": mac,
        "sn": hashlib.md5(mac.encode()).hexdigest(),
        "device_id": hashlib.sha256(mac.encode()).hexdigest(),
        "stb_lang": "en",
        "timezone": "Europe/Amsterdam",
    }


def make_params(mac: str, action: str, _type: str) -> dict:
    return {
        "mac": mac,
        "user": mac,
        "password": mac,
        "action": action,
        "type": _type,
        "token": "",
    }


def _request_get(url, params=None, headers=None, cookies=None,
                 timeout=5, proxy=None):
    """Wrapper around requests.get with optional proxy."""
    proxies = _make_proxies_dict(proxy)
    return requests.get(url, params=params, headers=headers,
                        cookies=cookies, timeout=timeout,
                        proxies=proxies)


# ── Portal functions ──────────────────────────────────────

def check_portal(url: str, timeout: int = 5, proxy: str = None) -> bool:
    """Check if a portal endpoint responds to a handshake."""
    try:
        mac = generate_random_mac()
        params = make_params(mac, "handshake", "stb")
        cookies = make_cookies(mac)
        headers = {"User-Agent": random_user_agent(), "Accept": "*/*"}

        res = _request_get(url, params=params, headers=headers,
                           cookies=cookies, timeout=timeout, proxy=proxy)
        if res.status_code == 200 and res.json() is not None:
            return True
        return False
    except Exception:
        return False


def get_handshake(url: str, mac: str, timeout: int = 5,
                  proxy: str = None) -> Optional[str]:
    """Perform handshake and retrieve bearer token."""
    try:
        cookies = make_cookies(mac)
        params = make_params(mac, "handshake", "stb")
        headers = {"User-Agent": random_user_agent(), "Accept": "*/*"}

        res = _request_get(url, params=params, headers=headers,
                           cookies=cookies, timeout=timeout, proxy=proxy)
        if res.status_code == 200 and res.json():
            token = res.json().get("js", {}).get("token", None)
            return token
        return None
    except Exception:
        return None


def get_responding_endpoint(server_address: str, timeout: int = 5,
                            proxy: str = None) -> Optional[str]:
    """Find an endpoint that responds on the given server."""
    for endpoint in ENDPOINTS:
        url = server_address + endpoint
        if check_portal(url, timeout=timeout, proxy=proxy):
            return endpoint
    return None


def parse_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    if url.endswith("/"):
        url = url[:-1]
    return url


def check_mac(url: str, mac: str, timeout: int = 5,
              proxy: str = None) -> Optional[List]:
    """
    Check if a MAC address is valid on the portal.
    Returns [timestamp, expiry_string] or None.
    """
    try:
        cookies = make_cookies(mac)
        handshake = get_handshake(url, mac=mac, timeout=timeout, proxy=proxy)
        if not handshake:
            return None

        params = make_params(mac, "get_main_info", "account_info")
        headers = {
            "User-Agent": random_user_agent(),
            "Accept": "*/*",
            "Authorization": f"Bearer {handshake}",
        }

        res = _request_get(url, params=params, headers=headers,
                           cookies=cookies, timeout=timeout, proxy=proxy)

        if res.status_code == 200 and len(res.json().get("js", {})) > 0:
            expires_at: str = res.json().get("js", {}).get("phone", "")
            if not expires_at or len(expires_at.strip()) < 5:
                return None

            str_datetime = expires_at

            for month_en, month_pl in MONTHS_PL.items():
                if month_en in str_datetime:
                    str_datetime = str_datetime.replace(month_en, month_pl)
                    break

            try:
                parts = expires_at.replace(",", "").split(" ")
                if len(parts) >= 5:
                    month_str, day, year, hh, tf = parts[:5]
                    hour, minute = hh.split(":")
                    month_num = str(list(MONTHS_PL.keys()).index(month_str) + 1).zfill(2)
                    day = day.zfill(2)
                    h = int(hour)
                    if tf.lower() == "pm" and h != 12:
                        h += 12
                    elif tf.lower() == "am" and h == 12:
                        h = 0
                    hour_str = str(h).zfill(2)
                    minute = minute.zfill(2)
                    timestamp = datetime.strptime(
                        f"{day}/{month_num}/{year}/{hour_str}/{minute}",
                        "%d/%m/%Y/%H/%M",
                    ).timestamp()
                    return [timestamp, str_datetime]
                else:
                    return [0, str_datetime]
            except Exception:
                return [0, str_datetime]

        return None
    except Exception:
        return None


# ── Channel / IPTV functions ──────────────────────────────

def get_genres(url: str, mac: str, token: str, timeout: int = 5,
               proxy: str = None) -> List[Dict]:
    """Get list of TV genres/categories."""
    try:
        cookies = make_cookies(mac)
        params = make_params(mac, "get_genres", "itv")
        headers = {
            "User-Agent": random_user_agent(),
            "Accept": "*/*",
            "Authorization": f"Bearer {token}",
        }
        res = _request_get(url, params=params, headers=headers,
                           cookies=cookies, timeout=timeout, proxy=proxy)
        if res.status_code == 200:
            data = res.json().get("js", [])
            if isinstance(data, list):
                return data
        return []
    except Exception:
        return []


def get_channels(url: str, mac: str, token: str, genre_id: str = "*",
                 page: int = 1, timeout: int = 5,
                 proxy: str = None) -> List[Dict]:
    """Get list of TV channels for a genre."""
    try:
        cookies = make_cookies(mac)
        params = {
            "mac": mac,
            "user": mac,
            "password": mac,
            "action": "get_ordered_list",
            "type": "itv",
            "genre": genre_id,
            "p": str(page),
            "JsHttpRequest": "1-xml",
            "force_ch_link_check": "",
            "sortby": "number",
            "fav": "0",
        }
        headers = {
            "User-Agent": random_user_agent(),
            "Accept": "*/*",
            "Authorization": f"Bearer {token}",
        }
        res = _request_get(url, params=params, headers=headers,
                           cookies=cookies, timeout=timeout, proxy=proxy)
        if res.status_code == 200:
            js = res.json().get("js", {})
            data = js.get("data", [])
            if isinstance(data, list):
                return data
        return []
    except Exception:
        return []


def get_stream_url(url: str, mac: str, token: str, cmd: str,
                   timeout: int = 5, proxy: str = None) -> Optional[str]:
    """Get the actual stream URL for a channel."""
    try:
        cookies = make_cookies(mac)
        params = {
            "mac": mac,
            "user": mac,
            "password": mac,
            "action": "create_link",
            "type": "itv",
            "cmd": cmd,
            "JsHttpRequest": "1-xml",
        }
        headers = {
            "User-Agent": random_user_agent(),
            "Accept": "*/*",
            "Authorization": f"Bearer {token}",
        }
        res = _request_get(url, params=params, headers=headers,
                           cookies=cookies, timeout=timeout, proxy=proxy)
        if res.status_code == 200:
            js = res.json().get("js", {})
            cmd_out = js.get("cmd", "")
            # Format: "ffmpeg http://..." -> extract url
            if cmd_out.startswith("ffmpeg "):
                return cmd_out[7:].strip()
            elif cmd_out.startswith("http"):
                return cmd_out.strip()
        return None
    except Exception:
        return None
