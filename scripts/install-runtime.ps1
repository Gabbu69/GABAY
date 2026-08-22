[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
$runtimeRoot = Join-Path $root '.runtime'
$runtimeExe = Join-Path $runtimeRoot 'GABAY\GABAY.exe'
$tagFile = Join-Path $runtimeRoot 'release-tag.txt'

$api = 'https://api.github.com/repos/Gabbu69/GABAY/releases/latest'
$headers = @{ 'User-Agent' = 'GABAY-Windows-Lite' }
$release = Invoke-RestMethod -Uri $api -Headers $headers -TimeoutSec 30
$currentTag = if (Test-Path -LiteralPath $tagFile) {
    (Get-Content -Raw -LiteralPath $tagFile).Trim()
} else { '' }
if (-not $Force -and (Test-Path -LiteralPath $runtimeExe) -and $currentTag -eq $release.tag_name) {
    Write-Host "GABAY runtime $currentTag is current."
    exit 0
}
$zipAsset = $release.assets | Where-Object { $_.name -eq 'GABAY-Windows-x64-Lite.zip' } | Select-Object -First 1
$hashAsset = $release.assets | Where-Object { $_.name -eq 'GABAY-Windows-x64-Lite.zip.sha256' } | Select-Object -First 1
if (-not $zipAsset -or -not $hashAsset) {
    throw 'The latest GitHub release does not contain the Windows Lite ZIP and checksum.'
}

$downloadRoot = Join-Path $root 'tmp\runtime-download'
New-Item -ItemType Directory -Path $downloadRoot -Force | Out-Null
$zipPath = Join-Path $downloadRoot $zipAsset.name
$hashPath = Join-Path $downloadRoot $hashAsset.name
Invoke-WebRequest -Uri $zipAsset.browser_download_url -Headers $headers -OutFile $zipPath -TimeoutSec 300
Invoke-WebRequest -Uri $hashAsset.browser_download_url -Headers $headers -OutFile $hashPath -TimeoutSec 60

$expected = ((Get-Content -Raw -LiteralPath $hashPath).Trim() -split '\s+')[0].ToLowerInvariant()
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
if ($expected -notmatch '^[0-9a-f]{64}$' -or $actual -ne $expected) {
    throw 'The downloaded Windows Lite runtime failed SHA-256 verification.'
}

$staging = Join-Path $root '.runtime-staging'
if (Test-Path -LiteralPath $staging) {
    $resolvedStaging = (Resolve-Path -LiteralPath $staging).Path
    if (-not $resolvedStaging.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Unsafe runtime staging path.'
    }
    Remove-Item -LiteralPath $resolvedStaging -Recurse -Force
}
New-Item -ItemType Directory -Path $staging | Out-Null
Expand-Archive -LiteralPath $zipPath -DestinationPath $staging
if (-not (Test-Path -LiteralPath (Join-Path $staging 'GABAY\GABAY.exe'))) {
    throw 'The verified archive does not contain GABAY.exe in the expected folder.'
}
$backup = Join-Path $root '.runtime-backup'
if (Test-Path -LiteralPath $backup) {
    $resolvedBackup = (Resolve-Path -LiteralPath $backup).Path
    if (-not $resolvedBackup.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Unsafe runtime backup path.'
    }
    Remove-Item -LiteralPath $resolvedBackup -Recurse -Force
}
if (Test-Path -LiteralPath $runtimeRoot) {
    Move-Item -LiteralPath $runtimeRoot -Destination $backup
}
try {
    Move-Item -LiteralPath $staging -Destination $runtimeRoot
    $release.tag_name | Set-Content -LiteralPath $tagFile -Encoding utf8
    if (Test-Path -LiteralPath $backup) {
        Remove-Item -LiteralPath $backup -Recurse -Force
    }
} catch {
    if (Test-Path -LiteralPath $runtimeRoot) {
        $resolvedRuntime = (Resolve-Path -LiteralPath $runtimeRoot).Path
        if ($resolvedRuntime.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedRuntime -Recurse -Force
        }
    }
    if (Test-Path -LiteralPath $backup) {
        Move-Item -LiteralPath $backup -Destination $runtimeRoot
    }
    throw
}
Write-Host "Installed verified GABAY runtime from release $($release.tag_name)." -ForegroundColor Green
