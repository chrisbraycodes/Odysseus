@echo off
setlocal
title Odysseus Windows Host Agent
pushd "%~dp0" >nul

set "NO_PAUSE="
if /i "%~1"=="-NoPause" set "NO_PAUSE=1"
if /i "%~2"=="-NoPause" set "NO_PAUSE=1"

for /f "usebackq tokens=1,* delims==" %%A in (`findstr /r /b "WORKSPACE_HOST_AGENT_TOKEN=" ".env" 2^>nul`) do set "WORKSPACE_HOST_AGENT_TOKEN=%%B"
if not defined WORKSPACE_HOST_AGENT_TOKEN (
  echo ERROR: WORKSPACE_HOST_AGENT_TOKEN missing from .env
  echo Run start.bat once to generate it.
  if not defined NO_PAUSE pause
  exit /b 1
)

if not exist ".host-agent-venv\Scripts\python.exe" (
  echo Creating host agent virtualenv...
  python -m venv .host-agent-venv
  if errorlevel 1 (
    echo ERROR: Could not create .host-agent-venv - install Python 3.
    if not defined NO_PAUSE pause
    exit /b 1
  )
  ".host-agent-venv\Scripts\python.exe" -m pip install -q -r scripts\host_agent_requirements.txt
)

powershell -NoProfile -Command ^
  "$t=$env:WORKSPACE_HOST_AGENT_TOKEN; try { $r=Invoke-WebRequest -Uri 'http://127.0.0.1:17789/health' -Headers @{Authorization=\"Bearer $t\"} -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1"
if not errorlevel 1 (
  echo Windows host agent is already running on 127.0.0.1:17789
  popd >nul
  exit /b 0
)

if not exist logs mkdir logs
echo Starting Windows host agent...
rem Detached launch: start /B shares the caller console and blocks "cmd /c ... -Wait" in launch-docker.ps1
powershell -NoProfile -Command ^
  "Start-Process -WindowStyle Hidden -WorkingDirectory (Get-Location) -FilePath '.host-agent-venv\Scripts\pythonw.exe' -ArgumentList @('scripts\windows_host_agent.py','--token','%WORKSPACE_HOST_AGENT_TOKEN%','--port','17789','--log-file','logs\host-agent.log') | Out-Null"

ping -n 4 127.0.0.1 >nul
powershell -NoProfile -Command ^
  "$t=$env:WORKSPACE_HOST_AGENT_TOKEN; try { $r=Invoke-WebRequest -Uri 'http://127.0.0.1:17789/health' -Headers @{Authorization=\"Bearer $t\"} -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { Write-Host 'Windows host agent is ready.' -ForegroundColor Green; exit 0 } } catch { Write-Host $_.Exception.Message -ForegroundColor Red }; exit 1"
set "EXIT_CODE=%ERRORLEVEL%"
if %EXIT_CODE% neq 0 (
  echo Check logs\host-agent.log for details.
  if not defined NO_PAUSE pause
)
popd >nul
exit /b %EXIT_CODE%
