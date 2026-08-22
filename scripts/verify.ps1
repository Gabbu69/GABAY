[CmdletBinding()]
param(
    [switch]$SkipCoverage
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$ruff = Join-Path $root '.venv\Scripts\ruff.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Run scripts\setup-lite.ps1 before verification.'
}

Push-Location $root
try {
    & $python manage.py check
    if ($LASTEXITCODE -ne 0) { throw 'Django system check failed.' }
    & $python manage.py makemigrations --check --dry-run
    if ($LASTEXITCODE -ne 0) { throw 'Uncommitted model changes were detected.' }
    & $ruff check .
    if ($LASTEXITCODE -ne 0) { throw 'Ruff checks failed.' }
    if ($SkipCoverage) {
        & $python -m pytest
    } else {
        & $python -m coverage erase
        & $python -m coverage run -m pytest
        if ($LASTEXITCODE -eq 0) { & $python -m coverage report }
    }
    if ($LASTEXITCODE -ne 0) { throw 'Test verification failed.' }
    & $python manage.py collectstatic --noinput --clear
    if ($LASTEXITCODE -ne 0) { throw 'Static asset collection failed.' }
    Write-Host 'GABAY verification passed.' -ForegroundColor Green
} finally {
    Pop-Location
}
