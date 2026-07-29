@echo off
chcp 65001 >nul 2>&1
REM ============================================
REM  NetMountX - Build Script
REM  Usage: double-click or run from project root
REM  Requires: pip install pyinstaller PyQt6-Fluent-Widgets
REM ============================================

echo.
echo [1/3] Cleaning old build artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist NetMountX.spec del /q NetMountX.spec

echo [2/3] Building single-file exe with PyInstaller...
pyinstaller --noconfirm --clean ^
    --onefile --windowed ^
    --name NetMountX ^
    --icon NetMountX\netmountx.ico ^
    --add-data "NetMountX\netmountx.ico;." ^
    --collect-data qfluentwidgets ^
    --hidden-import NetMountX ^
    --hidden-import NetMountX.__main__ ^
    --hidden-import NetMountX.constants ^
    --hidden-import NetMountX.core ^
    --hidden-import NetMountX.config ^
    --hidden-import NetMountX.monitor ^
    --hidden-import NetMountX.autostart ^
    --hidden-import NetMountX.gui ^
    --hidden-import NetMountX.selftest ^
    netmountx.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [FAIL] Build failed! Check the error messages above.
    pause
    exit /b 1
)

echo [3/3] Build complete!
echo.
echo Output: dist\NetMountX.exe
echo.
explorer dist
pause
