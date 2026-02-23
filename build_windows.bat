@echo off
REM ══════════════════════════════════════════════════════════
REM  Flipper — Windows Build Script
REM  Uses flat goto/label pattern (no nested IF blocks).
REM ══════════════════════════════════════════════════════════

setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "FLIPPER_DATA_DIR=%LOCALAPPDATA%\Flipper"
set "MPV_EXTRACT_DIR=%FLIPPER_DATA_DIR%\mpv"
set "MPV_DLL=%MPV_EXTRACT_DIR%\libmpv-2.dll"
set "DIST_EXE=%SCRIPT_DIR%\dist\Flipper.exe"
set "DESKTOP_DIR="
set "PY="
set "MPV_ARCH=x86_64"

REM ── Detect Python ──────────────────────────────────────
where python >nul 2>nul
if not errorlevel 1 (
    set "PY=python"
    goto :py_ok
)
where py >nul 2>nul
if not errorlevel 1 (
    set "PY=py -3"
    goto :py_ok
)
echo ERROR: Python not found in PATH.
goto :fail

:py_ok
echo Using: %PY%

REM ── Detect Python architecture (32/64 bit) ─────────────
for /f "usebackq delims=" %%A in (`cmd /c %PY% -c "import struct; print(struct.calcsize('P')*8)"`) do set "PY_BITS=%%A"
if "%PY_BITS%"=="32" set "MPV_ARCH=i686"
if "%PY_BITS%"=="64" set "MPV_ARCH=x86_64"
echo Python is %PY_BITS%-bit, using mpv arch: %MPV_ARCH%

set "MPV_URL=https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20260222/mpv-dev-%MPV_ARCH%-20260222-git-250d605.7z"
set "MPV_ARCHIVE=%SCRIPT_DIR%\mpv-dev-%MPV_ARCH%-20260222-git-250d605.7z"

if not exist "%FLIPPER_DATA_DIR%" mkdir "%FLIPPER_DATA_DIR%"
if not exist "%MPV_EXTRACT_DIR%" mkdir "%MPV_EXTRACT_DIR%"

REM ── Step 1: Dependencies ───────────────────────────────
echo.
echo [1/6] Installing dependencies...
cmd /c %PY% -m pip install -r "%SCRIPT_DIR%\requirements.txt"
echo     requirements.txt done (rc=%errorlevel%)
cmd /c %PY% -m pip install pyinstaller py7zr
echo     pyinstaller+py7zr done (rc=%errorlevel%)
echo [1/6] Finished.

REM ── Step 2: Ensure libmpv DLL ──────────────────────────
echo.
echo [2/6] Ensuring libmpv-2.dll...

REM Delete old v3 DLL if present (wrong arch causes "not a valid Win32 application")
if exist "%MPV_DLL%" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Add-Type -MemberDefinition '[DllImport(\"kernel32.dll\",SetLastError=true)]public static extern IntPtr LoadLibraryEx(string lpFileName,IntPtr hFile,uint dwFlags);[DllImport(\"kernel32.dll\")]public static extern bool FreeLibrary(IntPtr hModule);' -Name W -Namespace K; $h=[K.W]::LoadLibraryEx('%MPV_DLL%',[IntPtr]::Zero,1); if($h -eq [IntPtr]::Zero){Write-Host 'BAD';exit 1}else{[K.W]::FreeLibrary($h);Write-Host 'OK';exit 0} } catch {Write-Host 'BAD';exit 1}"
    if errorlevel 1 (
        echo     Existing DLL is invalid/incompatible. Removing...
        del /f "%MPV_DLL%" >nul 2>nul
    ) else (
        echo     Existing DLL is valid.
        goto :mpv_ready
    )
)

if exist "%MPV_DLL%" goto :mpv_ready

echo     DLL not found. Downloading...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri '%MPV_URL%' -OutFile '%MPV_ARCHIVE%'"
if not exist "%MPV_ARCHIVE%" (
    echo     WARNING: Download failed.
    goto :mpv_skip
)

