"""
Scanner module — portal API logic for MAC address scanning.
Supports proxy rotation, HTTP status code reporting, VOD/Series content.
"""

import hashlib
import time
import requests
import threading
from random import randint
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from constants import USER_AGENTS, ENDPOINTS, MONTHS_PL

# Status codes that indicate proxy should be removed
PROXY_BAD_CODES = {403, 404, 407, 500, 501, 502, 503, 504}

# ── Shared proxy state ────────────────────────────────────
_proxy_lock = threading.Lock()
_proxy_list: List[str] = []
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
    with _proxy_lock:
        if not _proxy_list:
            return None
        return _proxy_list[_proxy_index % len(_proxy_list)]


def rotate_proxy() -> Optional[str]:
    global _proxy_index
    with _proxy_lock:
        if not _proxy_list:
            return None
        _proxy_index = (_proxy_index + 1) % len(_proxy_list)
        return _proxy_list[_proxy_index]


def report_proxy_fail(proxy: str) -> bool:
    """Report failure. Returns True if proxy was removed."""
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
    with _proxy_lock:
        _proxy_fail_counts[proxy] = 0


def should_remove_proxy(status_code: int) -> bool:
    """Check if HTTP status code means proxy should be removed."""
    return status_code in PROXY_BAD_CODES


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
        "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
        "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
        "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/http_proxies.txt",
        "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt",
        "https://raw.githubusercontent.com/prxchk/proxy-list/main/http.txt",
        "https://raw.githubusercontent.com/zloi-user/hideip.me/main/http.txt",
        "https://api.openproxylist.xyz/http.txt",
        "https://proxyspace.pro/http.txt",
    ]
    for api_url in urls:
        try:
            r = requests.get(api_url, timeout=8)
            if r.status_code == 200:
                for line in r.text.strip().split("\n"):
                    line = line.strip()
                    if ":" in line and len(line) > 7:
                        p = f"http://{line}" if not line.startswith("http") else line
                        if p not in proxies:
                            proxies.append(p)
        except Exception:
            continue
    return proxies[:500]


def test_proxy_latency(proxy: str, timeout: float = 5.0) -> float:
    """Test proxy latency in seconds. Returns latency or float('inf') on failure."""
    test_url = "http://httpbin.org/ip"
    proxies_dict = {"http": proxy, "https": proxy}
    try:
        start = time.time()
        r = requests.get(test_url, proxies=proxies_dict, timeout=timeout)
        elapsed = time.time() - start
        if r.status_code == 200:
            return round(elapsed, 3)
    except Exception:
        pass
    return float('inf')


