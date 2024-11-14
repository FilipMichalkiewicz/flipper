from colorama import *

def ask(q: str, default: any = ""):
    res = str(input(f'{Fore.CYAN}{q}{Style.RESET_ALL} '))
    return res if len(res) > 0 else default

def askYesNo(q: str, default: bool = False):
    res = str(ask(q))
    return True if res.upper() == 'T' else default