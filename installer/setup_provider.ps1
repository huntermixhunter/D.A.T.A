# DATA - AI provider bootstrap (runs during the installer, optional step)
# ----------------------------------------------------------------------------
# DATA's dashboard loads on its own, but chat needs an AI provider CLI. Most
# install via npm, so Node.js is the shared prerequisite. This script makes
# installing one or more of them one-click:
#
#   1. Grok Build is not on npm: run xAI's official PowerShell installer.
#   2. Ensure Node.js LTS is present (winget; falls back to the official MSI).
#   3. For each requested npm CLI, npm install -g <package>.
#   4. Report status. The ONE thing it can't do is sign you in -- you run the
#      CLI once and authenticate (an interactive browser/login flow).
#
# Which CLIs to install is passed by the wizard via -Clis (comma list). Valid
# ids: claude, codex, gemini, grok. Defaults to "claude" when omitted.
#
#   claude  -> @anthropic-ai/claude-code  (Anthropic Claude Code)   bin: claude
#   codex   -> @openai/codex              (OpenAI Codex CLI)        bin: codex
#   gemini  -> @google/gemini-cli         (Google Gemini CLI)       bin: gemini
#   grok    -> https://x.ai/cli/install.ps1 (xAI Grok Build)        bin: grok
#             (installs to %USERPROFILE%\.grok\bin and adds it to the user PATH)
#
# Never hard-fails the install: if a step errors, DATA is still fully installed
# and the user can add a provider later. Exit code is always 0 so the wizard
# completes; real status is written to the log and echoed to the console.
param(
    [string]$Clis = "claude"
)
$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$log = Join-Path $env:TEMP "data_provider_setup.log"
function Say($msg, $color = "Gray") {
    Write-Host "  $msg" -ForegroundColor $color
    try { Add-Content -Path $log -Value ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg) } catch {}
}

function Find-Cmd($name) {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    return $null
}

# npm/node may not be on the *current* session PATH right after a winget install.
# Probe the well-known install location too.
function Find-Npm {
    $c = Find-Cmd "npm"
    if ($c) { return $c }
    foreach ($p in @(
        "$env:ProgramFiles\nodejs\npm.cmd",
        "${env:ProgramFiles(x86)}\nodejs\npm.cmd",
        "$env:LOCALAPPDATA\Programs\nodejs\npm.cmd"
    )) { if (Test-Path $p) { return $p } }
    return $null
}

# Catalog of supported provider CLIs.
$CATALOG = @{
    "claude" = @{ pkg = "@anthropic-ai/claude-code"; bin = "claude"; name = "Claude Code (Anthropic)"; login = "type 'claude', then '/login'" }
    "codex"  = @{ pkg = "@openai/codex";             bin = "codex";  name = "Codex CLI (OpenAI)";      login = "type 'codex', then sign in when prompted" }
    "gemini" = @{ pkg = "@google/gemini-cli";        bin = "gemini"; name = "Gemini CLI (Google)";     login = "type 'gemini', then sign in when prompted" }
    "grok"   = @{ pkg = $null; script = "https://x.ai/cli/install.ps1"; bin = "grok"; name = "Grok Build (xAI)"; login = "type 'grok login' and sign in with your SuperGrok or X Premium+ account" }
}

# Grok Build lands in %USERPROFILE%\.grok\bin (or $env:GROK_HOME\bin); that
# folder is added to the USER PATH, which this session does not see yet.
function Find-Grok {
    $c = Find-Cmd "grok"
    if ($c) { return $c }
    $homeDir = if ($env:GROK_HOME) { $env:GROK_HOME } else { Join-Path $env:USERPROFILE ".grok" }
    foreach ($p in @(
        (Join-Path $homeDir "bin\grok.exe"),
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\grok.exe"
    )) { if (Test-Path $p) { return $p } }
    return $null
}

# Parse + normalize the requested list.
$requested = @()
foreach ($id in ($Clis -split "[,; ]+")) {
    $k = $id.Trim().ToLower()
    if ($k -and $CATALOG.ContainsKey($k) -and ($requested -notcontains $k)) { $requested += $k }
}
if ($requested.Count -eq 0) { $requested = @("claude") }

Write-Host ""
Write-Host "  DATA - AI provider setup" -ForegroundColor Cyan
Write-Host "  ---------------------------------------------------------------"
"" | Set-Content -Path $log -ErrorAction SilentlyContinue
Say ("Requested CLIs: " + ($requested -join ", ")) "Gray"

$ok = @()
$pending = @()

# Grok Build first: it does not need Node.js, so a Node failure below can't
# block it. Runs xAI's official installer in a child PowerShell so anything it
# does to its own session (exit, scope changes) stays contained.
if ($requested -contains "grok") {
    $requested = @($requested | Where-Object { $_ -ne "grok" })
    if (Find-Grok) {
        Say "Grok Build (xAI) is already installed -- skipping." "Green"
    } else {
        Say "Installing Grok Build (xAI) with the official installer from x.ai..."
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm $($CATALOG['grok'].script) | iex" 2>&1 |
            ForEach-Object { Add-Content -Path $log -Value $_ }
        if (Find-Grok) {
            Say "Grok Build (xAI) installed (will be on PATH in a new terminal)." "Green"; $ok += "grok"
        } else {
            Say "Grok Build install did not complete. Run later in PowerShell:  irm https://x.ai/cli/install.ps1 | iex" "Yellow"
            $pending += "grok"
        }
    }
}

