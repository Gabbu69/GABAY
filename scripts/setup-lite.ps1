[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root '.venv\Scripts\python.exe'

function Test-Python312x64([string]$Executable, [string[]]$PrefixArgs = @()) {
    try {
        $runtime = & $Executable @PrefixArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}|{64 if sys.maxsize > 2**32 else 32}')" 2>$null
        return $runtime -eq '3.12|64'
    } catch {
        return $false
    }
}

if (Test-Path -LiteralPath $venvPython) {
    if (-not (Test-Python312x64 $venvPython)) {
        throw 'The existing .venv is not 64-bit Python 3.12. Remove only .venv and rerun setup.'
    }
} else {
    $created = $false
    if ($env:GABAY_PYTHON -and (Test-Python312x64 $env:GABAY_PYTHON)) {
        & $env:GABAY_PYTHON -m venv (Join-Path $root '.venv')
        $created = $true
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            & py -3.12 -m venv (Join-Path $root '.venv')
            $created = $LASTEXITCODE -eq 0
        } catch { $created = $false }
    }
    if (-not $created -and (Get-Command python -ErrorAction SilentlyContinue) -and (Test-Python312x64 'python')) {
        & python -m venv (Join-Path $root '.venv')
        $created = $true
    }
    if (-not $created) {
        throw 'Python 3.12 x64 was not found. Use the GABAY Windows Lite release, or set GABAY_PYTHON to a Python 3.12 executable.'
    }
}

& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host 'GABAY Lite dependencies are ready.' -ForegroundColor Green
