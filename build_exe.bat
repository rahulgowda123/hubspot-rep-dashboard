@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  Building MBR_Dashboard.exe  (standalone desktop app)
echo ============================================================

REM Prerequisites — pywebview gives us the native window
python -m pip install --quiet --disable-pip-version-check ^
  pyinstaller flask requests pywebview
if errorlevel 1 (
  echo [ERROR] pip install failed.
  pause
  exit /b 1
)

REM Clean previous builds so the .exe is always fresh
if exist build      rd /s /q build
if exist dist       rd /s /q dist
if exist MBR_Dashboard.spec del /q MBR_Dashboard.spec

REM --windowed = no console window (matches RepReportGUI behavior)
REM --collect-all webview = bundle pywebview backends (EdgeChromium etc.)
python -m PyInstaller ^
  --onefile ^
  --windowed ^
  --name MBR_Dashboard ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --hidden-import requests ^
  --hidden-import flask ^
  --hidden-import jinja2 ^
  --hidden-import webview ^
  --collect-submodules flask ^
  --collect-submodules werkzeug ^
  --collect-submodules jinja2 ^
  --collect-all webview ^
  --collect-all proxy_tools ^
  launcher.py

if errorlevel 1 (
  echo [ERROR] PyInstaller build failed.
  pause
  exit /b 1
)

REM Assemble the redistributable folder
if exist Release rd /s /q Release
mkdir Release
copy /Y dist\MBR_Dashboard.exe Release\MBR_Dashboard.exe >nul
copy /Y .env Release\.env >nul
copy /Y README.txt Release\README.txt >nul 2>nul

echo.
echo ============================================================
echo  Build complete. Distribute everything in the "Release\" folder:
echo    - MBR_Dashboard.exe   (standalone desktop app, no browser needed)
echo    - .env                (HubSpot token + optional MBR_PORT)
echo    - README.txt
echo ============================================================
pause
