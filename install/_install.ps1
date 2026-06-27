# DAITA - Windows installer
# Checks Python, installs optional deps, creates the start_daita.bat launcher.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "  DAITA - Dashboard for Artificial Intelligence Thought and Action" -ForegroundColor Cyan
Write-Host "  Windows installer" -ForegroundColor Cyan
Write-Host ""

# 1. Find Python 3.10+
$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $v = & $cmd -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($v -and [version]$v -ge [version]"3.10") { $python = $cmd; break }
    } catch {}
}
if (-not $python) {
    Write-Host "  [X] Python 3.10+ not found." -ForegroundColor Red
    Write-Host "      Install it from https://www.python.org/downloads/ (check 'Add to PATH')"
    exit 1
}
Write-Host "  [OK] Python $((& $python --version 2>&1)) found ($python)"

# 2. Optional: psutil for the system-vitals panel
try {
    & $python -c "import psutil" 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Host "  [OK] psutil already installed" }
    else { throw "missing" }
} catch {
    Write-Host "  [..] Installing psutil (system vitals - optional but recommended)..."
    & $python -m pip install --quiet psutil
    if ($LASTEXITCODE -eq 0) { Write-Host "  [OK] psutil installed" }
    else { Write-Host "  [!!] psutil install failed - vitals will read zero. Continuing." -ForegroundColor Yellow }
}

# 3. Check for an AI provider CLI
$providers = @()
foreach ($p in @("claude", "codex", "gemini", "ollama")) {
    if (Get-Command $p -ErrorAction SilentlyContinue) { $providers += $p }
}
if ($providers.Count -gt 0) {
    Write-Host "  [OK] AI provider(s) found: $($providers -join ', ')"
} else {
    Write-Host "  [!!] No AI provider CLI found (claude / codex / gemini / ollama)." -ForegroundColor Yellow
    Write-Host "      The dashboard will load, but chat needs one. Recommended:"
    Write-Host "      https://docs.claude.com/en/docs/claude-code"
    Write-Host "      NOTE: install the Claude Code COMMAND-LINE tool, not the" -ForegroundColor Yellow
    Write-Host "      Claude Desktop app. The CLI installs via npm, which needs" -ForegroundColor Yellow
    Write-Host "      Node.js first: https://nodejs.org (LTS). Then in a terminal:" -ForegroundColor Yellow
    Write-Host "        npm install -g @anthropic-ai/claude-code" -ForegroundColor Yellow
    Write-Host "      After installing, run 'claude' in a terminal and type" -ForegroundColor Yellow
    Write-Host "      '/login' once to sign in (DAITA can't show the login" -ForegroundColor Yellow
    Write-Host "      prompt itself; if chat says 'run /login', that's why)." -ForegroundColor Yellow
    Write-Host "      Verify with 'claude --version'." -ForegroundColor Yellow
}

# 4. Seed .env from the example if missing
if (-not (Test-Path "$root\.env")) {
    Copy-Item "$root\.env.example" "$root\.env"
    Write-Host "  [OK] Created .env (edit it to set weather coords, port, etc.)"
}

# 4b. Install the bundled DAITA-core skills (idempotent; never clobbers your own copies)
if (Test-Path "$root\dashboard\install_skills.py") {
    Write-Host "  [..] Installing bundled DAITA-core skills..."
    try {
        & $python "$root\dashboard\install_skills.py" | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Host "  [OK] DAITA-core skills installed" }
        else { Write-Host "  [!!] Skill install reported an issue - DAITA still runs. Re-run: python dashboard\install_skills.py" -ForegroundColor Yellow }
    } catch {
        Write-Host "  [!!] Skill install failed - DAITA still runs. Re-run later: python dashboard\install_skills.py" -ForegroundColor Yellow
    }
}

