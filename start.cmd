@echo off
setlocal
rem Checkpoint launcher. Run setup.ps1 once first.
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "HF_HUB_DISABLE_TELEMETRY=1"
set "HF_HUB_DISABLE_PROGRESS_BARS=1"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo  Checkpoint isn't set up yet.
  echo  Run this once in PowerShell from this folder:
  echo      powershell -ExecutionPolicy Bypass -File setup.ps1
  echo.
  pause
  exit /b 1
)

echo Checking prerequisites...
pushd backend
"..\.venv\Scripts\python.exe" -m checkpoint.preflight
if errorlevel 1 (
  popd
  echo.
  echo  Checkpoint can't start. Fix the errors above, then run start.cmd again.
  pause
  exit /b 1
)

if /i "%~1"=="--console" (
  echo Running attached to this window. Close it or use the tray icon ^> Quit to stop.
  "..\.venv\Scripts\python.exe" -m checkpoint
  set "RC=%ERRORLEVEL%"
  popd
  if not "%RC%"=="0" (
    echo.
    echo  Checkpoint exited with an error ^(code %RC%^). See the log in %%LOCALAPPDATA%%\Checkpoint\logs.
    pause
  )
  exit /b %RC%
)

rem Normal use: run without a console window. The tray icon shows the real state.
start "" "..\.venv\Scripts\pythonw.exe" -m checkpoint
popd
echo Checkpoint is starting - look for its icon in the Windows notification area.
echo (Logs: %LOCALAPPDATA%\Checkpoint\logs)
timeout /t 3 >nul
exit /b 0
