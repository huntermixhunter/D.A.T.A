# DAITA - embedded-Python runtime builder
# ----------------------------------------------------------------------------
# Produces a self-contained CPython that DAITA's bridge runs on, so a buyer needs
# ZERO pre-installed Python. Downloads the official Windows "embeddable" package,
# enables site-packages + pip, and pre-installs psutil (the bridge's one optional
# third-party dep). Everything else the bridge needs is in the standard library.
#
# Output:  <OutDir>\python\  (python.exe, pythonw.exe, stdlib zip, Lib\site-packages\psutil)
#
# Usage:   .\tools\prep_runtime.ps1 -OutDir build\staging\DAITA\runtime
#          .\tools\prep_runtime.ps1 -OutDir build\staging\DAITA\runtime -PyVersion 3.12.8
#
# Idempotent-ish: pass -Force to rebuild from scratch (deletes an existing python\).
param(
    [Parameter(Mandatory = $true)][string]$OutDir,
    [string]$PyVersion = "3.12.8",
    [switch]$Force
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$arch       = "amd64"
$pyShort    = "python" + ($PyVersion.Split('.')[0..1] -join '')   # e.g. python312
$embedUrl   = "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-$arch.zip"
$getPipUrl  = "https://bootstrap.pypa.io/get-pip.py"

$pythonDir  = Join-Path $OutDir "python"
$work       = Join-Path ([IO.Path]::GetTempPath()) ("data_runtime_" + [IO.Path]::GetRandomFileName())

Write-Host ""
Write-Host "  DAITA embedded-runtime builder" -ForegroundColor Cyan
Write-Host "  CPython $PyVersion ($arch) -> $pythonDir"
Write-Host ""

if (Test-Path $pythonDir) {
    if ($Force) { Remove-Item -Recurse -Force $pythonDir }
    else { Write-Host "  [X] $pythonDir already exists. Pass -Force to rebuild." -ForegroundColor Red; exit 1 }
}
New-Item -ItemType Directory -Force $pythonDir | Out-Null
New-Item -ItemType Directory -Force $work      | Out-Null

try {
    # 1. Download + extract the embeddable package
    $embedZip = Join-Path $work "embed.zip"
    Write-Host "  [..] Downloading embeddable Python..."
    Invoke-WebRequest -Uri $embedUrl -OutFile $embedZip -UseBasicParsing
    Expand-Archive -Path $embedZip -DestinationPath $pythonDir -Force
    Write-Host "  [OK] Extracted to $pythonDir"

    # 2. Enable site-packages + the `import site` machinery in the ._pth file.
    #    The embeddable ships with `import site` commented out and no site-packages
    #    entry; pip-installed packages are invisible until we fix both.
    $pth = Get-ChildItem -Path $pythonDir -Filter "$pyShort._pth" | Select-Object -First 1
    if (-not $pth) { throw "Could not find $pyShort._pth in the embeddable package" }
    @(
        "$pyShort.zip"
        "."
        "Lib\site-packages"
        ""
        "import site"
    ) | Set-Content -Path $pth.FullName -Encoding ascii
    Write-Host "  [OK] Patched $($pth.Name) (site-packages + import site enabled)"

    # 3. Bootstrap pip into the embeddable
    $getPip = Join-Path $work "get-pip.py"
    Write-Host "  [..] Bootstrapping pip..."
    Invoke-WebRequest -Uri $getPipUrl -OutFile $getPip -UseBasicParsing
    & "$pythonDir\python.exe" $getPip --no-warn-script-location --quiet
    if ($LASTEXITCODE -ne 0) { throw "get-pip.py failed (exit $LASTEXITCODE)" }
    Write-Host "  [OK] pip installed"

    # 4. Pre-install psutil (system-vitals panel). Baked in -> no internet at install time.
    Write-Host "  [..] Installing psutil into the runtime..."
    & "$pythonDir\python.exe" -m pip install --no-warn-script-location --quiet psutil
    if ($LASTEXITCODE -ne 0) { throw "psutil install failed (exit $LASTEXITCODE)" }
    Write-Host "  [OK] psutil baked in"

    # 5. Smoke test: the bundled interpreter must run and import psutil
    $check = & "$pythonDir\python.exe" -c "import sys, psutil; print(sys.version.split()[0])" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Runtime smoke test failed: $check" }
    Write-Host "  [OK] Runtime verified: Python $check + psutil import OK"

    # 6. Trim build noise (pip cache, __pycache__) to keep the installer lean
    Get-ChildItem -Path $pythonDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

    $size = "{0:N1} MB" -f ((Get-ChildItem $pythonDir -Recurse | Measure-Object Length -Sum).Sum / 1MB)
    Write-Host ""
    Write-Host "  Runtime ready: $pythonDir ($size)" -ForegroundColor Green
    Write-Host ""
}
finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
