[CmdletBinding()]
param(
    [string]$ExecutablePath = '',
    [int]$Port = 8891
)

$ErrorActionPreference = 'Stop'

function Get-ProcessTreeSnapshot {
    param([Parameter(Mandatory)][int]$RootProcessId)

    $processes = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, WorkingSetSize)
    $ids = [System.Collections.Generic.HashSet[int]]::new()
    [void]$ids.Add($RootProcessId)
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($process in $processes) {
            if ($ids.Contains([int]$process.ParentProcessId) -and $ids.Add([int]$process.ProcessId)) {
                $changed = $true
            }
        }
    }
    $workingSet = 0L
    foreach ($process in $processes) {
        if ($ids.Contains([int]$process.ProcessId)) {
            $workingSet += [long]$process.WorkingSetSize
        }
    }
    [pscustomobject]@{ ProcessIds = @($ids); WorkingSetBytes = $workingSet }
}

function Stop-ProcessTree {
    param([Parameter(Mandatory)][System.Diagnostics.Process]$RootProcess)

    if ($RootProcess.HasExited) { return }
    $snapshot = Get-ProcessTreeSnapshot -RootProcessId $RootProcess.Id
    foreach ($processId in ($snapshot.ProcessIds | Where-Object { $_ -ne $RootProcess.Id })) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
    Stop-Process -Id $RootProcess.Id -Force -ErrorAction SilentlyContinue
}

$packageRoot = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
if (-not $ExecutablePath) {
    $ExecutablePath = Join-Path $packageRoot 'GABAY.exe'
}
$executable = (Resolve-Path -LiteralPath $ExecutablePath).Path
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$scratchRoot = Join-Path $env:TEMP "GABAY-Lite-acceptance-$stamp-$PID"
$dataDir = Join-Path $scratchRoot 'data'
$logDir = Join-Path $scratchRoot 'logs'
$reportDir = Join-Path $packageRoot 'reports'
New-Item -ItemType Directory -Path $dataDir, $logDir, $reportDir -Force | Out-Null

$computer = Get-CimInstance Win32_ComputerSystem
$operatingSystem = Get-CimInstance Win32_OperatingSystem
$processor = Get-CimInstance Win32_Processor | Select-Object -First 1
$driveLetter = (Split-Path -Qualifier $packageRoot).TrimEnd('\')
$drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$driveLetter'"
$totalRamMb = [math]::Round($computer.TotalPhysicalMemory / 1MB)
$freeDiskMb = [math]::Round($drive.FreeSpace / 1MB)
$architectureOk = [Environment]::Is64BitOperatingSystem
$preflightOk = $architectureOk -and $totalRamMb -ge 3500 -and $freeDiskMb -ge 2048

$environmentNames = @(
    'GABAY_PROFILE', 'GABAY_DATA_DIR', 'GABAY_PORT', 'GABAY_SECRET_KEY',
    'GABAY_ALLOWED_HOSTS', 'GABAY_SKIP_FIRST_RUN_ADMIN', 'GABAY_NO_BROWSER',
    'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'
)
$previous = @{}
foreach ($name in $environmentNames) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

$env:GABAY_PROFILE = 'lite'
$env:GABAY_DATA_DIR = $dataDir
$env:GABAY_PORT = [string]$Port
$env:GABAY_SECRET_KEY = 'packaged-acceptance-only-secret-not-for-production'
$env:GABAY_ALLOWED_HOSTS = '127.0.0.1,localhost'
$env:GABAY_SKIP_FIRST_RUN_ADMIN = '1'
$env:GABAY_NO_BROWSER = '1'
$env:OMP_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'

$server = $null
$startupSeconds = $null
$idleWorkingSetMb = $null
$forecastSeconds = $null
$forecastPeakWorkingSetMb = $null
$forecastPassed = $false
$routeResults = @()
try {
    $fixtureLog = Join-Path $logDir 'fixture.log'
    & $executable --forecast-worker migrate --noinput *> $fixtureLog
    if ($LASTEXITCODE -ne 0) { throw 'Frozen migration command failed.' }
    & $executable --forecast-worker seed_demo_prices *>> $fixtureLog
    if ($LASTEXITCODE -ne 0) { throw 'Frozen demo fixture command failed.' }

    $forecastOut = Join-Path $logDir 'forecast.out.log'
    $forecastErr = Join-Path $logDir 'forecast.err.log'
    $forecastWatch = [System.Diagnostics.Stopwatch]::StartNew()
    $forecast = Start-Process -FilePath $executable -ArgumentList @(
        '--forecast-worker', 'run_forecast', 'demo-mlang-palay-other-dry',
        '--simulation', '--time-limit', '180'
    ) -WorkingDirectory (Split-Path -Parent $executable) -WindowStyle Hidden -RedirectStandardOutput $forecastOut -RedirectStandardError $forecastErr -PassThru
    $null = $forecast.Handle
    $peakBytes = 0L
    while (-not $forecast.HasExited -and $forecastWatch.Elapsed.TotalSeconds -le 200) {
        $snapshot = Get-ProcessTreeSnapshot -RootProcessId $forecast.Id
        if ($snapshot.WorkingSetBytes -gt $peakBytes) { $peakBytes = $snapshot.WorkingSetBytes }
        Start-Sleep -Milliseconds 250
    }
    if (-not $forecast.HasExited) {
        Stop-ProcessTree -RootProcess $forecast
        throw 'Frozen forecast worker exceeded the 200-second acceptance timeout.'
    }
    $forecast.WaitForExit()
    $forecastWatch.Stop()
    $forecastSeconds = [math]::Round($forecastWatch.Elapsed.TotalSeconds, 3)
    $forecastPeakWorkingSetMb = [math]::Round($peakBytes / 1MB, 2)
    $forecastOutput = if (Test-Path -LiteralPath $forecastOut) { Get-Content -Raw -LiteralPath $forecastOut } else { '' }
    $forecastError = if (Test-Path -LiteralPath $forecastErr) { Get-Content -Raw -LiteralPath $forecastErr } else { '' }
    $forecastPassed = $forecast.ExitCode -eq 0 -and $forecastOutput -match 'GABAY_MODEL_RUN_ID=' -and $forecastSeconds -le 180 -and $forecastPeakWorkingSetMb -le 450
    if (-not $forecastPassed) {
        throw "Frozen forecast failed (exit=$($forecast.ExitCode), seconds=$forecastSeconds, peak_mb=$forecastPeakWorkingSetMb). $forecastError"
    }

    $serverOut = Join-Path $logDir 'server.out.log'
    $serverErr = Join-Path $logDir 'server.err.log'
    $serverStartedAt = Get-Date
    $server = Start-Process -FilePath $executable -WorkingDirectory (Split-Path -Parent $executable) -WindowStyle Hidden -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr -PassThru
    $null = $server.Handle
    $healthUri = "http://127.0.0.1:$Port/health/"
    $ready = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        if ($server.HasExited) { break }
        try {
            $health = Invoke-WebRequest -Uri $healthUri -UseBasicParsing -TimeoutSec 1
            if ($health.StatusCode -eq 200) { $ready = $true; break }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) {
        $serverError = if (Test-Path -LiteralPath $serverErr) { Get-Content -Raw -LiteralPath $serverErr } else { '' }
        throw "Frozen GABAY did not become healthy within 60 seconds. $serverError"
    }
    $startupSeconds = [math]::Round(((Get-Date) - $serverStartedAt).TotalSeconds, 3)
    Start-Sleep -Seconds 2
    $serverSnapshot = Get-ProcessTreeSnapshot -RootProcessId $server.Id
    $idleWorkingSetMb = [math]::Round($serverSnapshot.WorkingSetBytes / 1MB, 2)

    foreach ($path in @(
        '/', '/history/', '/methodology/', '/check-offer/', '/offline/',
        '/service-worker.js', '/static/css/gabay.css', '/static/images/gabay-palay-hero.webp'
    )) {
        $watch = [System.Diagnostics.Stopwatch]::StartNew()
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port$path" -UseBasicParsing -TimeoutSec 10
            $watch.Stop()
            $routeResults += [ordered]@{ path=$path; status=[int]$response.StatusCode; milliseconds=[math]::Round($watch.Elapsed.TotalMilliseconds, 1); ok=$response.StatusCode -eq 200 }
        } catch {
            $watch.Stop()
            $routeResults += [ordered]@{ path=$path; status=0; milliseconds=[math]::Round($watch.Elapsed.TotalMilliseconds, 1); ok=$false; error=$_.Exception.Message }
        }
    }
} finally {
    if ($server -and -not $server.HasExited) {
        Stop-ProcessTree -RootProcess $server
        $server.WaitForExit(5000) | Out-Null
    }
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}

