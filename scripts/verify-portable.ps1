[CmdletBinding()]
param(
    [string]$ArchivePath = '',
    [int]$Port = 8891
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
if (-not $ArchivePath) {
    $ArchivePath = Join-Path $root 'dist\GABAY-Windows-x64-Lite.zip'
}
$archive = (Resolve-Path -LiteralPath $ArchivePath).Path
$checksumPath = "$archive.sha256"
if (Test-Path -LiteralPath $checksumPath) {
    $expectedHash = ((Get-Content -Raw -LiteralPath $checksumPath).Trim() -split '\s+')[0].ToLowerInvariant()
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "Portable archive checksum mismatch: expected $expectedHash, received $actualHash."
    }
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$scratchRoot = Join-Path $root "tmp\portable-smoke\$stamp-$PID"
$extractRoot = Join-Path $scratchRoot 'extracted'
$dataDir = Join-Path $scratchRoot 'data'
$logDir = Join-Path $scratchRoot 'logs'
New-Item -ItemType Directory -Path $extractRoot, $dataDir, $logDir -Force | Out-Null
Expand-Archive -LiteralPath $archive -DestinationPath $extractRoot
$executable = Get-ChildItem -LiteralPath $extractRoot -Filter 'GABAY.exe' -Recurse -File | Select-Object -First 1
if (-not $executable) {
    throw 'The portable archive does not contain GABAY.exe.'
}

$packagedCheck = Get-ChildItem -LiteralPath $extractRoot -Filter 'check-packaged-lite.ps1' -Recurse -File | Select-Object -First 1
if (-not $packagedCheck) {
    throw 'The portable archive does not contain the packaged Lite acceptance script.'
}
& $packagedCheck.FullName -ExecutablePath $executable.FullName -Port $Port
if ($LASTEXITCODE -ne 0) {
    throw 'The extracted portable acceptance check failed.'
}
$archiveMb = [math]::Round((Get-Item -LiteralPath $archive).Length / 1MB, 2)
Write-Host "Portable archive smoke test passed: $archiveMb MB." -ForegroundColor Green
