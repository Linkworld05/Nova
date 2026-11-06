@echo off
setlocal
cd /d "%~dp0"
py -3.13 -c "import sys;sys.exit(sys.version_info[:2] != (3,13))" >nul 2>nul
if errorlevel 1 (
  echo Install Python 3.13 x64 from https://www.python.org/downloads/
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" py -3.13 -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements-windows-py313.lock.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip check
if errorlevel 1 exit /b 1
echo Core setup complete. Read README.md for Ollama and optional speech models.
endlocal
