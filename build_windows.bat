@echo off
REM Build script for Windows .exe
REM Run this on a Windows machine with Python installed

setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "FLIPPER_DATA_DIR=%LOCALAPPDATA%\Flipper"
set "MPV_EXTRACT_DIR=%FLIPPER_DATA_DIR%\mpv"
set "MPV_URL=https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20260222/mpv-dev-x86_64-v3-20260222-git-250d605.7z"
set "MPV_ARCHIVE=%SCRIPT_DIR%\mpv-dev-x86_64-v3-20260222-git-250d605.7z"
set "MPV_DLL=%MPV_EXTRACT_DIR%\libmpv-2.dll"
set "DIST_EXE=%SCRIPT_DIR%\dist\Flipper.exe"
set "DESKTOP_DIR="
set "PYTHON_EXE=python"
set "PYTHON_ARGS="

where python >nul 2>nul
if errorlevel 1 (
	where py >nul 2>nul
 	if not errorlevel 1 (
		set "PYTHON_EXE=py"
		set "PYTHON_ARGS=-3"
	)
)

where %PYTHON_EXE% >nul 2>nul
if errorlevel 1 (
	echo ERROR: Python not found in PATH.
	goto :fail
)

if not exist "%FLIPPER_DATA_DIR%" mkdir "%FLIPPER_DATA_DIR%"
if not exist "%MPV_EXTRACT_DIR%" mkdir "%MPV_EXTRACT_DIR%"

echo [1/6] Installing dependencies...
call %PYTHON_EXE% %PYTHON_ARGS% -m pip install -r "%SCRIPT_DIR%\requirements.txt" --quiet 2>nul
call %PYTHON_EXE% %PYTHON_ARGS% -m pip install pyinstaller py7zr --quiet 2>nul
echo Dependencies installed (or already present).

echo [2/6] Ensuring libmpv-2.dll (download + extract if missing)...
if not exist "%MPV_DLL%" (
	echo libmpv-2.dll not found in %MPV_EXTRACT_DIR%. Downloading winbuild package...
	powershell -NoProfile -ExecutionPolicy Bypass -Command ^
		"Invoke-WebRequest -Uri '%MPV_URL%' -OutFile '%MPV_ARCHIVE%'"
	if errorlevel 1 (
		echo WARNING: Download failed: %MPV_URL%
	)

	if exist "%MPV_ARCHIVE%" (
		echo Extracting archive (fast path: tar/7z, fallback: py7zr)...
		call :extract_mpv "%MPV_ARCHIVE%" "%MPV_EXTRACT_DIR%"

		for /f "delims=" %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path ''%MPV_EXTRACT_DIR%'' -Filter ''libmpv-2.dll'' -Recurse -File | Select-Object -First 1 -ExpandProperty FullName"') do set "MPV_DLL=%%I"
	)
)

if not exist "%MPV_DLL%" (
	echo WARNING: Could not find libmpv-2.dll after download/extract.
	echo Build will continue without embedded mpv DLL.
)

echo [3/6] Configuring PATH for current session...
set "MPV_DLL_DIR=%MPV_EXTRACT_DIR%"
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
if not exist "%MPV_DLL%" (
	echo WARNING: libmpv-2.dll not found in runtime dir: %MPV_DLL%
	echo App may need to download/extract mpv on first start.
)

REM Intentionally do not embed libmpv in --onefile to avoid using Temp\_MEI path.
call %PYTHON_EXE% %PYTHON_ARGS% -m PyInstaller --name "Flipper" --windowed --onefile --clean main.py
if errorlevel 1 goto :fail

echo [6/6] Copying Flipper.exe to Desktop...
for /f "usebackq delims=" %%D in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESKTOP_DIR=%%D"
if not defined DESKTOP_DIR set "DESKTOP_DIR=%USERPROFILE%\Desktop"

if exist "%DIST_EXE%" (
	copy /Y "%DIST_EXE%" "%DESKTOP_DIR%\Flipper.exe" >nul
	if errorlevel 1 (
		echo WARNING: Could not copy Flipper.exe to Desktop: %DESKTOP_DIR%
	) else (
		echo Copied: %DESKTOP_DIR%\Flipper.exe
	)
) else (
	echo WARNING: Build output not found: %DIST_EXE%
)

echo Done.
echo.
echo Windows .exe created in dist\Flipper.exe
pause
goto :eof

:extract_mpv
set "EXTRACTED_OK="
set "ARCHIVE=%~1"
set "OUTDIR=%~2"

where tar >nul 2>nul
if not errorlevel 1 (
	tar -xf "%ARCHIVE%" -C "%OUTDIR%" >nul 2>nul
	if not errorlevel 1 set "EXTRACTED_OK=1"
)

if not defined EXTRACTED_OK (
	where 7z >nul 2>nul
	if not errorlevel 1 (
		7z x -y -o"%OUTDIR%" "%ARCHIVE%" >nul
		if not errorlevel 1 set "EXTRACTED_OK=1"
	)
)

if not defined EXTRACTED_OK (
	call %PYTHON_EXE% %PYTHON_ARGS% -c "import py7zr,sys; z=py7zr.SevenZipFile(sys.argv[1], mode='r'); z.extractall(path=sys.argv[2]); z.close()" "%ARCHIVE%" "%OUTDIR%"
	if not errorlevel 1 set "EXTRACTED_OK=1"
)

if not defined EXTRACTED_OK (
	echo WARNING: Failed to extract MPV archive.
)
exit /b 0

:fail
echo.
echo ERROR: Build failed. Check messages above.
pause
exit /b 1
