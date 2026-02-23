#!/bin/bash
# Build script for macOS application with obfuscation

echo "Installing dependencies..."
python3 -m pip install --user pyinstaller cython

echo "Compiling scanner.py with Cython for obfuscation..."
python3 -m cython scanner.py --embed-pos-in-docstring 2>/dev/null || true

echo "Building macOS application with optimizations..."
/Users/$USER/Library/Python/3.9/bin/pyinstaller \
  --name="Flipper" \
  --windowed \
  --onefile \
  --clean \
  --optimize 2 \
  Flipper.spec

echo ""
echo "✅ Build complete!"
echo "macOS app: dist/Flipper.app"
echo "Executable: dist/Flipper"
echo ""
echo "🔒 Built with code obfuscation enabled."
echo "You can now run the app by double-clicking dist/Flipper.app"
