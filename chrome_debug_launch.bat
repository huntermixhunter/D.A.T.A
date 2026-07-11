@echo off
REM ============================================================================
REM  chrome_debug_launch.bat
REM ----------------------------------------------------------------------------
REM  Launch your real Chrome with the DevTools Protocol exposed on port 9222 so
REM  DATA's chrome-cdp skill can attach via Playwright CDP.
REM
REM  Uses an ISOLATED user-data-dir so this instance does not conflict with your
REM  daily Chrome. Log in ONCE to each site you want automated (Instagram web,
REM  YouTube Studio, Vercel, etc.) inside this debug Chrome, then the automation
REM  reuses those sessions on every subsequent run.
REM
REM  Idempotent: if port 9222 is already listening, this script exits without
REM  spawning a second Chrome.
REM ============================================================================

setlocal EnableDelayedExpansion

REM --- Locate Chrome (portable across install layouts) ----------------------
set "CHROME_EXE="
for %%P in (
    "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
    "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
    "%LocalAppData%\Google\Chrome\Application\chrome.exe"
) do (
    if not defined CHROME_EXE if exist "%%~P" set "CHROME_EXE=%%~P"
)

REM --- Install-relative profile dir (works for any user) --------------------
set "DEBUG_PROFILE=%~dp0chrome-debug-profile"
set "DEBUG_PORT=9222"

REM --- Already running? ----------------------------------------------------
netstat -ano -p TCP | findstr /R /C:":%DEBUG_PORT% .*LISTENING" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [chrome_debug] Debug Chrome already running on port %DEBUG_PORT% - nothing to do.
    exit /b 0
)

if not defined CHROME_EXE (
    echo [chrome_debug] ERROR: Chrome not found in the usual locations.
    echo                Install Google Chrome, or edit CHROME_EXE in this script.
    exit /b 1
)

if not exist "%DEBUG_PROFILE%" mkdir "%DEBUG_PROFILE%"

echo [chrome_debug] Launching Chrome with --remote-debugging-port=%DEBUG_PORT%
echo [chrome_debug] Chrome  : %CHROME_EXE%
echo [chrome_debug] Profile : %DEBUG_PROFILE%
echo.
echo  FIRST RUN: Log into the sites you want automated:
echo    - Instagram web .... https://www.instagram.com
echo    - YouTube Studio ... https://studio.youtube.com
echo    - Threads .......... https://www.threads.net
echo    - Vercel ........... https://vercel.com
echo    - Cloudflare ....... https://dash.cloudflare.com
echo.
echo  Sessions persist in the profile dir - you only have to log in once.
echo.

start "" "%CHROME_EXE%" ^
    --remote-debugging-port=%DEBUG_PORT% ^
    --user-data-dir="%DEBUG_PROFILE%" ^
    --no-first-run ^
    --no-default-browser-check ^
    --disable-features=AutomationControlled

echo [chrome_debug] Debug Chrome launched. Attach from DATA via:
echo               python dashboard\chrome_cdp.py screenshot https://example.com
endlocal
