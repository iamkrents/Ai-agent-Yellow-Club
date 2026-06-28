@echo off
cd /d "%~dp0"
echo Removing .venv...
rmdir /s /q .venv 2>nul
echo Done. Now run start_windows.bat
pause
