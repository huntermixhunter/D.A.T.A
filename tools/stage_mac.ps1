# Windows preview of the committed Mac payload. Does not claim to execute macOS.
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
    [ValidateSet('arm64', 'x86_64')][string]$Architecture = 'arm64'
)
$ErrorActionPreference = 'Stop'
$gitCommand = (Get-Command git -ErrorAction Stop).Source
$gitRoot = Split-Path -Parent (Split-Path -Parent $gitCommand)
$bashPath = Join-Path $gitRoot 'bin\bash.exe'
if (-not (Test-Path -LiteralPath $bashPath)) {
    throw 'Git for Windows with Git Bash is required.'
}
$buildScript = (Join-Path $PSScriptRoot 'build_dmg.sh').Replace('\', '/')
& $bashPath $buildScript $Version --arch $Architecture --stage-only
if ($LASTEXITCODE -ne 0) { throw "Mac staging failed with exit code $LASTEXITCODE" }
