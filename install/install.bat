@echo off
REM DATA - Windows installer launcher (double-clickable, self-pausing).
REM Runs _install.ps1 with the execution policy bypassed for THIS process only,
REM then pauses so any output - success or error - stays on screen.
title DATA-Install
echo.
echo   Launching DATA installer...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_install.ps1"
echo.
echo   ----------------------------------------------------------------
echo   Installer finished. Review the output above.
echo   If you saw an error, fix it and run this file again.
echo.
pause
