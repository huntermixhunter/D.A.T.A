# DATA — buyer-zip release builder
# Usage:  .\tools\release.ps1 1.0.0
# Produces dist\DATA-v<version>.zip from tracked files only (git archive),
# tags the release, and prints a SHA256 the buyer can verify.
param(
    [Parameter(Mandatory = $true)][string]$Version
)
$ErrorActionPreference = "Stop"
# Repo root — robust regardless of how the script is invoked
$root = (git rev-parse --show-toplevel 2>$null)
if (-not $root) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $root = Split-Path -Parent $scriptDir
}
$root = (Resolve-Path $root).Path
Set-Location $root

if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    Write-Host "Version must look like 1.0.0" -ForegroundColor Red; exit 1
}

# Refuse to package uncommitted work — the zip must match a commit exactly.
if (git status --porcelain) {
    Write-Host "Working tree is dirty — commit or stash first." -ForegroundColor Red
    git status --short
    exit 1
}

New-Item -ItemType Directory -Force "$root\dist" | Out-Null
$zip = "$root\dist\DATA-v$Version.zip"
if (Test-Path $zip) { Remove-Item -Force -Confirm:$false $zip }

# Tracked files only: no .git, no .env, no runtime state, no dev tools
# (tools/ and .gitattributes are export-ignored in .gitattributes).
git archive --format=zip --prefix="DATA/" -o $zip HEAD
if ($LASTEXITCODE -ne 0) { Write-Host "git archive failed" -ForegroundColor Red; exit 1 }

# Tag the release if the tag doesn't exist yet
if (-not (git tag -l "v$Version")) {
    git tag -a "v$Version" -m "DATA v$Version"
    Write-Host "  [OK] Tagged v$Version  (push with: git push origin v$Version)"
} else {
    Write-Host "  [..] Tag v$Version already exists — zip rebuilt from HEAD"
}

$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
$size = "{0:N1} MB" -f ((Get-Item $zip).Length / 1MB)

Write-Host ""
Write-Host "  Release ready:" -ForegroundColor Green
Write-Host "    File:   $zip  ($size)"
Write-Host "    SHA256: $hash"
Write-Host ""
Write-Host "  Upload the zip to your store (Gumroad / Lemon Squeezy / Stripe)."
Write-Host "  Buyer instructions: unzip, then run install\install.ps1 (Windows)"
Write-Host "  or install/install.sh (macOS / Linux / ChromeOS)."
