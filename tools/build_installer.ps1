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
    [switch]$RebuildRuntime
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
$cachedPy = Join-Path $runtimeCache "python"
if ($RebuildRuntime -or -not (Test-Path (Join-Path $cachedPy "python.exe"))) {
    Write-Host "  [..] Building embedded Python runtime (cache miss)..."
    & "$root\tools\prep_runtime.ps1" -OutDir $runtimeCache -PyVersion $PyVersion -Force
    if ($LASTEXITCODE -ne 0) { Write-Host "  runtime prep failed" -ForegroundColor Red; exit 1 }
} else {
    Write-Host "  [OK] Reusing cached runtime ($cachedPy)  [-RebuildRuntime to refresh]"
}
Write-Host "  [..] Copying runtime into staging..."
$stageRuntime = Join-Path $staging "runtime\python"
New-Item -ItemType Directory -Force (Split-Path -Parent $stageRuntime) | Out-Null
Copy-Item -Recurse -Force $cachedPy $stageRuntime
Write-Host "  [OK] Runtime in place -> $stageRuntime"

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
