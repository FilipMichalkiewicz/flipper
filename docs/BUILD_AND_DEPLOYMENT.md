# Build & Deployment

## Overview

Flipper is distributed as standalone executables built with PyInstaller. The build produces a single-file binary for each platform — no Python installation required by end users.

---

## macOS Build

### Script: `build_macos.sh`

```bash
./build_macos.sh
```

### Process

1. Installs PyInstaller via pip
2. Runs PyInstaller with flags:
   - `--name=Flipper` — output name
   - `--windowed` — no terminal window (`.app` bundle)
   - `--onefile` — single executable
   - `--clean` — clean build cache
   - `--optimize 2` — bytecode optimization (removes docstrings)
   - `--disable-windowed-traceback` — no traceback dialog on crash

### Output

- `dist/Flipper.app` — macOS application bundle
- `dist/Flipper` — standalone binary (inside the `.app`)

### Prerequisites

- Python 3.9+ with Tkinter support
- `brew install mpv` (for libmpv)
- `pip install pyinstaller`

---

## Windows Build

### Script: `build_windows.bat` (~258 lines)

```cmd
build_windows.bat
```

### Process (6 Stages)

**Stage 1 — Detect Python**
- Tries `python`, then `py -3`
- Verifies Tkinter is importable
- Detects Tcl/Tk data directories for bundling

**Stage 2 — Ensure libmpv DLL**
- Checks for existing `libmpv-2.dll`
- If missing, downloads mpv dev builds from GitHub (shinchiro/mpv-winbuild-cmake)
- Extraction fallback chain: `tar` → `7z` → `py7zr` → `7zr.exe`
- Validates DLL architecture matches Python (32/64 bit)

**Stage 3 — Configure PATH**
- Adds mpv DLL directory to current session PATH

**Stage 4 — Persist PATH**
- Permanently adds mpv directory to user PATH via PowerShell
- Ensures mpv is available for future runs

**Stage 5 — Build EXE**
- Installs PyInstaller if needed
- Runs PyInstaller with:
  - `--add-binary` for all mpv DLLs (`*.dll`)
  - `--add-data` for Tcl/Tk data directories
  - `--hidden-import=tkinter`
  - `--collect-submodules=tkinter`
  - `--onefile`, `--windowed`, `--clean`

**Stage 6 — Copy to Desktop**
- Copies `Flipper.exe` to user's Desktop

### Output

- `dist/Flipper.exe` — Windows executable (~30-50MB)

### Prerequisites

- Python 3.9+ with Tkinter
- Internet connection (for mpv DLL download on first build)

---

## PyInstaller Spec (`Flipper.spec`)

The spec file provides fine-grained control over the build:

- **Hidden imports:** `mpv`, `ctypes`
- **Excluded modules:** `matplotlib`, `numpy`, `PIL` (reduces binary size)
- **Binary includes:** mpv DLLs
- **Data includes:** Tcl/Tk runtime data

---

## Obfuscation Strategy

Documented in `BUILD_OBFUSCATION.md`. Current measures:

| Technique | Status | Effect |
|---|---|---|
| Bytecode optimization (level 2) | Active | Removes docstrings, type hints |
| Binary stripping | Active | Removes debug symbols |
| UPX compression | Active | Compresses binary, hinders static analysis |
| Disabled windowed traceback | Active | No error dialogs in production |
| Module exclusion | Active | Removes unnecessary imports |
| Cython compilation | Planned | Would compile `.py` to `.so`/`.pyd` |

---

## Auto-Update System (Windows Only)

### Flow

```
App Startup
    │
    ▼
GET version.txt from GitHub
    │
    ▼
Compare with local APP_VERSION
    │
    ├── Same version → skip
    │
    └── Newer version found
        │
        ▼
    GET changes.txt → show update dialog
        │
        ├── User declines → skip
        │
        └── User accepts
            │
            ▼
        GET zipball/{branch} → save to temp
            │
            ▼
        Extract ZIP to Desktop (hidden folder)
            │
            ▼
        Generate runner.bat:
          - Calls build_windows.bat
          - Copies new .exe to Desktop
          - Cleans up source and ZIP
            │
            ▼
        Launch runner.bat
            │
            ▼
        Exit current app
```

### GitHub API Authentication

- Uses Bearer token if a GitHub PAT is configured
- PAT is encrypted at rest (see [DATA_PERSISTENCE.md](DATA_PERSISTENCE.md))
- Works without a token for public repos (lower rate limits)

### Configuration

```python
DEFAULT_UPDATE_REPO = "FilipMichalkiewicz/flipper"
DEFAULT_UPDATE_BRANCH = "main"
```

---

## Cleanup Scripts

### macOS: `cleanup_build.sh`

Removes:
- `build/` directory
- `dist/` directory
- `__pycache__/` directories
- `.pyc` files
- Cython artifacts (`.c`, `.so` files from compilation)

### Windows: `cleanup_build.bat`

Same as macOS equivalent, adapted for Windows paths.

---

## Troubleshooting

### mpv DLL Not Found (Windows)

The build script auto-downloads libmpv, but if it fails:
1. Download mpv dev builds from https://github.com/shinchiro/mpv-winbuild-cmake/releases
2. Extract `libmpv-2.dll` and all dependency DLLs
3. Place them in the project root or `%APPDATA%\Flipper\mpv\`

### Tkinter Not Found

Some Python installations (especially from python.org) don't include Tkinter:
- **Windows:** Reinstall Python with "tcl/tk and IDLE" checkbox enabled
- **macOS:** `brew install python-tk`
- **Linux:** `sudo apt install python3-tk`

### Build Size Too Large

The `--onefile` flag bundles everything into a single executable. Excluded modules (`matplotlib`, `numpy`, `PIL`) already reduce size. Further reduction requires identifying unnecessary imports with `--debug imports`.
