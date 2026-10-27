@echo off
chcp 65001 >nul
rem УРМ — тестовый запуск. Двойной клик или: run_demo.bat
rem Политика выполнения PowerShell не затрагивается: -ExecutionPolicy Bypass
rem действует только на этот один запуск.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_demo.ps1"
echo.
pause
