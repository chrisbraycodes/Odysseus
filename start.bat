@echo off
setlocal
title Start Odysseus

pushd "%~dp0" >nul

echo =========================================
echo Starting Odysseus (Docker)
echo =========================================
echo.

where docker >nul 2>nul
if errorlevel 1 (
  echo ERROR: Docker is not installed or not on PATH.
  echo Install Docker Desktop for Windows, then run this script again.
  echo.
  pause
  exit /b 1
)

docker info >nul 2>nul
if errorlevel 1 (
  echo ERROR: Docker Desktop does not appear to be running.
  echo Start Docker Desktop, wait until it is ready, then run this script again.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-docker.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if %EXIT_CODE% neq 0 (
  echo.
  echo Start failed. Check the message above and try again.
  pause
)

popd >nul
exit /b %EXIT_CODE%
