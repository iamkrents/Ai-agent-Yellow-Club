@echo off
setlocal
cd /d "%~dp0"

echo Starting Yellow Club Agent v3...

if not exist ".env" (
  echo [ERROR] .env not found. Copy .env.example to .env and fill TELEGRAM_BOT_TOKEN.
  pause
  exit /b 1
)

set PY_CMD=
where py >nul 2>nul
if %errorlevel%==0 (
  py -3.12 --version >nul 2>nul
  if %errorlevel%==0 set PY_CMD=py -3.12
)
if "%PY_CMD%"=="" (
  where python >nul 2>nul
  if %errorlevel%==0 set PY_CMD=python
)
if "%PY_CMD%"=="" (
  echo [ERROR] Python not found. Install Python 3.12 and enable Add Python to PATH.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY_CMD% -m venv .venv
  if %errorlevel% neq 0 (
    echo [ERROR] Could not create virtual environment.
    pause
    exit /b 1
  )
)

echo Installing dependencies...
.venv\Scripts\python.exe -m pip install --upgrade pip >nul
.venv\Scripts\python.exe -m pip install -r requirements.txt
if %errorlevel% neq 0 (
  echo [ERROR] Dependencies installation failed.
  pause
  exit /b 1
)

echo Running bot...
.venv\Scripts\python.exe bot.py
pause
