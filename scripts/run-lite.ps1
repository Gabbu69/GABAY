[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runtimeExe = Join-Path $root '.runtime\GABAY\gabay.exe'
$venvPython = Join-Path $root '.venv\Scripts\python.exe'

# Keep project data outside the replaceable downloaded runtime.  This also
# makes the same Start/Stop scripts work for both source and portable modes.
$env:GABAY_PROFILE = 'lite'
$env:GABAY_DATA_DIR = Join-Path $root 'data'
$env:OMP_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'

try {
    & (Join-Path $PSScriptRoot 'install-runtime.ps1')
} catch {
    if (Test-Path -LiteralPath $runtimeExe) {
        Write-Warning "Could not check for a runtime update; using the installed copy: $($_.Exception.Message)"
    } else {
        Write-Warning "Portable runtime download was unavailable: $($_.Exception.Message)"
    }
}

if (Test-Path -LiteralPath $runtimeExe) {
    & $runtimeExe
    exit $LASTEXITCODE
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    & (Join-Path $PSScriptRoot 'setup-lite.ps1')
}

& $venvPython -m gabay.launcher
exit $LASTEXITCODE
