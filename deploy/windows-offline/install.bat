@echo off
setlocal

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set "EXIT_CODE=%errorlevel%"
echo.
if "%EXIT_CODE%"=="0" (
  echo Installation or upgrade completed.
) else (
  echo Installation failed. Error code: %EXIT_CODE%
  echo Please keep this window and install.log for diagnosis.
)
echo.
pause
exit /b %EXIT_CODE%
