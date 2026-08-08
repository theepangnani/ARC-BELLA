@echo off
title ARC tunnel - keep this window OPEN
chcp 65001 >nul
echo.
echo   Starting a public HTTPS tunnel to your local ARC (port 8420).
echo   A QR code will appear below in a moment - scan it with your phone,
echo   log in, then Add to Home Screen and allow the microphone.
echo.
echo   Keep this window open while you use ARC on your phone.
echo   Close it (or press Ctrl+C) to stop exposing ARC to the internet.
echo.
echo   ---------------------------------------------------------------------
echo.
python "%~dp0tunnel_qr.py"
echo.
pause
