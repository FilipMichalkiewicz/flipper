@echo off
REM Build script for Windows .exe
REM Run this on a Windows machine with Python installed

setlocal

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "MPV_URL=https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20260222/mpv-dev-x86_64-v3-20260222-git-250d605.7z"
set "MPV_ARCHIVE=%SCRIPT_DIR%\mpv-dev-x86_64-v3-20260222-git-250d605.7z"
set "MPV_EXTRACT_DIR=%SCRIPT_DIR%\mpv_runtime"
set "MPV_DLL=%SCRIPT_DIR%\libmpv-2.dll"

echo [1/6] Installing dependencies...
python -m pip install --upgrade pip
python -m pip install --upgrade pyinstaller python-mpv py7zr

echo [2/6] Ensuring libmpv-2.dll (download + extract if missing)...
if not exist "%MPV_DLL%" (
	echo libmpv-2.dll not found in project root. Downloading winbuild package...
	powershell -NoProfile -ExecutionPolicy Bypass -Command ^
		"Invoke-WebRequest -Uri '%MPV_URL%' -OutFile '%MPV_ARCHIVE%'"

	if exist "%MPV_ARCHIVE%" (
		if not exist "%MPV_EXTRACT_DIR%" mkdir "%MPV_EXTRACT_DIR%"
		python -c "import py7zr; py7zr.SevenZipFile(r'%MPV_ARCHIVE%', mode='r').extractall(path=r'%MPV_EXTRACT_DIR%'); print('Extracted')"

		for /f "delims=" %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path ''%MPV_EXTRACT_DIR%'' -Filter ''libmpv-2.dll'' -Recurse -File | Select-Object -First 1 -ExpandProperty FullName"') do set "MPV_DLL=%%I"
	)
)

if not exist "%MPV_DLL%" (
	echo WARNING: Could not find libmpv-2.dll after download/extract.
	echo Build will continue without embedded mpv DLL.
)

echo [3/6] Configuring PATH for current session...
set "MPV_DLL_DIR=%SCRIPT_DIR%"
if exist "%MPV_DLL%" (
	for %%D in ("%MPV_DLL%") do set "MPV_DLL_DIR=%%~dpD"
)
if "%MPV_DLL_DIR:~-1%"=="\" set "MPV_DLL_DIR=%MPV_DLL_DIR:~0,-1%"
set "PATH=%MPV_DLL_DIR%;%PATH%"

echo [4/6] Persisting PATH (User) if needed...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
	"$p=[Environment]::GetEnvironmentVariable('Path','User');" ^
	"if (-not $p) { $p='' };" ^
	"$d='%MPV_DLL_DIR%';" ^
	"if (($p -split ';') -notcontains $d) {" ^
	"  [Environment]::SetEnvironmentVariable('Path', ($p.TrimEnd(';') + ';' + $d).Trim(';'), 'User');" ^
	"  Write-Host 'Added to user PATH:' $d;" ^
	"} else { Write-Host 'Already in user PATH:' $d }"

echo [5/6] Building executable...
if exist "%MPV_DLL%" (
	pyinstaller --name "Flipper" --windowed --onefile --clean --add-binary "%MPV_DLL%;." main.py
) else (
	echo WARNING: libmpv-2.dll not found in project root: %MPV_DLL%
	echo Building without embedded mpv DLL.
	pyinstaller --name "Flipper" --windowed --onefile --clean main.py
)

echo [6/6] Done.
echo.
echo Windows .exe created in dist\Flipper.exe
pause