$routesOk = ($routeResults | Where-Object { -not $_.ok }).Count -eq 0
$combinedWorkingSetEstimateMb = [math]::Round($idleWorkingSetMb + $forecastPeakWorkingSetMb, 2)
$budgetsOk = $startupSeconds -le 60 -and $idleWorkingSetMb -le 180 -and $forecastPassed -and $combinedWorkingSetEstimateMb -le 600
$isDeclaredTarget = $computer.Manufacturer -match 'Lenovo' -and $processor.Name -match 'Pentium' -and $totalRamMb -le 5120
$report = [ordered]@{
    generated_at = (Get-Date).ToString('o')
    artifact = [ordered]@{ executable=$executable; sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $executable).Hash.ToLowerInvariant() }
    computer = [ordered]@{
        manufacturer=$computer.Manufacturer; model=$computer.Model; processor=$processor.Name
        operating_system=$operatingSystem.Caption; operating_system_version=$operatingSystem.Version
        total_ram_mb=$totalRamMb; free_disk_mb=$freeDiskMb; is_64_bit=$architectureOk
    }
    measurements = [ordered]@{
        startup_seconds=$startupSeconds; idle_server_working_set_mb=$idleWorkingSetMb
        forecast_seconds=$forecastSeconds; forecast_peak_working_set_mb=$forecastPeakWorkingSetMb
        conservative_combined_working_set_mb=$combinedWorkingSetEstimateMb; routes=$routeResults
    }
    result = [ordered]@{
        preflight_passed=$preflightOk; routes_passed=$routesOk; measured_budgets_passed=$budgetsOk
        overall_passed=$preflightOk -and $routesOk -and $budgetsOk
        physical_lenovo_acceptance=if ($isDeclaredTarget) { 'ASSESSED on a Lenovo Pentium system with at most 5 GB RAM' } else { 'UNASSESSED - rerun Check-GABAY-Lite.cmd on the target Lenovo Pentium 4 GB laptop' }
    }
    scratch_evidence = $scratchRoot
}
$reportPath = Join-Path $reportDir "packaged-lite-acceptance-$stamp.json"
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding utf8
Write-Host "Acceptance report: $reportPath"
if (-not $report.result.overall_passed) {
    throw 'Packaged Windows Lite acceptance checks did not all pass. Review the report.'
}
Write-Host "Packaged GABAY Lite passed: $forecastSeconds s forecast, $forecastPeakWorkingSetMb MB forecast peak, $startupSeconds s startup, $idleWorkingSetMb MB idle." -ForegroundColor Green
Write-Host $report.result.physical_lenovo_acceptance
