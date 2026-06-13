@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_local.ps1" %*
if errorlevel 1 (
  echo.
  echo start_local failed. Press any key to close.
  pause >nul
)
