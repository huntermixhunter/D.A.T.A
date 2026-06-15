# DATA - Windows installer
# Checks Python, installs optional deps, creates the start_data.bat launcher.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "  DATA - Dashboard for Analytical Thought and Action" -ForegroundColor Cyan
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
}

# 4. Seed .env from the example if missing
if (-not (Test-Path "$root\.env")) {
    Copy-Item "$root\.env.example" "$root\.env"
    Write-Host "  [OK] Created .env (edit it to set weather coords, port, etc.)"
}

# 5. Write the launcher
# Starts the supervisor (port 7766), which spawns the bridge (port 7777) and
# stays alive to handle the dashboard's REBOOT button — so DATA can be brought
# back online from the page without closing the open windows. Stale instances on
# either port are cleared first so a relaunch always gets a clean start.
$launcher = @"
@echo off
title DATA-LaunchControl
cd /d "%~dp0dashboard"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7777 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7766 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
start "" http://localhost:7777
$python supervisor.py
"@
Set-Content -Path "$root\start_data.bat" -Value $launcher -Encoding ascii
Write-Host "  [OK] Launcher written: start_data.bat"

# 6. Desktop shortcut with the DATA icon
try {
    $ws  = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut("$([Environment]::GetFolderPath('Desktop'))\DATA.lnk")
    $lnk.TargetPath       = "$root\start_data.bat"
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation     = "$root\dashboard\favicon.ico"
    $lnk.Description      = "DATA - Dashboard for Analytical Thought and Action"
    $lnk.Save()
    Write-Host "  [OK] Desktop shortcut created: DATA"
} catch {
    Write-Host "  [!!] Could not create a desktop shortcut - use start_data.bat directly." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  Done. Run start_data.bat (or the DATA desktop shortcut) to launch." -ForegroundColor Green
Write-Host "  Dashboard: http://localhost:7777"
Write-Host ""
