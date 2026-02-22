# Flipper — MAC Address Scanner

Aplikacja GUI do skanowania i walidacji adresów MAC na serwerach Stalker portal.

![Flipper Scanner](https://img.shields.io/badge/Python-3.9+-blue.svg)
![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Windows-lightgrey.svg)

## 🚀 Funkcje

- ⚡ Wielowątkowe skanowanie adresów MAC
- 🎨 Ciemny interfejs graficzny (Tkinter)
- ⏸ Pauza/Wznowienie skanowania
- 📋 Kopiowanie znalezionych MAC do schowka
- 💾 Automatyczny zapis wyników
- 📁 Eksport do pliku tekstowego
- 🔄 Zapisywanie sesji (ustawienia zachowane między uruchomieniami)

## 📦 Instalacja

### Wymagania
- Python 3.9 lub nowszy
- Biblioteki z `requirements.txt`

### Instalacja zależności
```bash
pip install -r requirements.txt
```

## 🎮 Użycie

### Uruchomienie ze źródła
```bash
python3 main.py
```

### Skompilowana aplikacja macOS
Gotowa aplikacja znajduje się w folderze `dist/Flipper.app`

Aby uruchomić:
1. Otwórz folder `dist/`
2. Kliknij dwukrotnie `Flipper.app`
3. Jeśli macOS wyświetli ostrzeżenie bezpieczeństwa, kliknij prawym → Otwórz

## 🔨 Kompilacja

### macOS
```bash
./build_macos.sh
```
Wynik: `dist/Flipper.app`

### Windows
Na maszynie z Windows:
```batch
build_windows.bat
```
Wynik: `dist/Flipper.exe`

## 📖 Instrukcja użytkowania

1. **URL serwera** - Adres serwera Stalker portal
2. **Pierwsze 3 bajty MAC** - Prefix adresu MAC (np. 00:1B:79)
3. **Ilość procesów** - Liczba równoległych procesów (domyślnie: 10)
4. **Timeout** - Timeout żądania w sekundach (domyślnie: 5)
5. **START** - Rozpocznij skanowanie

### Przyciski
- **⏸ PAUZA** - Wstrzymaj skanowanie
- **⏹ STOP** - Zatrzymaj skanowanie
- **📋 Kopiuj** - Kopiuj znalezione MAC do schowka
- **📁 Eksportuj** - Eksportuj wyniki do pliku

## 📁 Struktura projektu

```
flipper/
├── main.py              # Główna aplikacja (Tkinter)
├── scanner.py           # Logika skanowania MAC
├── constants.py         # Stałe konfiguracyjne
├── requirements.txt     # Zależności Python
├── build_macos.sh       # Skrypt kompilacji macOS
├── build_windows.bat    # Skrypt kompilacji Windows
├── BUILD_README.md      # Szczegółowa dokumentacja kompilacji
└── dist/               # Skompilowane aplikacje
    └── Flipper.app     # Aplikacja macOS
```

## ⚠️ Znane problemy

### macOS: Ostrzeżenie SSL
```
NotOpenSSLWarning: urllib3 v2 only supports OpenSSL 1.1.1+
```
To ostrzeżenie jest normalne na macOS i nie wpływa na funkcjonalność.

### macOS: "Aplikacja nie może być otwarta"
1. Kliknij prawym przyciskiem → Otwórz
2. Lub: Preferencje systemowe → Bezpieczeństwo i prywatność → "Otwórz mimo to"

## 🛠️ Technologie

- **Python 3.9+**
- **Tkinter** - GUI (native, bez dodatkowych zależności)
- **requests** - HTTP requests
- **concurrent.futures** - Wielowątkowe przetwarzanie
- **PyInstaller** - Kompilacja do .app/.exe

## 📝 Licencja

Wolne do użytku i modyfikacji.

## 👨‍💻 Autor

Filip Michalkiewicz

## 🔗 Linki

- Repository: [github.com/FilipMichalkiewicz/flipper](https://github.com/FilipMichalkiewicz/flipper)
