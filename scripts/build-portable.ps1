[CmdletBinding()]
param(
    [switch]$SkipVerification
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$dist = Join-Path $root 'dist'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Run scripts\setup-lite.ps1 before building the portable release.'
}

Push-Location $root
try {
    if (-not $SkipVerification) {
        & (Join-Path $PSScriptRoot 'verify.ps1') -SkipCoverage
        if ($LASTEXITCODE -ne 0) { throw 'Verification failed; portable build was not created.' }
    }
    & $python -m PyInstaller --noconfirm --clean (Join-Path $root 'packaging\gabay.spec')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

    $portableRoot = Join-Path $dist 'GABAY'
    $portableScripts = Join-Path $portableRoot 'scripts'
    New-Item -ItemType Directory -Path $portableScripts -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $root 'Stop-GABAY-Lite.cmd') -Destination $portableRoot
    Copy-Item -LiteralPath (Join-Path $root 'Restore-GABAY-Backup.cmd') -Destination $portableRoot
    Copy-Item -LiteralPath (Join-Path $root 'Check-GABAY-Lite.cmd') -Destination $portableRoot
    Copy-Item -LiteralPath (Join-Path $root 'scripts\stop-lite.ps1') -Destination $portableScripts
    Copy-Item -LiteralPath (Join-Path $root 'scripts\restore-lite-backup.ps1') -Destination $portableScripts
    Copy-Item -LiteralPath (Join-Path $root 'scripts\check-packaged-lite.ps1') -Destination $portableScripts
    Copy-Item -LiteralPath (Join-Path $root 'README.md') -Destination $portableRoot

    $zip = Join-Path $dist 'GABAY-Windows-x64-Lite.zip'
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    Compress-Archive -LiteralPath $portableRoot -DestinationPath $zip -CompressionLevel Optimal
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
    $hashFile = "$zip.sha256"
    "$hash  GABAY-Windows-x64-Lite.zip" | Set-Content -LiteralPath $hashFile -Encoding ascii
    Write-Host "Portable release: $zip" -ForegroundColor Green
    Write-Host "Checksum file: $hashFile"
    Write-Host "SHA-256: $hash"
    & (Join-Path $PSScriptRoot 'verify-portable.ps1') -ArchivePath $zip
    if ($LASTEXITCODE -ne 0) { throw 'Portable artifact smoke test failed.' }
} finally {
    Pop-Location
}
