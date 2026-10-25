@echo off
cd /d "%~dp0demo"
start "" /min powershell -NoProfile -Command "Start-Sleep -Seconds 2; Start-Process 'http://localhost:8000'"
python server.py 8000
pause
