@echo off
REM DATA — stops the dashboard (frees ports 7777 and 7766).
title DATA-Stop
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7777 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7766 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
echo DATA stopped.
timeout /t 2 >nul
