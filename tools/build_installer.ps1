# DATA - one-click installer (.exe) builder
# ----------------------------------------------------------------------------
# Produces dist\DATA-Setup-v<version>.exe : a click-through wizard that installs
# DATA with a bundled embedded Python (buyer needs ZERO preinstalled Python) and
# optionally installs the AI provider (Node.js + Claude Code).
#
# Pipeline:
#   1. Stage a clean product tree from the repo (git archive HEAD = tracked files
#      only -> no .env, no secrets, no dev tools, exactly like the buyer zip).
#   2. Drop in the embedded Python runtime (cached between builds).
#   3. Compile installer\DATA.iss with Inno Setup -> dist\DATA-Setup-v<ver>.exe.
#
# Usage:
#   .\tools\build_installer.ps1 1.0.21
#   .\tools\build_installer.ps1 1.0.21 -RebuildRuntime      # force re-download Python
#   .\tools\build_installer.ps1 1.0.21 -PyVersion 3.12.8
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$PyVersion = "3.12.8",
    [switch]$RebuildRuntime,
    [switch]$SkipVoice
)
$ErrorActionPreference = "Stop"

if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    Write-Host "Version must look like 1.0.0" -ForegroundColor Red; exit 1
}

# --- locate repo root ---
$root = (git rev-parse --show-toplevel 2>$null)
if (-not $root) { $root = Split-Path -Parent $PSScriptRoot }
$root = (Resolve-Path $root).Path
Set-Location $root

# --- locate ISCC (Inno Setup compiler) ---
$iscc = $null
foreach ($c in @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)) { if (Test-Path $c) { $iscc = $c; break } }
if (-not $iscc) {
    Write-Host "Inno Setup compiler (ISCC.exe) not found." -ForegroundColor Red
    Write-Host "Install it:  winget install --id JRSoftware.InnoSetup -e" -ForegroundColor Yellow
    exit 1
}

$build       = Join-Path $root "build"
$staging     = Join-Path $build "staging\DATA"
$runtimeCache= Join-Path $build "runtime_cache"
$archiveZip  = Join-Path $build "_archive.zip"

Write-Host ""
Write-Host "  DATA installer builder  -  v$Version" -ForegroundColor Cyan
Write-Host "  ---------------------------------------------------------------"

# --- 1. stage a clean product tree from HEAD ---
Write-Host "  [..] Staging clean product tree (git archive HEAD)..."
if (Test-Path (Join-Path $build "staging")) { Remove-Item -Recurse -Force (Join-Path $build "staging") }
New-Item -ItemType Directory -Force (Join-Path $build "staging") | Out-Null
if (Test-Path $archiveZip) { Remove-Item -Force $archiveZip }
git archive --format=zip --prefix="DATA/" -o $archiveZip HEAD
if ($LASTEXITCODE -ne 0) { Write-Host "  git archive failed" -ForegroundColor Red; exit 1 }
Expand-Archive -Path $archiveZip -DestinationPath (Join-Path $build "staging") -Force
Remove-Item -Force $archiveZip
$fileCount = (Get-ChildItem $staging -Recurse -File).Count
Write-Host "  [OK] Staged $fileCount product files -> $staging"

# --- 2. embedded Python runtime (cached) ---
# The runtime bakes in the voice stack by default (Conversation Mode works on a
# fresh install with no runtime pip / reboot). A cached runtime from an older,
# voice-less build must be treated as a cache MISS, or the .exe would ship
# without voice and the old install-on-first-use loop would return.
$cachedPy   = Join-Path $runtimeCache "python"
$voiceMark  = Join-Path $cachedPy "Lib\site-packages\faster_whisper"
$hasPy      = Test-Path (Join-Path $cachedPy "python.exe")
$hasVoice   = Test-Path $voiceMark
$cacheStale = $hasPy -and (-not $SkipVoice) -and (-not $hasVoice)
if ($cacheStale) {
    Write-Host "  [!!] Cached runtime predates the voice bake - forcing rebuild" -ForegroundColor Yellow
}
if ($RebuildRuntime -or -not $hasPy -or $cacheStale) {
    Write-Host "  [..] Building embedded Python runtime (cache miss)..."
    if ($SkipVoice) {
        & "$root\tools\prep_runtime.ps1" -OutDir $runtimeCache -PyVersion $PyVersion -Force -SkipVoice
    } else {
        & "$root\tools\prep_runtime.ps1" -OutDir $runtimeCache -PyVersion $PyVersion -Force
    }
    if ($LASTEXITCODE -ne 0) { Write-Host "  runtime prep failed" -ForegroundColor Red; exit 1 }
} else {
    Write-Host "  [OK] Reusing cached runtime ($cachedPy)  [-RebuildRuntime to refresh]"
}
Write-Host "  [..] Copying runtime into staging..."
$stageRuntime = Join-Path $staging "runtime\python"
New-Item -ItemType Directory -Force (Split-Path -Parent $stageRuntime) | Out-Null
# Use robocopy, NOT Copy-Item: the embedded runtime (onnxruntime, setuptools,
# pip _vendor) nests paths past Windows MAX_PATH (260), and Copy-Item -Recurse
# silently aborts partway, shipping a runtime missing pyyaml/setuptools/pip
# (breaks the voice stack). Robocopy handles long paths natively. /MIR mirrors
# the tree, /NFL /NDL /NJH /NJS /NP keep the log quiet. Robocopy exit codes
# 0-7 are success (8+ is a real failure).
$rc = Start-Process robocopy -ArgumentList @("`"$cachedPy`"","`"$stageRuntime`"","/MIR","/NFL","/NDL","/NJH","/NJS","/NP","/R:1","/W:1") -Wait -PassThru -NoNewWindow
if ($rc.ExitCode -ge 8) { Write-Host "  runtime copy (robocopy) failed with code $($rc.ExitCode)" -ForegroundColor Red; exit 1 }
$srcCount = (Get-ChildItem $cachedPy -Recurse -File -Force).Count
$dstCount = (Get-ChildItem $stageRuntime -Recurse -File -Force).Count
if ($dstCount -lt $srcCount) {
    Write-Host "  runtime copy incomplete: staged $dstCount of $srcCount files" -ForegroundColor Red; exit 1
}
Write-Host "  [OK] Runtime in place ($dstCount files) -> $stageRuntime"

# --- 3. compile the wizard ---
New-Item -ItemType Directory -Force (Join-Path $root "dist") | Out-Null
Write-Host "  [..] Compiling installer with Inno Setup..."
& $iscc "/DAppVersion=$Version" "/DStagingDir=$staging" (Join-Path $root "installer\DATA.iss")
if ($LASTEXITCODE -ne 0) { Write-Host "  ISCC compile failed" -ForegroundColor Red; exit 1 }

$exe = Join-Path $root "dist\DATA-Setup-v$Version.exe"
if (-not (Test-Path $exe)) { Write-Host "  Expected output missing: $exe" -ForegroundColor Red; exit 1 }

$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
$size = "{0:N1} MB" -f ((Get-Item $exe).Length / 1MB)

Write-Host ""
Write-Host "  Installer ready:" -ForegroundColor Green
Write-Host "    File:   $exe  ($size)"
Write-Host "    SHA256: $hash"
Write-Host ""
Write-Host "  This is the primary buyer download (one-click, bundled Python)."
Write-Host "  The DATA-v$Version.zip (tools\release.ps1) stays as the Mac/Linux/advanced path."
Write-Host ""
