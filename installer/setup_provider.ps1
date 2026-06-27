# DATA - AI provider bootstrap (runs during the installer, optional step)
# ----------------------------------------------------------------------------
# DATA's dashboard loads on its own, but chat needs an AI provider CLI. The
# recommended one is Anthropic's Claude Code, which installs via npm and so
# needs Node.js first. This script makes that one-click:
#
#   1. If Claude Code is already on PATH -> done, nothing to do.
#   2. Ensure Node.js LTS is present (winget; falls back to the official MSI).
#   3. npm install -g @anthropic-ai/claude-code
#   4. Report status. The ONE thing it can't do is sign you in -- you run
#      `claude` once and type /login (an interactive browser auth).
#
# Never hard-fails the install: if a step errors, DATA is still fully installed
# and the user can add a provider later. Exit code is always 0 so the wizard
# completes; real status is written to the log and echoed to the console.
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

Write-Host ""
Write-Host "  DATA - AI provider setup" -ForegroundColor Cyan
Write-Host "  ---------------------------------------------------------------"
"" | Set-Content -Path $log -ErrorAction SilentlyContinue

# 1. Already have Claude Code?
if (Find-Cmd "claude") {
    Say "Claude Code is already installed -- nothing to do." "Green"
    Say "If chat ever says 'run /login', open a terminal, type 'claude', then '/login'." "Gray"
    exit 0
}

# 2. Ensure Node.js
$npm = Find-Npm
if ($npm) {
    Say "Node.js found." "Green"
} else {
    Say "Node.js not found -- installing the LTS release..." "Yellow"
    $installed = $false

    # 2a. Preferred: winget (present on Windows 10 1809+/11)
    if (Find-Cmd "winget") {
        Say "Installing Node.js via winget (this can take a minute)..."
        winget install --id OpenJS.NodeJS.LTS -e --silent `
            --accept-source-agreements --accept-package-agreements --disable-interactivity 2>&1 |
            ForEach-Object { Add-Content -Path $log -Value $_ }
        $npm = Find-Npm
        if ($npm) { $installed = $true; Say "Node.js installed via winget." "Green" }
    }

    # 2b. Fallback: download + run the official Node MSI silently
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
        Say "  npm install -g @anthropic-ai/claude-code" "Yellow"
        exit 0   # DATA itself is still fully installed
    }
}

# 3. Install Claude Code globally
Say "Installing Claude Code (npm i -g @anthropic-ai/claude-code)..."
& $npm install -g "@anthropic-ai/claude-code" 2>&1 | ForEach-Object {
    Add-Content -Path $log -Value $_
}
if (Find-Cmd "claude") {
    Say "Claude Code installed." "Green"
} else {
    # npm's global bin may not be on the current session PATH yet; check directly.
    $npmBin = & $npm prefix -g 2>$null
    if ($npmBin -and (Test-Path (Join-Path $npmBin "claude.cmd"))) {
        Say "Claude Code installed (will be on PATH in a new terminal)." "Green"
    } else {
        Say "Claude Code install did not complete. Run later:" "Yellow"
        Say "  npm install -g @anthropic-ai/claude-code" "Yellow"
        exit 0
    }
}

# 4. The one manual step
Write-Host ""
Say "ONE more step the installer can't do for you:" "Cyan"
Say "Open a terminal, type 'claude', then '/login' to sign in once." "Cyan"
Say "After that, DATA's chat is live." "Cyan"
Write-Host ""
exit 0
