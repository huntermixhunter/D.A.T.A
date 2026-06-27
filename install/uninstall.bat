@echo off
REM DATA - Windows uninstaller launcher (double-clickable, self-pausing).
REM Runs _uninstall.ps1 with the execution policy bypassed for THIS process only,
REM then pauses so the output stays on screen.
title DATA-Uninstall
echo.
echo   Launching DATA uninstaller...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_uninstall.ps1" %*
echo.
echo   ----------------------------------------------------------------
echo   Uninstaller finished. Review the output above.
echo   To finish, close this window and delete the DATA folder.
echo.
pause
