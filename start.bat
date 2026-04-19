@echo off
cd /d "%~dp0"
echo Starting RotaShift...
echo Open in browser: http://127.0.0.1:8000
echo On your phone (same WiFi): http://YOUR_PC_IP:8000  ^(see ipconfig^)
echo Invite codes load from ".env" in this folder ^(see .env.example^).
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
