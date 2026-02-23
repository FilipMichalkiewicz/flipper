#!/bin/bash
# ════════════════════════════════════════════════════════════
# Cleanup Build Artifacts
# ════════════════════════════════════════════════════════════

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "Cleaning up build artifacts..."
echo ""

# Remove build directories
[ -d "$SCRIPT_DIR/build" ] && echo "Removing build/" && rm -rf "$SCRIPT_DIR/build"
[ -d "$SCRIPT_DIR/dist" ] && echo "Removing dist/" && rm -rf "$SCRIPT_DIR/dist"

# Remove PyInstaller spec build directory
[ -d "$SCRIPT_DIR/buildspec" ] && echo "Removing buildspec/" && rm -rf "$SCRIPT_DIR/buildspec"

# Remove Python cache
echo "Removing __pycache__ directories..."
find "$SCRIPT_DIR" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Remove Cython generated files
echo "Removing Cython artifacts..."
find "$SCRIPT_DIR" -maxdepth 1 \( -name "*.c" -o -name "*.cpp" \
    -o -name "*.so" -o -name "*.pyd" -o -name "*.o" -o -name "*.a" \) \
    -delete 2>/dev/null || true

# Remove .pyc files
echo "Removing .pyc files..."
find "$SCRIPT_DIR" -name "*.pyc" -delete 2>/dev/null || true

# Remove .pyo files
find "$SCRIPT_DIR" -name "*.pyo" -delete 2>/dev/null || true

echo ""
echo "✅ Cleanup complete!"
