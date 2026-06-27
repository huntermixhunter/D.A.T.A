@echo off
REM DAITA launcher — uses the bundled embedded Python (no system Python needed).
REM Clears any stale instance on 7777/7766, opens the dashboard, starts the
REM supervisor windowless so no console stays open.
title DAITA-LaunchControl
cd /d "%~dp0dashboard"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7777 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7766 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
start "" http://localhost:7777
start "DAITA" "%~dp0runtime\python\pythonw.exe" supervisor.py