# 5. Write the launcher
# Starts the supervisor (port 7766), which spawns the bridge (port 7777) and
# stays alive to handle the dashboard's REBOOT button — so DAITA can be brought
# back online from the page without closing the open windows. Stale instances on
# either port are cleared first so a relaunch always gets a clean start.
#
# The supervisor is launched DETACHED via the windowless interpreter (pythonw),
# so the launcher exits immediately instead of holding a "DAITA-LaunchControl"
# console open for the life of the app. Every launch path — the desktop
# shortcut, the .vbs, or double-clicking this .bat directly — now stays clean.
$pythonw = $python
try {
    $exe = & $python -c "import sys; print(sys.executable)" 2>$null
    if ($exe) {
        $cand = Join-Path (Split-Path -Parent $exe) "pythonw.exe"
        if (Test-Path $cand) { $pythonw = "`"$cand`"" }
    }
} catch {}
if ($pythonw -eq $python) {
    Write-Host "  [!!] pythonw.exe not found next to $python - the launcher may flash a console. Continuing." -ForegroundColor Yellow
}

$launcher = @"
@echo off
title DAITA-LaunchControl
cd /d "%~dp0dashboard"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7777 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7766 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
start "" http://localhost:7777
start "DAITA" $pythonw supervisor.py
"@
Set-Content -Path "$root\start_daita.bat" -Value $launcher -Encoding ascii
Write-Host "  [OK] Launcher written: start_daita.bat (windowless - no console stays open)"

# 5b. Background launcher — runs start_daita.bat with a hidden console so DAITA
# lives in the background with NO visible "DAITA-LaunchControl" window. The
# supervisor still stays alive (REBOOT keeps working); the dashboard still opens
# in the browser. This is the launcher the desktop shortcut points at.
$vbs = @"
' DAITA - starts the dashboard in the background with no console window.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run """" & scriptDir & "\start_daita.bat""", 0, False
"@
Set-Content -Path "$root\start_daita.vbs" -Value $vbs -Encoding ascii
Write-Host "  [OK] Background launcher written: start_daita.vbs (no console window)"

# 5c. Clean stop — since the background launcher has no window to close, give the
# user a one-click way to shut DAITA down (frees both ports).
$stopper = @"
@echo off
title DAITA-Stop
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7777 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7766 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
echo DAITA stopped.
timeout /t 2 >nul
"@
Set-Content -Path "$root\stop_daita.bat" -Value $stopper -Encoding ascii
Write-Host "  [OK] Stop script written: stop_daita.bat"

# 6. Desktop shortcut with the DAITA icon (opt-in)
# Ask first so the user stays in control. Defaults to Yes on a bare Enter.
# In a non-interactive run (no console), skip the prompt and create it anyway.
$makeShortcut = $true
try {
    if (-not [Environment]::UserInteractive) { throw "non-interactive" }
    $answer = Read-Host "  Add a DAITA icon to your desktop? [Y/n]"
    if ($answer -and $answer.Trim().ToLower().StartsWith("n")) { $makeShortcut = $false }
} catch {
    # No interactive console - keep the default (create the shortcut).
}

if ($makeShortcut) {
    try {
        $ws  = New-Object -ComObject WScript.Shell
        $lnk = $ws.CreateShortcut("$([Environment]::GetFolderPath('Desktop'))\DAITA.lnk")
        $lnk.TargetPath       = "$root\start_daita.vbs"
        $lnk.WorkingDirectory = $root
        $lnk.IconLocation     = "$root\dashboard\favicon.ico"
        $lnk.Description      = "DAITA - Dashboard for Artificial Intelligence Thought and Action"
        $lnk.Save()
        Write-Host "  [OK] Desktop shortcut created: DAITA"
    } catch {
        Write-Host "  [!!] Could not create a desktop shortcut - use start_daita.bat directly." -ForegroundColor Yellow
    }
} else {
    Write-Host "  [--] Skipped desktop shortcut - launch with start_daita.bat anytime."
}

Write-Host ""
Write-Host "  Done. Launch with the DAITA desktop shortcut (runs in the background," -ForegroundColor Green
Write-Host "  no console window). To stop DAITA later, run stop_daita.bat."
Write-Host "  Dashboard: http://localhost:7777"
Write-Host "  Tip: both start_daita.bat and the shortcut now run windowless. For"
Write-Host "  debugging output, check bridge.log next to start_daita.bat."
Write-Host ""