# Figure out which npm CLIs still need installing (skip any already on PATH).
$toInstall = @()
foreach ($id in $requested) {
    if (Find-Cmd $CATALOG[$id].bin) {
        Say ("$($CATALOG[$id].name) is already installed -- skipping.") "Green"
    } else {
        $toInstall += $id
    }
}
if ($toInstall.Count -eq 0 -and $requested.Count -gt 0) {
    Say "All requested npm provider CLIs are already installed." "Green"
}

# Ensure Node.js (shared prerequisite for every npm CLI). Skipped when only
# Grok Build was picked; clawdcursor below still installs if Node is present.
$npm = Find-Npm
if ($toInstall.Count -eq 0) {
    # Nothing from npm to install.
} elseif ($npm) {
    Say "Node.js found." "Green"
} else {
    Say "Node.js not found -- installing the LTS release..." "Yellow"
    $installed = $false

    # Preferred: winget (present on Windows 10 1809+/11)
    if (Find-Cmd "winget") {
        Say "Installing Node.js via winget (this can take a minute)..."
        winget install --id OpenJS.NodeJS.LTS -e --silent `
            --accept-source-agreements --accept-package-agreements --disable-interactivity 2>&1 |
            ForEach-Object { Add-Content -Path $log -Value $_ }
        $npm = Find-Npm
        if ($npm) { $installed = $true; Say "Node.js installed via winget." "Green" }
    }

    # Fallback: download + run the official Node MSI silently
    if (-not $installed) {
        Say "winget unavailable or failed -- downloading the Node.js MSI..." "Yellow"
        try {
            $ver  = "20.18.1"   # current LTS at build time; MSI is forward-safe
            $msiUrl = "https://nodejs.org/dist/v$ver/node-v$ver-x64.msi"
            $msi  = Join-Path $env:TEMP "node-lts-x64.msi"
            Invoke-WebRequest -Uri $msiUrl -OutFile $msi -UseBasicParsing
            Start-Process "msiexec.exe" -ArgumentList "/i `"$msi`" /qn /norestart" -Wait
            $npm = Find-Npm
            if ($npm) { $installed = $true; Say "Node.js installed via MSI." "Green" }
        } catch {
            Say "Node.js MSI install failed: $($_.Exception.Message)" "Red"
        }
    }

    if (-not $npm) {
        Say "Could not install Node.js automatically." "Red"
        Say "Install it yourself from https://nodejs.org (LTS), then run:" "Yellow"
        foreach ($id in $toInstall) { Say ("  npm install -g " + $CATALOG[$id].pkg) "Yellow" }
        $pending += $toInstall
        $toInstall = @()   # DATA itself is still fully installed; carry on to the summary
    }
}

# Install each requested npm CLI globally.
foreach ($id in $toInstall) {
    $pkg = $CATALOG[$id].pkg
    $bin = $CATALOG[$id].bin
    Say ("Installing $($CATALOG[$id].name)  (npm i -g $pkg)...")
    & $npm install -g $pkg 2>&1 | ForEach-Object { Add-Content -Path $log -Value $_ }
    if (Find-Cmd $bin) {
        Say ("$($CATALOG[$id].name) installed.") "Green"; $ok += $id
    } else {
        # npm's global bin may not be on the current session PATH yet; check directly.
        $npmBin = & $npm prefix -g 2>$null
        if ($npmBin -and (Test-Path (Join-Path $npmBin "$bin.cmd"))) {
            Say ("$($CATALOG[$id].name) installed (will be on PATH in a new terminal).") "Green"; $ok += $id
        } else {
            Say ("$($CATALOG[$id].name) install did not complete. Run later:  npm install -g $pkg") "Yellow"
            $pending += $id
        }
    }
}

# clawdcursor — the desktop-takeover MCP server (last-mile GUI automation).
# Installs alongside the provider CLIs since Node/npm is already present here.
# Registered in .mcp.json but stays DISARMED until armed in Settings, so this is
# safe to install by default. Never hard-fails the wizard.
if ($npm) {
    if (Find-Cmd "clawdcursor") {
        Say "clawdcursor already installed (desktop takeover -- arm it in Settings)." "Green"
    } else {
        Say "Installing clawdcursor (desktop-takeover MCP server)..."
        & $npm install -g clawdcursor 2>&1 | ForEach-Object { Add-Content -Path $log -Value $_ }
        $npmBin = & $npm prefix -g 2>$null
        if ((Find-Cmd "clawdcursor") -or ($npmBin -and (Test-Path (Join-Path $npmBin "clawdcursor.cmd")))) {
            Say "clawdcursor installed (stays disarmed until you flip the switch in Settings > Upgrades)." "Green"
        } else {
            Say "clawdcursor install did not complete. Run later:  npm install -g clawdcursor" "Yellow"
        }
    }
}

# Final guidance — each installed CLI still needs an interactive sign-in.
if ($ok.Count -gt 0) {
    Write-Host ""
    Say "ONE more step the installer can't do for you -- sign in to each CLI once:" "Cyan"
    foreach ($id in $ok) { Say ("  $($CATALOG[$id].name): open a terminal, " + $CATALOG[$id].login) "Cyan" }
    Say "After that, DATA's chat is live with your chosen provider." "Cyan"
    Write-Host ""
}
exit 0
