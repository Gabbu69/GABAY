[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root 'data\gabay.pid'
if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host 'GABAY Lite is not running.'
    exit 0
}

$gabayPid = [int](Get-Content -Raw -LiteralPath $pidFile)
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $gabayPid" -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host 'Removed a stale GABAY PID file.'
    exit 0
}

$command = [string]$process.CommandLine
if ($command -notmatch 'gabay\.launcher|gabay\.exe') {
    throw "PID $gabayPid does not belong to GABAY; it was not stopped."
}
Stop-Process -Id $gabayPid
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Host 'GABAY Lite stopped.' -ForegroundColor Green