def test_and_filter_proxies(proxies: List[str], max_latency: float = 4.0,
                            max_workers: int = 30,
                            callback=None) -> List[Tuple[str, float]]:
    """Test all proxies in parallel and return sorted list of (proxy, latency)
    where latency <= max_latency. Calls callback(tested, total, proxy, latency)
    for progress updates."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = []
    total = len(proxies)
    tested = [0]  # mutable counter for thread safety
    lock = threading.Lock()

    def _test(p):
        lat = test_proxy_latency(p, timeout=max_latency + 1)
        with lock:
            tested[0] += 1
        if callback:
            callback(tested[0], total, p, lat)
        return (p, lat)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_test, p): p for p in proxies}
        for future in as_completed(futures):
            try:
                proxy, latency = future.result()
                if latency <= max_latency:
                    results.append((proxy, latency))
            except Exception:
                continue

    results.sort(key=lambda x: x[1])
    return results


# ── Core helpers ──────────────────────────────────────────

def random_user_agent() -> str:
    return USER_AGENTS[randint(0, len(USER_AGENTS) - 1)]


def generate_random_mac(first_bytes: str = "00:1B:79") -> str:
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
        "mac": mac, "user": mac, "password": mac,
        "action": action, "type": _type, "token": "",
    }


def _request_get(url, params=None, headers=None, cookies=None,
                 timeout=5, proxy=None):
    proxies = _make_proxies_dict(proxy)
    if headers and "X-User-Agent" not in headers:
        headers["X-User-Agent"] = "Model: MAG250; Link: Ethernet"
    return requests.get(url, params=params, headers=headers,
                        cookies=cookies, timeout=timeout, proxies=proxies)


# ── Portal functions (return status codes) ────────────────

def check_portal(url: str, timeout: int = 5,
                 proxy: str = None) -> Tuple[bool, int]:
    """Returns (responding, status_code)."""
    try:
        mac = generate_random_mac()
        params = make_params(mac, "handshake", "stb")
        cookies = make_cookies(mac)
        headers = {"User-Agent": random_user_agent(), "Accept": "*/*"}
        res = _request_get(url, params=params, headers=headers,
                           cookies=cookies, timeout=timeout, proxy=proxy)
        if res.status_code == 200 and res.json() is not None:
            return (True, res.status_code)
        return (False, res.status_code)
    except Exception:
        return (False, 0)


def get_handshake(url: str, mac: str, timeout: int = 5,
                  proxy: str = None) -> Tuple[Optional[str], int]:
    """Returns (token_or_none, status_code)."""
    try:
        cookies = make_cookies(mac)
        params = make_params(mac, "handshake", "stb")
        headers = {"User-Agent": random_user_agent(), "Accept": "*/*"}
        res = _request_get(url, params=params, headers=headers,
                           cookies=cookies, timeout=timeout, proxy=proxy)
        if res.status_code == 200 and res.json():
            token = res.json().get("js", {}).get("token", None)
            return (token, res.status_code)
        return (None, res.status_code)
    except Exception:
        return (None, 0)


def get_responding_endpoint(server_address: str, timeout: int = 5,
                            proxy: str = None) -> Tuple[Optional[str], int]:
    """Returns (endpoint_or_none, last_status_code)."""
    last_code = 0
    for endpoint in ENDPOINTS:
        url = server_address + endpoint
        ok, code = check_portal(url, timeout=timeout, proxy=proxy)
        last_code = code
        if ok:
            return (endpoint, code)
    return (None, last_code)


def parse_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    if url.endswith("/"):
        url = url[:-1]
    return url


def check_mac(url: str, mac: str, timeout: int = 5,
              proxy: str = None) -> dict:
    """
    Check if a MAC address is valid on the portal.
    Returns dict: {found, mac, codes, expiry, timestamp, error,
                   elapsed_ms, request_info, response_info}
    codes is list of HTTP status codes encountered.
    """
    result = {
        "found": False, "mac": mac, "codes": [],
        "expiry": None, "timestamp": None, "error": None,
        "elapsed_ms": 0.0, "request_info": "", "response_info": "",
    }
    t_start = time.time()
    try:
        cookies = make_cookies(mac)
        token, hs_code = get_handshake(url, mac=mac, timeout=timeout,
                                       proxy=proxy)
        result["codes"].append(hs_code)

        if not token:
            result["error"] = f"Handshake failed (HTTP {hs_code})"
            result["elapsed_ms"] = (time.time() - t_start) * 1000
            return result

        params = make_params(mac, "get_main_info", "account_info")
        headers = {
            "User-Agent": random_user_agent(),
            "Accept": "*/*",
            "Authorization": f"Bearer {token}",
        }
        result["request_info"] = (
            f"GET {url}?action=get_main_info&type=account_info"
            f"&mac={mac}  proxy={proxy or 'none'}")
        res = _request_get(url, params=params, headers=headers,
                           cookies=cookies, timeout=timeout, proxy=proxy)
        result["codes"].append(res.status_code)
        try:
            result["response_info"] = res.text[:500]
        except Exception:
            pass

        if res.status_code == 200 and len(res.json().get("js", {})) > 0:
            expires_at = res.json().get("js", {}).get("phone", "")
            if not expires_at or len(expires_at.strip()) < 5:
                return result

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
                    month_num = str(
                        list(MONTHS_PL.keys()).index(month_str) + 1
                    ).zfill(2)
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
                    result.update(found=True, expiry=str_datetime,
                                  timestamp=timestamp)
                else:
                    result.update(found=True, expiry=str_datetime,
                                  timestamp=0)
            except Exception:
                result.update(found=True, expiry=str_datetime, timestamp=0)
        else:
            result["error"] = f"Account info (HTTP {res.status_code})"

        result["elapsed_ms"] = (time.time() - t_start) * 1000
        return result

    except requests.exceptions.Timeout:
        result["error"] = "Timeout"
        result["codes"].append(-1)
        result["elapsed_ms"] = (time.time() - t_start) * 1000
        return result
    except requests.exceptions.ProxyError:
        result["error"] = "Proxy error"
        result["codes"].append(0)
        result["elapsed_ms"] = (time.time() - t_start) * 1000
        return result
    except requests.exceptions.ConnectionError:
        result["error"] = "Connection error"
        result["codes"].append(0)
        result["elapsed_ms"] = (time.time() - t_start) * 1000
        return result
    except Exception as e:
        result["error"] = str(e)[:80]
        result["elapsed_ms"] = (time.time() - t_start) * 1000
        return result


def count_channels_quick(url: str, mac: str, timeout: int = 5,
                         proxy: str = None) -> int:
    """Quick channel count — handshake + single page fetch.
    Returns total_items count or 0 on failure."""
    try:
        token, _ = get_handshake(url, mac, timeout=timeout, proxy=proxy)
        if not token:
            return 0
        cookies = make_cookies(mac)
        params = {
            "mac": mac, "user": mac, "password": mac,
            "action": "get_ordered_list", "type": "itv",
            "p": "1", "JsHttpRequest": "1-xml",
            "force_ch_link_check": "", "fav": "0",
            "genre": "*", "sortby": "number",
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
            data = js.get("data", []) if isinstance(js, dict) else []

            total_raw = js.get("total_items", 0) if isinstance(js, dict) else 0
            try:
                total = int(total_raw)
            except (ValueError, TypeError):
                total = len(data)

            # Some portals return absurd total_items values (e.g. 46000).
            # If total looks suspiciously high, count real unique channels
            # across pages with a hard cap.
            if 0 <= total <= 5000:
                return total if total > 0 else len(data)

            unique_keys = set()

            def _item_key(item: dict):
                cmd = str(item.get("cmd", "")).strip()
                if cmd:
                    return ("cmd", cmd)
                item_id = str(item.get("id", "")).strip()
                if item_id:
                    return ("id", item_id)
                name = str(item.get("name", item.get("o_name", ""))).strip()
                if name:
                    return ("name", name)
                return None

            for row in data if isinstance(data, list) else []:
                if isinstance(row, dict):
                    key = _item_key(row)
                    if key:
                        unique_keys.add(key)

            max_pages = 120
            consecutive_no_growth = 0
            for page in range(2, max_pages + 1):
                params["p"] = str(page)
                page_res = _request_get(url, params=params, headers=headers,
                                        cookies=cookies, timeout=timeout,
                                        proxy=proxy)
                if page_res.status_code != 200:
                    break

                page_js = page_res.json().get("js", {})
                page_data = page_js.get("data", []) if isinstance(page_js, dict) else []
                if not isinstance(page_data, list) or not page_data:
                    break

                before = len(unique_keys)
                for row in page_data:
                    if isinstance(row, dict):
                        key = _item_key(row)
                        if key:
                            unique_keys.add(key)

                if len(unique_keys) == before:
                    consecutive_no_growth += 1
                else:
                    consecutive_no_growth = 0

                if len(page_data) < 10 or consecutive_no_growth >= 2:
                    break

            return len(unique_keys)
        return 0
    except Exception:
        return 0


# ── Channel / IPTV / VOD functions ───────────────────────

def get_genres(url: str, mac: str, token: str,
               content_type: str = "itv", timeout: int = 5,
               proxy: str = None) -> List[Dict]:
    """Get genres/categories. content_type: 'itv', 'vod', 'series'."""
    try:
        cookies = make_cookies(mac)
        action = "get_genres" if content_type == "itv" else "get_categories"
        params = make_params(mac, action, content_type)
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
            if isinstance(data, dict):
                # Some portals wrap genres in {"result": [...]}
                for key in ("result", "data", "items"):
                    inner = data.get(key)
                    if isinstance(inner, list):
                        return inner
        return []
    except Exception:
        return []


def get_channels(url: str, mac: str, token: str,
                 genre_id: str = "*", content_type: str = "itv",
                 page: int = 1, timeout: int = 5,
                 proxy: str = None) -> List[Dict]:
    """Get items list. Works for itv, vod, series."""
    try:
        cookies = make_cookies(mac)
        params = {
            "mac": mac, "user": mac, "password": mac,
            "action": "get_ordered_list",
            "type": content_type,
            "p": str(page),
            "JsHttpRequest": "1-xml",
            "force_ch_link_check": "",
            "fav": "0",
        }
        if content_type == "itv":
            params["genre"] = genre_id
            params["sortby"] = "number"
        else:
            params["category"] = genre_id
            params["sortby"] = "added"

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
                   content_type: str = "itv", timeout: int = 5,
                   proxy: str = None) -> Optional[str]:
    """Get actual stream URL for a channel/vod item."""
    try:
        cookies = make_cookies(mac)
        params = {
            "mac": mac, "user": mac, "password": mac,
            "action": "create_link",
            "type": content_type,
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
            if cmd_out.startswith("ffmpeg "):
                return cmd_out[7:].strip()
            elif cmd_out.startswith("http"):
                return cmd_out.strip()
        return None
    except Exception:
        return None