echo     Extracting archive...
call :extract_mpv
if not exist "%MPV_DLL%" (
    echo     Searching recursively...
    for /f "delims=" %%I in ('dir /s /b "%MPV_EXTRACT_DIR%\libmpv-2.dll" 2^>nul') do set "MPV_DLL=%%I"
)

:mpv_ready
if exist "%MPV_DLL%" echo     Found: %MPV_DLL%
if not exist "%MPV_DLL%" echo     WARNING: libmpv-2.dll not found.

:mpv_skip

REM ── Step 3: Configure PATH ─────────────────────────────
echo.
echo [3/6] Configuring PATH...
set "MPV_DLL_DIR=%MPV_EXTRACT_DIR%"
if exist "%MPV_DLL%" for %%D in ("%MPV_DLL%") do set "MPV_DLL_DIR=%%~dpD"
if "%MPV_DLL_DIR:~-1%"=="\" set "MPV_DLL_DIR=%MPV_DLL_DIR:~0,-1%"
set "PATH=%MPV_DLL_DIR%;%PATH%"
echo     PATH updated: %MPV_DLL_DIR%

REM ── Step 4: Persist user PATH ──────────────────────────
echo.
echo [4/6] Persisting user PATH...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=[Environment]::GetEnvironmentVariable('Path','User'); if(-not $p){$p=''}; $d='%MPV_DLL_DIR%'; if(($p -split ';') -notcontains $d){[Environment]::SetEnvironmentVariable('Path',($p.TrimEnd(';')+';'+$d).Trim(';'),'User'); Write-Host 'Added:' $d}else{Write-Host 'Already present:' $d}"

REM ── Step 5: Build EXE ──────────────────────────────────
echo.
echo [5/6] Building executable...
cmd /c %PY% -m PyInstaller --name "Flipper" --windowed --onefile --clean main.py
if not exist "%DIST_EXE%" goto :fail
echo     OK: %DIST_EXE%

REM ── Step 6: Copy to Desktop ────────────────────────────
echo.
echo [6/6] Copying to Desktop...
for /f "usebackq delims=" %%D in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESKTOP_DIR=%%D"
if not defined DESKTOP_DIR set "DESKTOP_DIR=%USERPROFILE%\Desktop"
if not exist "%DESKTOP_DIR%" goto :done
copy /Y "%DIST_EXE%" "%DESKTOP_DIR%\Flipper.exe" >nul
if not errorlevel 1 echo     Copied: %DESKTOP_DIR%\Flipper.exe

:done
echo.
echo ════════════════════════════════════════
echo   BUILD COMPLETE: dist\Flipper.exe
echo ════════════════════════════════════════
pause
goto :eof

REM ══════════════════════════════════════════════════════════
REM  SUBROUTINE: extract_mpv
REM ══════════════════════════════════════════════════════════
:extract_mpv
where tar >nul 2>nul
if errorlevel 1 goto :try_7z
tar -xf "%MPV_ARCHIVE%" -C "%MPV_EXTRACT_DIR%" >nul 2>nul
if not errorlevel 1 (
    echo     Extracted with tar.
    exit /b 0
)

:try_7z
where 7z >nul 2>nul
if errorlevel 1 goto :try_py7zr
7z x -y -o"%MPV_EXTRACT_DIR%" "%MPV_ARCHIVE%" >nul
if not errorlevel 1 (
    echo     Extracted with 7z.
    exit /b 0
)

:try_py7zr
echo     Trying py7zr...
REM Use -I flag to isolate Python from local imports and avoid loading libmpv-2.dll
cmd /c %PY% -I -c "import py7zr,sys; a=py7zr.SevenZipFile(sys.argv[1],'r'); a.extractall(sys.argv[2]); a.close()" "%MPV_ARCHIVE%" "%MPV_EXTRACT_DIR%"
if not errorlevel 1 (
    echo     Extracted with py7zr.
    exit /b 0
)
echo     WARNING: All extraction methods failed.
exit /b 1

:fail
echo.
echo ════════════════════════════════════════
echo   ERROR: Build failed!
echo ════════════════════════════════════════
pause
exit /b 1
