@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
if not "%errorlevel%"=="0" pause
