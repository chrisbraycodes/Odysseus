@echo off
setlocal
title Start Odysseus

pushd "%~dp0" >nul

echo =========================================
echo Starting Odysseus
echo =========================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-windows.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if %EXIT_CODE% neq 0 (
  echo.
  echo Start failed. Check the message above and try again.
  pause
)

popd >nul
exit /b %EXIT_CODE%
