# DATA - embedded-Python runtime builder
# ----------------------------------------------------------------------------
# Produces a self-contained CPython that DATA's bridge runs on, so a buyer needs
# ZERO pre-installed Python. Downloads the official Windows "embeddable" package,
# enables site-packages + pip, and pre-installs psutil (the bridge's one optional
# third-party dep). Everything else the bridge needs is in the standard library.
#
# Output:  <OutDir>\python\  (python.exe, pythonw.exe, stdlib zip, Lib\site-packages\psutil)
#
# Usage:   .\tools\prep_runtime.ps1 -OutDir build\staging\DATA\runtime
#          .\tools\prep_runtime.ps1 -OutDir build\staging\DATA\runtime -PyVersion 3.12.8
#
# Idempotent-ish: pass -Force to rebuild from scratch (deletes an existing python\).
param(
    [Parameter(Mandatory = $true)][string]$OutDir,
    [string]$PyVersion = "3.12.8",
    [switch]$Force,
    [switch]$SkipVoice
)
$ErrorActionPreference = "Stop"

# Voice stack (Conversation Mode STT/TTS) is baked into the runtime by default so
# a fresh .exe install has a working voice loop on first launch - no runtime pip,
# no bridge reboot, no flashing PowerShell window, no first-use lag. Pass
# -SkipVoice to produce the old lean (chat-only) runtime.
$repoRoot = Split-Path -Parent $PSScriptRoot
$voiceReq = Join-Path $repoRoot "dashboard\requirements-voice.txt"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$arch       = "amd64"
$pyShort    = "python" + ($PyVersion.Split('.')[0..1] -join '')   # e.g. python312
$embedUrl   = "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-$arch.zip"
$getPipUrl  = "https://bootstrap.pypa.io/get-pip.py"

$pythonDir  = Join-Path $OutDir "python"
$work       = Join-Path ([IO.Path]::GetTempPath()) ("data_runtime_" + [IO.Path]::GetRandomFileName())

Write-Host ""
Write-Host "  DATA embedded-runtime builder" -ForegroundColor Cyan
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

    # 1b. Bundle the Microsoft Visual C++ runtime DLLs the embeddable does NOT
    #     ship. The embeddable includes vcruntime140.dll (and 140_1) but NOT
    #     msvcp140.dll - the C++ standard library. numpy / onnxruntime /
    #     ctranslate2 are C++ and fail to import without it on a fresh Windows
    #     box that has no VC++ 2015-2022 redistributable. psutil (pure C) works,
    #     which is why the smoke test passed but Conversation Mode did not. These
    #     DLLs are redistributable per Microsoft's redist list.
    $vcDlls = @(
        "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
        "msvcp140_codecvt_ids.dll", "concrt140.dll", "vcomp140.dll", "vccorlib140.dll"
    )
    $sys32 = Join-Path $env:WINDIR "System32"
    $copied = 0
    foreach ($dll in $vcDlls) {
        $src = Join-Path $sys32 $dll
        if (Test-Path $src) {
            Copy-Item -Force $src (Join-Path $pythonDir $dll)
            $copied++
        } else {
            Write-Host "  [!!] $dll not found in System32 (skipping)" -ForegroundColor Yellow
        }
    }
    if (-not (Test-Path (Join-Path $pythonDir "msvcp140.dll"))) {
        throw "msvcp140.dll could not be bundled - numpy/onnxruntime will not import on fresh machines. Install the VC++ 2015-2022 redistributable on this build box and retry."
    }
    Write-Host "  [OK] Bundled $copied VC++ runtime DLL(s) (msvcp140 et al.)"

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

    # 4b. Bake the voice stack (kokoro-onnx + faster-whisper, CPU-only) straight
    #     into the runtime. This is the fix for the retail Conversation Mode
    #     symptoms: with the wheels already present and importable, the bridge
    #     reports stt_available=true on first launch, so the frontend NEVER runs
    #     the on-demand pip install or the bridge reboot - which is what flashed
    #     a PowerShell window and tanked the video framerate. Only the one-time
    #     voice-MODEL download (~415MB) remains on first use, done in-process.
    $voiceBaked = $false
    if ($SkipVoice) {
        Write-Host "  [--] -SkipVoice set: building lean chat-only runtime (no voice stack)" -ForegroundColor Yellow
    } elseif (-not (Test-Path $voiceReq)) {
        Write-Host "  [!!] requirements-voice.txt not found at $voiceReq - skipping voice bake" -ForegroundColor Yellow
    } else {
        Write-Host "  [..] Baking voice stack into the runtime (kokoro-onnx + faster-whisper, ~200MB download)..."
        & "$pythonDir\python.exe" -m pip install --no-warn-script-location --disable-pip-version-check -r $voiceReq
        if ($LASTEXITCODE -ne 0) { throw "voice stack install failed (exit $LASTEXITCODE) - Conversation Mode would not work on a fresh install" }
        $voiceBaked = $true
        Write-Host "  [OK] Voice stack baked in"
    }

    # 5. Smoke test: the bundled interpreter must run and import psutil - and the
    #    voice wheels too when baked, since a C++-runtime miss (msvcp140) shows up
    #    here as an ImportError rather than silently at the buyer's first launch.
    $check = & "$pythonDir\python.exe" -c "import sys, psutil; print(sys.version.split()[0])" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Runtime smoke test failed: $check" }
    Write-Host "  [OK] Runtime verified: Python $check + psutil import OK"
    if ($voiceBaked) {
        $vcheck = & "$pythonDir\python.exe" -c "import numpy, soundfile, kokoro_onnx, faster_whisper; print('voice-ok')" 2>&1
        if ($LASTEXITCODE -ne 0) { throw "Voice smoke test failed (wheels installed but will not import - check msvcp140 bundling): $vcheck" }
        Write-Host "  [OK] Voice stack verified: numpy + soundfile + kokoro_onnx + faster_whisper import OK"
    }

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
