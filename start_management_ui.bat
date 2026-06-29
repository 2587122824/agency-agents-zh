@echo off
setlocal

set "ROOT=%~dp0"
set "HOST=127.0.0.1"
set "PORT=8765"
set "PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe"

cd /d "%ROOT%"

echo [agency-agents-zh] Starting management UI on http://%HOST%:%PORT% ...

for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  echo Stopping existing listener PID %%P ...
  taskkill /PID %%P /F >nul 2>nul
)

if not exist "%PYTHON%" (
  set "PYTHON=python"
)

start "agency-management-ui" /MIN "%PYTHON%" "%ROOT%my_workspace\web_app.py" --host %HOST% --port %PORT%

timeout /t 2 /nobreak >nul
start "" "http://%HOST%:%PORT%"

echo Management UI launched. If the browser does not open, visit:
echo http://%HOST%:%PORT%
endlocal
