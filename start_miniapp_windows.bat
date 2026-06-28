@echo off
cd /d %~dp0
chcp 65001 >nul

echo Starting Yellow Club Mini App...

set PYTHON_EXE=.venv\Scripts\python.exe
if not exist "%PYTHON_EXE%" (
    set PYTHON_EXE=python
)

echo Using Python: %PYTHON_EXE%

%PYTHON_EXE% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Failed to install requirements. Check Python / venv / internet connection.
    pause
    exit /b 1
)

echo.
echo Running Mini App server...
%PYTHON_EXE% web_app_server.py
pause
