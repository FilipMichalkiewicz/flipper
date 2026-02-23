@echo off
REM ════════════════════════════════════════════════════════════
REM  Cleanup Build Artifacts
REM ════════════════════════════════════════════════════════════

setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

echo Cleaning up build artifacts...
echo.

REM Remove build directories
if exist "%SCRIPT_DIR%\build\" (
    echo Removing build\...
    rmdir /s /q "%SCRIPT_DIR%\build\"
)

if exist "%SCRIPT_DIR%\dist\" (
    echo Removing dist\...
    rmdir /s /q "%SCRIPT_DIR%\dist\"
)

REM Remove PyInstaller cache
if exist "%SCRIPT_DIR%\__pycache__\" (
    echo Removing __pycache__\...
    rmdir /s /q "%SCRIPT_DIR%\__pycache__\"
)

REM Remove Cython generated files
for %%F in (*.c *.cpp *.so *.pyd) do (
    if exist "%SCRIPT_DIR%\%%F" (
        echo Deleting %%F...
        del /f "%SCRIPT_DIR%\%%F"
    )
)

REM Remove Python cache
for /r "%SCRIPT_DIR%" %%D in (__pycache__) do (
    if exist "%%D" (
        echo Removing %%D...
        rmdir /s /q "%%D"
    )
)

echo.
echo ✅ Cleanup complete!
pause
