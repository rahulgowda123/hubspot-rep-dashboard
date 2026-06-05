@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python is not installed or not on PATH.
  echo Install Python 3.9+ from https://www.python.org/downloads/ and re-run.
  pause
  exit /b 1
)

echo Installing dependencies...
python -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Failed to install dependencies.
  pause
  exit /b 1
)

echo.
echo Starting MBR Dashboard...
echo Open http://localhost:5000 in your browser.
echo Press Ctrl+C in this window to stop the server.
echo.
python app.py
pause
