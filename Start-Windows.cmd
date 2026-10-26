@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-windows.ps1"
set "SOBES_EXIT=%ERRORLEVEL%"
if not "%SOBES_EXIT%"=="0" pause
exit /b %SOBES_EXIT%
