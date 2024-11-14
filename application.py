import core
import question
from colorama import Fore, Style, Back
import os
from concurrent.futures import ThreadPoolExecutor
import keyboard
from constants import *
import json

class Configuration:
    server_address: str = ''
    first_bytes: str = '00:1A:79'
    proxy_enabled: bool = False
    timeout: int = 5
    max_workers: int = 10
    save_results: bool = True
    proxy: str = ''
    url: str = ''
    create_sfvip_profile: bool = False
    to_save = ['server_address', 'first_bytes', 'proxy_enabled', 'timeout', 'max_workers', 'save_results', 'proxy', 'url', 'create_sfvip_profile']

class Application:
    __exited = False
    __options = []
    config = Configuration
    valid_macs = {}

    def __init__(self):
        self.config = Configuration()
        self.__options = [
            ['1. Wyszukiwarka działających adresów mac.', self.__mac_lookup],
            ['2. Tester adresów mac.', self.__mac_tester] 
        ]

    def exit(self):
        self.__exited = True
        config_saved = self.__save_config()
        
        if self.config.save_results:
            
            macs = []
            if os.path.exists(results_file_path):
                r_results = open(results_file_path, 'r', encoding='utf-8')
                for line in r_results.readlines():
                    if len(line) > 0 and not line.startswith('#'):
                        line = line.replace('\n', '')
                        mac, str_datetime, timestamp = line.split(' | ')
                        macs.append([mac, float(timestamp), str_datetime])

            for mac in list(self.valid_macs.keys()):
                timestamp, str_datetime = self.valid_macs[mac]
                macs.append([mac, timestamp, str_datetime])

            macs = sorted(macs, key=lambda d: d[1], reverse=True)

            if self.config.create_sfvip_profile:
                self.__create_sfvip_profile(macs[0][0])
            
            w_results = open(results_file_path, 'w', encoding='utf-8')

            w_results.write('# Adres mac z najdłuższym terminem znajduje się pod tą linijką.\n')
            for mac in macs:
                str_datetime = mac[2]
                timestamp = mac[1]
                w_results.write(f'{mac[0]} | {str_datetime} | {timestamp}\n')
            
            w_results.close()
            if config_saved:
                print(f'\n{Fore.GREEN}ZAPISANO! możesz mnie zamknąć.')

    def __save_config(self):
        last_session = open(last_session_file_path, 'w', encoding='utf-8')
        config = {}

        for var in self.config.to_save:
            config[var] = self.config.__getattribute__(var)

        session = json.dumps(config, indent=2)
        last_session.write(str(session))
        last_session.close()

        return True
    
    def __load_config(self):
        if os.path.exists(last_session_file_path):
            session = open(last_session_file_path, 'r', encoding='utf-8')
            config = json.loads(session.read())

            for var in self.config.to_save:
                self.config.__setattr__(var, config[var])
            
            print(f'''
Adres strony: {self.config.server_address}
Proxy włączone: {self.config.proxy_enabled}
Ilość procesów: {self.config.max_workers}
Adres proxy: {self.config.proxy}
                  ''')

    def __create_sfvip_profile(self, mac: str): 
        try:
            accounts_file_path = os.getenv('APPDATA')+'\SFVIP-Player\Accounts.json'
            w_accounts_file = open(accounts_file_path, 'w+', encoding='utf-8')
            accounts = []

            if os.path.exists(accounts_file_path):
                r_accounts_file = open(accounts_file_path, 'r', encoding='utf-8')
                if len(r_accounts_file.read()) > 0:
                    accounts = json.loads(r_accounts_file.read())

            accounts.append({
                "Name": self.config.server_address.replace('http://', '')+'__'+str(len(accounts)),
                "Address": self.config.server_address,
                "Mac": mac,
                "Username": "",
                "Password": "",
                "SerialNumber": "",
                "DeviceID": "",
                "DeviceID2": "",
                "Firmware": "",
                "StbModel": "",
                "TimeZone": "",
                "UserAgent": "",
                "ForceUserAgent": False
            })
            w_accounts_file.write(json.dumps(accounts, indent=2))
            w_accounts_file.close()

        except Exception as e:
            core.report(e, '__create_sfvip_profile')

    def run(self):
        try:
            core.print_logo()

            for option in self.__options:
                max_len = len('                                                         ')
                text = '  ' + option[0]
                
                if len(text) < max_len:
                    text += ' ' * (max_len - len(text))
                print(f'{Fore.BLACK}{Back.CYAN}{text}{Style.RESET_ALL}')

            print(f'{Fore.BLACK}{Back.CYAN}                                                         {Style.RESET_ALL}')

            selected_option = int(question.ask(f'\nTryb (1-{len(self.__options)}):'))

            if selected_option <= len(self.__options):
                self.__options[selected_option-1][1]()

        except Exception as e:
            core.report(e, 'run')
            self.__exited = True

    #
    # Option 1
    #

    def mac_lookup_worker(self, url: str, config: Configuration):
        try:
            mac = core.generate_random_mac(config.first_bytes)
        
            proxy = {}
            if len(config.proxy) > 0:
                proxy = {
                    'http': config.proxy
                }

            res = core.check_mac(
                url,
                mac=mac,
                timeout=config.timeout,
                proxy=proxy
            )

            if self.__exited:
                return
            
            if res:
                print(f'{Fore.GREEN}{mac} {res[1]} {Style.RESET_ALL}')

                return [mac, res]
            return
        except Exception as e:
            core.report(e, 'mac_lookup_worker')
            return

    def __mac_lookup(self):
        try:
            load_last = question.askYesNo('Załadować ostatnio używane dane? (T/N):', False) and os.path.exists(last_session_file_path)

            if load_last:
                self.__load_config()
            else:
                self.config.server_address= core.parse_url(question.ask('Wpisz adress strony:'))

            if not load_last and question.askYesNo('Chcesz dostosować opcje? (T/N):', False):
                self.config.first_bytes = question.ask('Pierwsze 3 bajty (domyślnie 00:1A:79):', '00:1A:79')
                self.config.proxy_enabled = question.askYesNo('Użyć proxy do wyszukiwania maców? (T/N):', False)
                self.config.save_results = question.askYesNo('Zapisać wyniki do pliku? (T/N):', True)
                self.config.create_sfvip_profile = question.askYesNo('Utworzyć profil sfvip player? (T/N):', False)
                self.config.timeout = int(question.ask('Limit czasu serwera na odpowiedź w sekundach (domyślnie 5):', 5))
                #self.config.max_workers = int(question.ask(f'Ile procesów chcesz utworzyć (domyślnie {os.cpu_count()}):', str(os.cpu_count())))
                self.config.max_workers = int(question.ask(f'Ile procesów chcesz utworzyć (domyślnie 14):', 14))
                
            if len(self.config.first_bytes) > 8:
                return self.__mac_lookup()
              
            if not load_last:
                proxies = []
                if self.config.proxy_enabled:
                    proxies = core.load_proxies_from_file()
                    print(f'{Fore.YELLOW}Szukanie endpoint-u, który odpowie na zapytanie przez proxy. (Może potrwać do {int((len(proxies) * self.config.timeout)/60)}min | Wszystkie proxy){Style.RESET_ALL}')      
                else:
                    print(f'{Fore.YELLOW}Szukanie endpoint-u, który odpowie na zapytanie. (Może chwilę potrwać){Style.RESET_ALL}')      
                    
                res = core.get_responding_endpoint(
                    server_address=self.config.server_address,
                    proxies=proxies,
                    timeout=self.config.timeout
                )

                if self.config.proxy_enabled and proxies and len(res) == 2:
                    
                    endpoint, proxy = res
                    url = self.config.server_address + endpoint

                    self.config.url = url
                    self.config.proxy = proxy

                    print(f'{Fore.GREEN}Używam proxy: {proxy}\nUrl: {url}{Style.RESET_ALL}')
                else:
                    endpoint = res

                    if not endpoint: 
                        print(f'{Fore.RED}Serwer nie odpowiada.{Style.RESET_ALL}')

                        if not question.askYesNo(f'Chcesz zacząć od nowa? (T/N):', True):
                            exit(0)
                        
                        core.clear_console()
                        return self.__mac_lookup()

                    url = self.config.server_address + endpoint
                    
                    self.config.url = url
            
            print(f'{Fore.GREEN}Skanowanie ...{Style.RESET_ALL}')

            executor = ThreadPoolExecutor(max_workers=self.config.max_workers)
            workers = []

            print(f'{Fore.YELLOW}Aby zakończyć działanie programu kliknij ctrl + q{Style.RESET_ALL}')

            while not self.__exited:
                if keyboard.is_pressed('ctrl+q'):
                    self.__exited = True
                    print(f'{Fore.GREEN}Zamykanie...{Style.RESET_ALL}')
                    executor.shutdown(wait=False)
                    self.exit()
                    return
                
                workers.append(executor.submit(self.mac_lookup_worker, 
                    url=self.config.url,
                    config=self.config
                ))

                for worker in workers:
                    if worker.done():
                        res = worker.result()
                        if res:
                            self.valid_macs[res[0]] = res[1]

                
        except KeyboardInterrupt as e:
            self.exit()

        except Exception as e:
            core.report(e, '__mac_lookup')
            self.exit()

    #
    # Option 2
    #

    def __mac_tester(self):
        mac = question.ask('Podaj adres mac:')
        server_address = core.parse_url(question.ask('Podaj adres strony:', ''))
        timeout = int(question.ask('Limit czasu serwera na odpowiedź w sekundach (domyślnie 5):', 5))
        proxy_enabled = question.askYesNo('Użyć proxy? (T/N):', False)

        if not mac or len(mac) < 17:
            print(f'{Fore.RED}Niepoprawny kod mac.{Style.RESET_ALL}')
            
            if question.askYesNo('Chcesz zacząć od nowa? (T/N):', False):
                return self.__mac_tester()
            
            self.exit()
        
        if not server_address:
            print(f'{Fore.RED}Brak adresu strony.{Style.RESET_ALL}')
            
            if question.askYesNo('Chcesz zacząć od nowa? (T/N):', False):
                return self.__mac_tester()
            
            self.exit()

        proxies = []
        url = None
        proxy = None

        if proxy_enabled:
            proxies = core.load_proxies_from_file()
            print(f'{Fore.YELLOW}Szukanie endpoint-u, który odpowie na zapytanie przez proxy. (Może potrwać do {int((len(proxies) * timeout)/60)}min | Wszystkie proxy){Style.RESET_ALL}')      
        else:
            print(f'{Fore.YELLOW}Szukanie endpoint-u, który odpowie na zapytanie. (Może chwilę potrwać){Style.RESET_ALL}')      
            

        res = core.get_responding_endpoint(
            server_address=server_address,
            proxies=proxies,
            timeout=timeout
        )

        if proxy_enabled and proxies and len(res) == 2: 
            endpoint, proxy = res
            url = server_address + endpoint
            proxy = proxy

            print(f'{Fore.GREEN}Używam proxy: {proxy}\nUrl: {url}{Style.RESET_ALL}')
        else:
            endpoint = res

            if not endpoint: 
                print(f'{Fore.RED}Serwer nie odpowiada.{Style.RESET_ALL}')

                if not question.askYesNo(f'Chcesz zacząć od nowa? (T/N):', False):
                    exit(0)
                
                core.clear_console()
                return self.__mac_lookup()

            url = server_address + endpoint

        res = core.check_mac(
            url,
            mac=mac,
            timeout=3,
            proxy={
                'http': proxy
            }
        )
        
        if res:
            print(f'{Fore.GREEN}Adres mac jest ważny do: {res[1]} {Style.RESET_ALL}')
        else:
            print(f'{Fore.RED}Adres mac nie działa.{Style.RESET_ALL}')
