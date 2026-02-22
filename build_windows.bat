@echo off
REM Build script for Windows .exe
REM Run this on a Windows machine with Python installed

setlocal

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "MPV_DLL=%SCRIPT_DIR%\libmpv-2.dll"

echo [1/5] Installing dependencies...
python -m pip install --upgrade pip
python -m pip install --upgrade pyinstaller python-mpv

echo [2/5] Configuring PATH for current session...
set "PATH=%SCRIPT_DIR%;%PATH%"

echo [3/5] Persisting PATH (User) if needed...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
	"$p=[Environment]::GetEnvironmentVariable('Path','User');" ^
	"if (-not $p) { $p='' };" ^
	"$d='%SCRIPT_DIR%';" ^
	"if (($p -split ';') -notcontains $d) {" ^
	"  [Environment]::SetEnvironmentVariable('Path', ($p.TrimEnd(';') + ';' + $d).Trim(';'), 'User');" ^
	"  Write-Host 'Added to user PATH:' $d;" ^
	"} else { Write-Host 'Already in user PATH:' $d }"

echo [4/5] Building executable...
if exist "%MPV_DLL%" (
	pyinstaller --name "Flipper" --windowed --onefile --clean --add-binary "%MPV_DLL%;." main.py
) else (
	echo WARNING: libmpv-2.dll not found in project root: %MPV_DLL%
	echo Building without embedded mpv DLL.
	pyinstaller --name "Flipper" --windowed --onefile --clean main.py
)

echo [5/5] Done.
echo.
echo Windows .exe created in dist\Flipper.exe
pause
