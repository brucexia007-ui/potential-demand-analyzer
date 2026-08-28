@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\kanyikan.ps1" install
set "KANYIKAN_EXIT_CODE=%ERRORLEVEL%"
echo.
echo Kanyikan installer exit code: %KANYIKAN_EXIT_CODE%
pause
exit /b %KANYIKAN_EXIT_CODE%
