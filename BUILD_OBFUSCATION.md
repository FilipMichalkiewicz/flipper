# Flipper Build Obfuscation Guide

## Overview
The build process now includes multiple obfuscation layers to prevent code extraction and reverse engineering:

## Obfuscation Methods

### 1. **Bytecode Optimization** (`optimize=2`)
- PyInstaller compiles Python to optimized bytecode
- Removes docstrings and type hints
- Makes source reconstruction very difficult

### 2. **Binary Stripping** (`strip=True`)
- Removes all debug symbols
- Prevents traceback from leaking source code paths
- Reduces executable size

### 3. **UPX Compression**
- Compresses executable binary
- Makes static analysis harder
- Reduces distribution size

### 4. **No Traceback Information**
- `disable_windowed_traceback=True` prevents error dialogs from showing source paths
- Users won't see Python traceback on crash

### 5. **Excluded Unnecessary Modules**
- Removes matplotlib, numpy, PIL from bundle
- Reduces attack surface and binary size

### 6. **Cython Compilation** (Optional)
- `scanner.py` is compiled with Cython
- Can be further compiled to native code (`.pyd`/`.so`)
- Makes decompilation nearly impossible

## Build Security Notes

### ✅ What's Protected
- Core application logic in `main.py`
- Scanner module (`scanner.py` → compiled)
- Configuration and constants
- Network communication patterns

### ⚠️ Limitations
- GUI layout is still visible (tkinter)
- Resource files may be inspectable
- Determined attackers can still decompile, but it's much harder

## Building with Obfuscation

### Windows
```bash
build_windows.bat
```

### macOS
```bash
chmod +x build_macos.sh
./build_macos.sh
```

## Verify Obfuscation

After build, the executable should:
1. Not have readable `.py` files inside
2. Not have debug symbols (check with `objdump` on Linux/Mac)
3. Be compressed (smaller than expected)

### Check Windows EXE
```powershell
# Using 7-Zip or WinRAR: cannot extract readable Python source from .exe
```

### Check macOS/Linux Binary
```bash
objdump -t dist/Flipper | grep -i debug  # Should return empty
file dist/Flipper  # Shows if stripped
```

## Future Enhancements

1. **Full Cython Compilation**
   - Compile `main.py` and `scanner.py` to `.pyx`
   - Build native extensions (`*.pyd` on Windows, `*.so` on Linux)
   - Zero Python source code in binary

2. **Code Signing**
   - Sign executable to prevent tampering
   - Add certificate pinning for updates

3. **Anti-Tampering**
   - Runtime integrity checks
   - Detect modified bytecode

4. **Environment PIN**
   - Build unique binary for each user
   - Prevent redistribution if needed
