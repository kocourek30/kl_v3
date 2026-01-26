@echo off
title Kliknijidlo - Spouštění výdejního systému
color 0A
echo.
echo ============================================
echo   KLIKNIJIDLO - VÝDEJNÍ SYSTÉM
echo ============================================
echo.

set PROJECT_DIR=%~dp0
cd /d "%PROJECT_DIR%"

echo [1/5] Aktivuji virtuální prostředí...
call venv\Scripts\activate.bat

echo [2/5] Spouštím Django server...
start /min cmd /k "title Django Server && color 0B && cd /d "%PROJECT_DIR%" && venv\Scripts\activate.bat && python manage.py runserver 127.0.0.1:8000"
timeout /t 3 /nobreak >nul

echo [3/5] Spouštím RFID Bridge...
start /min cmd /k "title RFID Bridge && color 0E && cd /d "%PROJECT_DIR%" && node rfid_bridge.js"
timeout /t 2 /nobreak >nul

echo [4/5] Čekám na spuštění serverů...
timeout /t 4 /nobreak >nul

echo [5/5] Otevírám prohlížeč v fullscreen...
REM ⭐ ZMĚNĚNÁ URL - AUTO-LOGIN
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --kiosk --app=http://10.0.0.108:8000/vydej/kiosk-login/

echo.
echo ============================================
echo   ✅ SYSTÉM ÚSPĚŠNĚ SPUŠTĚN!
echo ============================================
echo.
echo Django běží na: http://10.0.0.108:8000
echo RFID Bridge:    http://localhost:3001
echo.
pause >nul
