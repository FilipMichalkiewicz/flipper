#!/bin/bash
# Build script for macOS application

echo "Installing PyInstaller..."
python3 -m pip install --user pyinstaller

echo "Building macOS application..."
/Users/$USER/Library/Python/3.9/bin/pyinstaller \
  --name="Flipper" \
  --windowed \
  --onefile \
  --clean \
  main.py

echo ""
echo "✅ Build complete!"
echo "macOS app: dist/Flipper.app"
echo "Executable: dist/Flipper"
echo ""
echo "You can now run the app by double-clicking dist/Flipper.app"
