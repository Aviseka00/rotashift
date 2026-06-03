@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev-start.ps1"
if errorlevel 1 pause
exit /b %errorlevel%
