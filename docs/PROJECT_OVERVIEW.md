# Flipper - Project Overview

## What is Flipper?

Flipper is a desktop GUI application for scanning and validating MAC addresses on Stalker portal IPTV servers. It includes an integrated IPTV player powered by mpv. The application is built in Python with a dark-themed Tkinter interface and runs on both Windows and macOS.

**Version:** 1.2.0
**Author:** Filip Michalkiewicz
**Repository:** https://github.com/FilipMichalkiewicz/flipper

## Core Features

- **MAC Address Scanning** - Parallel scanning of MAC addresses against Stalker portal servers using a ThreadPoolExecutor. Supports configurable worker count, timeout, and MAC prefix.
- **Proxy Rotation** - All scanning traffic is routed through proxies. Proxies are auto-fetched from 35+ public sources, tested for latency, and rotated during scans. Failed proxies are automatically removed.
- **Integrated IPTV Player** - Embedded mpv-based video player supporting TV (live), VOD, and Series content types. Includes channel browsing, genre filtering, search, and navigation history.
- **Profile Management** - Save and switch between named profiles (MAC + server + proxy combinations).
- **Session Persistence** - Full application state is saved to `session.json` and restored on launch (found MACs, proxy lists, settings, logs, profiles).
- **Auto-Update** - Checks GitHub for new versions on startup, downloads source, and triggers a rebuild (Windows).
- **Account Info** - Displays account details for a selected MAC (expiry date, IP, status, STB type).
- **Channel Count Filtering** - Filter found MACs by minimum channel count.

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.9+ |
| GUI | Tkinter (plain, dark theme) |
| HTTP Client | `requests` |
| Video Player | `python-mpv` (libmpv bindings) |
| Concurrency | `threading`, `concurrent.futures.ThreadPoolExecutor` |
| Build | PyInstaller (onefile bundles) |
| Encryption | Windows DPAPI / XOR fallback |

## Dependencies

```
requests>=2.31.0
python-mpv>=1.0.0
```

Tkinter ships with Python's standard library. On Windows, the libmpv DLL is required for the player and is automatically downloaded during the build process.

## Quick Start

### Running from Source

```bash
# Clone the repository
git clone https://github.com/FilipMichalkiewicz/flipper.git
cd flipper

# Install dependencies
pip install -r requirements.txt

# Run the application
python main.py
```

### Prerequisites

- **Python 3.9+** with Tkinter support
- **libmpv** - required for the embedded video player
  - **macOS:** `brew install mpv`
  - **Windows:** The build script downloads it automatically, or place `libmpv-2.dll` in the project directory

### Building Distributable Binaries

- **macOS:** `./build_macos.sh` - produces `dist/Flipper.app`
- **Windows:** `build_windows.bat` - produces `Flipper.exe` on your Desktop

See [BUILD_AND_DEPLOYMENT.md](BUILD_AND_DEPLOYMENT.md) for detailed build instructions.

## Project Structure

```
flipper/
├── main.py              # Main application (GUI + all logic, ~4800 lines)
├── scanner.py           # Network scanning engine (portal API, ~620 lines)
├── constants.py         # Static config (user agents, endpoints, ~40 lines)
├── requirements.txt     # Python dependencies
├── version.txt          # Current version string
├── changes.txt          # Changelog for auto-update dialog
├── Flipper.spec         # PyInstaller build specification
├── build_macos.sh       # macOS build script
├── build_windows.bat    # Windows build script
├── cleanup_build.sh     # macOS build artifact cleanup
├── cleanup_build.bat    # Windows build artifact cleanup
├── BUILD_README.md      # Build documentation (English)
├── BUILD_OBFUSCATION.md # Obfuscation strategy docs
├── README.md            # Project README (Polish)
└── docs/                # Developer documentation
```

## Further Reading

- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture, threading model, state management
- [MODULES.md](MODULES.md) - Detailed documentation for each source file
- [NETWORKING.md](NETWORKING.md) - Stalker portal protocol, proxy system, API calls
- [DATA_PERSISTENCE.md](DATA_PERSISTENCE.md) - Session storage, caching, encryption
- [BUILD_AND_DEPLOYMENT.md](BUILD_AND_DEPLOYMENT.md) - Build process, auto-update mechanism
- [CONTRIBUTING.md](CONTRIBUTING.md) - Development setup, code conventions, known issues
