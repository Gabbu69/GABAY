[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
$dataDir = Join-Path $root 'data'
$backupDir = Join-Path $dataDir 'backups'
$resolvedBackup = (Resolve-Path -LiteralPath $BackupPath).Path
$resolvedBackupDir = if (Test-Path -LiteralPath $backupDir) {
    (Resolve-Path -LiteralPath $backupDir).Path
} else { throw 'No GABAY backup directory exists yet.' }

if (-not $resolvedBackup.StartsWith($resolvedBackupDir + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Choose a backup file from data\backups.'
}
if ([System.IO.Path]::GetExtension($resolvedBackup) -ne '.sqlite3') {
    throw 'The selected backup is not a GABAY SQLite backup.'
}

$pidFile = Join-Path $dataDir 'gabay.pid'
if (Test-Path -LiteralPath $pidFile) {
    $gabayPid = [int](Get-Content -Raw -LiteralPath $pidFile)
    if (Get-Process -Id $gabayPid -ErrorAction SilentlyContinue) {
        throw 'Stop GABAY before restoring a database backup.'
    }
}

New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
$database = Join-Path $dataDir 'gabay.sqlite3'
if (Test-Path -LiteralPath $database) {
    $safetyCopy = Join-Path $backupDir "gabay-before-restore-$(Get-Date -Format 'yyyyMMdd-HHmmss').sqlite3"
    Copy-Item -LiteralPath $database -Destination $safetyCopy
    Write-Host "Current database preserved at: $safetyCopy"
}
Copy-Item -LiteralPath $resolvedBackup -Destination $database -Force
foreach ($sidecar in @("$database-wal", "$database-shm")) {
    if (Test-Path -LiteralPath $sidecar) {
        Remove-Item -LiteralPath $sidecar -Force
    }
}
Write-Host "GABAY restored from: $resolvedBackup" -ForegroundColor Green
