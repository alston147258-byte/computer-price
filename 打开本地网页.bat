@echo off
cd /d %~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File scraper\open_local.ps1
echo.
pause
