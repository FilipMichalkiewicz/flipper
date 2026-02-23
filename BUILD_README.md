# Flipper — MAC Address Scanner

A GUI application for scanning and validating MAC addresses on Stalker portal servers.

## 📦 Pre-built Application (macOS)

The application has been compiled for you:

**Location:** `dist/Flipper.app`

**To run:**
1. Navigate to the `dist/` folder
2. Double-click `Flipper.app`
3. If macOS shows a security warning, go to System Preferences → Security & Privacy and click "Open Anyway"

## 🚀 Running from Source

### Requirements
- Python 3.9+
- Dependencies: `pip install -r requirements.txt`

### Run
```bash
python3 main.py
```

## 🔨 Building from Source

### macOS Application
```bash
./build_macos.sh
```
Output: `dist/Flipper.app`

### Windows .exe
On a Windows machine with Python installed:
```batch
build_windows.bat
```
Output: `dist/Flipper.exe`

### Manual Build
```bash
# Install PyInstaller
pip install pyinstaller

# Build
pyinstaller --name="Flipper" --windowed --onefile --clean --optimize 2 --disable-windowed-traceback main.py
```

## 🔒 Security & Obfuscation

The build process includes **code obfuscation** to protect intellectual property:

- **Bytecode Optimization** (`optimize=2`) - Makes decompilation difficult
- **Binary Stripping** - Removes debug symbols and source paths
- **UPX Compression** - Compresses executable to prevent static analysis
- **Cython Compilation** - Key modules compiled to native code
- **No Traceback** - Error dialogs don't leak source code paths

See [BUILD_OBFUSCATION.md](BUILD_OBFUSCATION.md) for full details.

### Cleanup
To remove old build artifacts before rebuilding:

**Windows:**
```batch
cleanup_build.bat
```

**macOS/Linux:**
```bash
chmod +x cleanup_build.sh
./cleanup_build.sh
```

## 📖 Usage

1. **Enter Server URL** - The Stalker portal server address
2. **Set MAC Prefix** - First 3 bytes of MAC address (e.g., 00:1B:79)
3. **Configure Workers** - Number of parallel processes (default: 10)
4. **Set Timeout** - Request timeout in seconds (default: 5)
5. **Click START** - Begin scanning

### Features
- ⏸ **Pause/Resume** - Pause scanning at any time
- ⏹ **Stop** - Stop scanning
- 📋 **Copy MACs** - Copy found MAC addresses to clipboard
- 📁 **Export** - Export results to text file
- 💾 **Auto-save** - Automatically saves results to `results.txt`
- 🔄 **Session persistence** - Saves your settings between runs

## 📁 Files

- `main.py` - Main application (plain Tkinter version)
- `scanner.py` - MAC scanning logic
- `constants.py` - Configuration constants
- `session.json` - Saved session settings
- `results.txt` - Auto-saved results
- `dist/Flipper.app` - Built macOS application

## ⚠️ Warnings

The urllib3 SSL warning is normal on macOS and doesn't affect functionality:
```
NotOpenSSLWarning: urllib3 v2 only supports OpenSSL 1.1.1+
```

To suppress it, the app automatically sets environment variables.

## 🛠️ Troubleshooting

### macOS: "App can't be opened"
1. Right-click the app → Open
2. Or: System Preferences → Security & Privacy → "Open Anyway"

### Empty window on macOS
This has been fixed in the current version using plain Tkinter instead of CustomTkinter.

### PyInstaller not found
Add Python bin to PATH:
```bash
export PATH="$HOME/Library/Python/3.9/bin:$PATH"
```

## 📝 License

Free to use and modify.
