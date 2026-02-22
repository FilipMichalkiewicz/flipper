@echo off
REM Build script for Windows .exe
REM Run this on a Windows machine with Python and PyInstaller installed

pip install pyinstaller
pyinstaller --name="Flipper" --windowed --onefile --clean main.py

echo.
echo Windows .exe created in dist\Flipper.exe
pause
