@echo off
cd /d "%~dp0"
echo Lagonika monitor - one check (uses .env in this folder)
python lagonika_monitor.py %*
pause
