@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0apply-lab-config.ps1"
set "EXIT_CODE=%errorlevel%"
echo.
if "%EXIT_CODE%"=="0" (
  echo Lab device configuration update completed.
) else (
  echo Configuration update failed. Error code: %EXIT_CODE%
)
echo.
pause
exit /b %EXIT_CODE%
