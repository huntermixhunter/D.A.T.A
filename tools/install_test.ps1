# DATA - install-test gate
# ----------------------------------------------------------------------------
# Silently installs a freshly-built DATA-Setup-v<ver>.exe into a throwaway dir,
# then verifies the install is real and bootable. Exit 0 = PASS (safe to ship),
# non-zero = FAIL (do NOT ship). The provider task is deselected so the test is
# fast and offline (no Node download).
#
# Usage:  .\tools\install_test.ps1 1.0.32
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [int]$MinExeMB = 20
)
$ErrorActionPreference = "Stop"

# Sanitize an out-of-range PYTHONHASHSEED inherited from the host runtime (the
# live bridge sets a 64-bit value; embedded python only accepts "random" or a
# uint32). Without this the embedded runtime crashes on launch during the test.
$env:PYTHONHASHSEED = "0"

$root = (git rev-parse --show-toplevel 2>$null)
if (-not $root) { $root = Split-Path -Parent $PSScriptRoot }
$root = (Resolve-Path $root).Path

$exe = Join-Path $root "dist\DATA-Setup-v$Version.exe"
$fail = @()
function Check($name, $cond) {
    if ($cond) { Write-Host "  [PASS] $name" -ForegroundColor Green }
    else       { Write-Host "  [FAIL] $name" -ForegroundColor Red; $script:fail += $name }
}

Write-Host ""
Write-Host "  DATA install-test gate  -  v$Version" -ForegroundColor Cyan
Write-Host "  ---------------------------------------------------------------"

# --- 0. the .exe exists and is a real installer, not a stub ---
Check "installer exists" (Test-Path $exe)
if (-not (Test-Path $exe)) { Write-Host "  No installer to test." -ForegroundColor Red; exit 1 }
$exeMB = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "       size: $exeMB MB"
Check "installer >= $MinExeMB MB (not a stub)" ($exeMB -ge $MinExeMB)

# --- 1. silent install into a throwaway dir ---
$testDir = Join-Path $env:TEMP ("DATA_installtest_" + $Version)
if (Test-Path $testDir) { Remove-Item -Recurse -Force $testDir }
Write-Host "  [..] Silent install -> $testDir"
# /VERYSILENT no UI, /TASKS="" deselects optional tasks (no desktop icon, no provider/Node download)
$p = Start-Process -FilePath $exe -ArgumentList @(
    "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/NOCANCEL",
    "/TASKS=""""","/DIR=`"$testDir`"","/LOG=`"$testDir.log`""
) -Wait -PassThru
Check "installer exit code 0" ($p.ExitCode -eq 0)

# --- 2. the installed tree is real ---
$must = @(
    "dashboard\bridge_server.py",
    "dashboard\index.html",
    "dashboard\app.js",
    "runtime\python\python.exe",
    "start_data.vbs",
    "start_data.bat",
    ".env"                      # seeded from .env.example by the [Code] step
)
foreach ($rel in $must) { Check "installed: $rel" (Test-Path (Join-Path $testDir $rel)) }

# --- 3. .env must NOT carry secrets (clean retail seed) ---
$envFile = Join-Path $testDir ".env"
if (Test-Path $envFile) {
    # Ignore comment lines; flag only real key material or a sensitive key with a
    # non-empty value. An empty placeholder (e.g. DATA_BRIDGE_TOKEN=) is fine.
    $code = Get-Content $envFile | Where-Object { $_ -notmatch '^\s*#' }
    $leak = $code | Where-Object {
        $_ -match 'sk-ant-\S{10}' -or
        $_ -match 'ghp_\S{10}' -or
        $_ -match '^\s*(BLOB_READ_WRITE_TOKEN|CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|DATA_BRIDGE_TOKEN)\s*=\s*\S+'
    }
    Check ".env carries no live secrets" (-not $leak)
    if ($leak) { $leak | ForEach-Object { Write-Host "       leak: $_" -ForegroundColor DarkYellow } }
}

# --- 4. the embedded runtime actually runs ---
$py = Join-Path $testDir "runtime\python\python.exe"
if (Test-Path $py) {
    $ver = & $py -c "import sys;print(sys.version.split()[0])" 2>&1
    Write-Host "       embedded python: $ver"
    Check "embedded python executes" ($LASTEXITCODE -eq 0)

    # --- 5. boot test: bridge compiles AND all its imports resolve in the baked runtime ---
    #     (import-only; module-level code must not bind a port, so no clash with the live bridge)
    $bridge = Join-Path $testDir "dashboard\bridge_server.py"
    & $py -c "import py_compile; py_compile.compile(r'$bridge', doraise=True)" 2>&1 | Out-Null
    Check "bridge_server.py compiles" ($LASTEXITCODE -eq 0)

    $importProbe = "import importlib.util,sys; sys.path.insert(0,r'$(Join-Path $testDir 'dashboard')'); spec=importlib.util.spec_from_file_location('b',r'$bridge'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print('imports-ok')"
    $out = & $py -c $importProbe 2>&1
    if ($out -match "imports-ok") { Check "bridge imports resolve in baked runtime" $true }
    else { Check "bridge imports resolve in baked runtime" $false; Write-Host "       $out" -ForegroundColor DarkYellow }

    # --- 6. voice stack baked (Conversation Mode works on a fresh install) ---
    $voice = & $py -c "import faster_whisper; print('voice-ok')" 2>&1
    Check "voice stack (faster_whisper) baked in" ($voice -match "voice-ok")
}

# --- 7. cleanup ---
Write-Host "  [..] Cleaning up test install..."
try { Remove-Item -Recurse -Force $testDir -ErrorAction SilentlyContinue } catch {}
try { Remove-Item -Force "$testDir.log" -ErrorAction SilentlyContinue } catch {}

Write-Host ""
if ($fail.Count -eq 0) {
    Write-Host "  INSTALL-TEST: PASS  ($exeMB MB installer verified bootable)" -ForegroundColor Green
    exit 0
} else {
    Write-Host "  INSTALL-TEST: FAIL  ($($fail.Count) check(s) failed):" -ForegroundColor Red
    $fail | ForEach-Object { Write-Host "    - $_" -ForegroundColor Red }
    exit 1
}
