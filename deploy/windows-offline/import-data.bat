@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0import-data.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" (
  echo Data import failed. Please keep this window for diagnosis.
) else (
  echo Data import completed.
)
pause
exit /b %EXIT_CODE%
