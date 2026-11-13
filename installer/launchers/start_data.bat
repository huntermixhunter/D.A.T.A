@echo off
REM DATA launcher — uses the bundled embedded Python (no system Python needed).
REM Clears any stale instance on 7777/7766, starts the supervisor windowless so
REM no console stays open, then opens the dashboard in its own app window
REM (app_window.py waits for the bridge and falls back to the browser).
title DATA-LaunchControl
cd /d "%~dp0dashboard"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7777 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7766 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
start "DATA" "%~dp0runtime\python\pythonw.exe" supervisor.py
start "" "%~dp0runtime\python\pythonw.exe" app_window.py
