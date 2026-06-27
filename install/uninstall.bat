@echo off
REM DAITA - Windows uninstaller launcher (double-clickable, self-pausing).
REM Runs _uninstall.ps1 with the execution policy bypassed for THIS process only,
REM then pauses so the output stays on screen.
title DAITA-Uninstall
echo.
echo   Launching DAITA uninstaller...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_uninstall.ps1" %*
echo.
echo   ----------------------------------------------------------------
echo   Uninstaller finished. Review the output above.
echo   To finish, close this window and delete the DAITA folder.
echo.
pause
