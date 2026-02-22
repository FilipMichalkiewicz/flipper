"""
Scanner module — portal API logic for MAC address scanning.
Based on Stalker middleware portal protocol.
"""

import hashlib
import requests
from random import randint
from datetime import datetime
from typing import Optional, List
from constants import USER_AGENTS, ENDPOINTS, MONTHS_PL


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


def check_portal(url: str, timeout: int = 5) -> bool:
    """Check if a portal endpoint responds to a handshake."""
    try:
        mac = generate_random_mac()
        params = make_params(mac, "handshake", "stb")
        cookies = make_cookies(mac)
        headers = {"User-Agent": random_user_agent(), "Accept": "*/*"}

        res = requests.get(
            url, params=params, headers=headers, cookies=cookies, timeout=timeout
        )
        if res.status_code == 200 and res.json() is not None:
            return True
        return False
    except Exception:
        return False


def get_handshake(url: str, mac: str, timeout: int = 5) -> Optional[str]:
    """Perform handshake and retrieve bearer token."""
    try:
        cookies = make_cookies(mac)
        params = make_params(mac, "handshake", "stb")
        headers = {"User-Agent": random_user_agent(), "Accept": "*/*"}

        res = requests.get(
            url, params=params, headers=headers, cookies=cookies, timeout=timeout
        )
        if res.status_code == 200 and res.json():
            token = res.json().get("js", {}).get("token", None)
            return token
        return None
    except Exception:
        return None


def get_responding_endpoint(server_address: str, timeout: int = 5) -> Optional[str]:
    """Find an endpoint that responds on the given server."""
    for endpoint in ENDPOINTS:
        url = server_address + endpoint
        if check_portal(url, timeout=timeout):
            return endpoint
    return None


def parse_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    if url.endswith("/"):
        url = url[:-1]
    return url


def check_mac(url: str, mac: str, timeout: int = 5) -> Optional[List]:
    """
    Check if a MAC address is valid on the portal.
    Returns [timestamp, expiry_string] or None.
    """
    try:
        cookies = make_cookies(mac)
        handshake = get_handshake(url, mac=mac, timeout=timeout)
        if not handshake:
            return None

        params = make_params(mac, "get_main_info", "account_info")
        headers = {
            "User-Agent": random_user_agent(),
            "Accept": "*/*",
            "Authorization": f"Bearer {handshake}",
        }

        res = requests.get(
            url, params=params, headers=headers, cookies=cookies, timeout=timeout
        )

        if res.status_code == 200 and len(res.json().get("js", {})) > 0:
            expires_at: str = res.json().get("js", {}).get("phone", "")
            if not expires_at or len(expires_at.strip()) < 5:
                return None

            str_datetime = expires_at

            # Translate month names to Polish
            for month_en, month_pl in MONTHS_PL.items():
                if month_en in str_datetime:
                    str_datetime = str_datetime.replace(month_en, month_pl)
                    break

            # Parse: "January 15, 2026 3:45 pm"
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
                    # Fallback — just return raw string
                    return [0, str_datetime]
            except Exception:
                return [0, str_datetime]

        return None
    except Exception:
        return None
