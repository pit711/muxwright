@echo off
rem DAB-Lab PC-Server starten. Weitere Optionen werden durchgereicht, z. B.:
rem   start_dablab.cmd --lan
rem   start_dablab.cmd --backend ssh          (Runner auf dem Mac statt WSL)
rem   start_dablab.cmd --min-atten 60         (Daempfung nie unter 60 dB)
rem   start_dablab.cmd --source testgen       (Ersatzgenerator erzwingen)
chcp 65001 >nul
title DAB-Lab Server
cd /d "%~dp0"
set "PY=%USERPROFILE%\miniconda3\python.exe"
if not exist "%PY%" set "PY=python"
start "" /b cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8792/"
"%PY%" -u server.py %*
echo.
echo Server beendet (Pluto wurde stummgeschaltet).
pause
