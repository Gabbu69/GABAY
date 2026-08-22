[CmdletBinding()]
param(
    [int]$Port = 8876
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
    [pscustomobject]@{
        ProcessIds = @($ids)
        WorkingSetBytes = $workingSet
    }
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

$root = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Run scripts\setup-lite.ps1 before Windows Lite verification.'
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$scratchRoot = Join-Path $root "tmp\lite-acceptance\$stamp"
$dataDir = Join-Path $scratchRoot 'data'
$logDir = Join-Path $scratchRoot 'logs'
$reportDir = Join-Path $root 'reports\runtime'
New-Item -ItemType Directory -Path $dataDir, $logDir, $reportDir -Force | Out-Null

$computer = Get-CimInstance Win32_ComputerSystem
$operatingSystem = Get-CimInstance Win32_OperatingSystem
$processor = Get-CimInstance Win32_Processor | Select-Object -First 1
$drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$((Split-Path -Qualifier $root).TrimEnd('\'))'"
$totalRamMb = [math]::Round($computer.TotalPhysicalMemory / 1MB)
$freeDiskMb = [math]::Round($drive.FreeSpace / 1MB)
$architectureOk = [Environment]::Is64BitOperatingSystem
$preflightOk = $architectureOk -and $totalRamMb -ge 3500 -and $freeDiskMb -ge 2048

$previous = @{}
foreach ($name in @('GABAY_PROFILE','GABAY_DATA_DIR','GABAY_PORT','GABAY_SECRET_KEY','GABAY_ALLOWED_HOSTS','GABAY_SKIP_FIRST_RUN_ADMIN','GABAY_NO_BROWSER','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

$env:GABAY_PROFILE = 'lite'
$env:GABAY_DATA_DIR = $dataDir
$env:GABAY_PORT = [string]$Port
$env:GABAY_SECRET_KEY = 'acceptance-only-secret-key-not-for-production'
$env:GABAY_ALLOWED_HOSTS = '127.0.0.1,localhost'
$env:GABAY_SKIP_FIRST_RUN_ADMIN = '1'
$env:GABAY_NO_BROWSER = '1'
$env:OMP_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'

$stdout = Join-Path $logDir 'server.out.log'
$stderr = Join-Path $logDir 'server.err.log'
$server = $null
$startupSeconds = $null
$routeResults = @()
$idleWorkingSetMb = $null
$forecastSeconds = $null
$forecastPeakWorkingSetMb = $null
$forecastPassed = $false

try {
    $migrationLog = Join-Path $logDir 'migration.log'
    & $python manage.py migrate --noinput *> $migrationLog
    if ($LASTEXITCODE -ne 0) { throw 'Fresh Lite migration failed.' }
    & $python manage.py seed_demo_prices *>> $migrationLog
    if ($LASTEXITCODE -ne 0) { throw 'Demo fixture setup failed.' }

    $forecastStdout = Join-Path $logDir 'forecast.out.log'
    $forecastStderr = Join-Path $logDir 'forecast.err.log'
    $forecastWatch = [System.Diagnostics.Stopwatch]::StartNew()
    $forecastArguments = "/d /s /c `"`"$python`" manage.py run_forecast demo-mlang-palay-other-dry --time-limit 180`""
    $forecastProcess = Start-Process -FilePath $env:ComSpec -ArgumentList $forecastArguments -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $forecastStdout -RedirectStandardError $forecastStderr -PassThru
    # Cache the native handle before the fast worker exits; otherwise Windows
    # PowerShell 5.1 can leave ExitCode unset on a redirected process.
    $null = $forecastProcess.Handle
    $peakBytes = 0L
    while (-not $forecastProcess.HasExited -and $forecastWatch.Elapsed.TotalSeconds -le 200) {
        $forecastProcess.Refresh()
        $forecastSnapshot = Get-ProcessTreeSnapshot -RootProcessId $forecastProcess.Id
        if ($forecastSnapshot.WorkingSetBytes -gt $peakBytes) {
            $peakBytes = $forecastSnapshot.WorkingSetBytes
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not $forecastProcess.HasExited) {
        Stop-ProcessTree -RootProcess $forecastProcess
        throw 'Forecast worker exceeded the 200-second acceptance timeout.'
    }
    # Start-Process can report HasExited before redirected streams and ExitCode
    # are fully synchronized. WaitForExit() makes the acceptance result
    # deterministic and ensures the complete logs are available on failure.
    $forecastProcess.WaitForExit()
    $forecastProcess.Refresh()
    $forecastWatch.Stop()
    $forecastSeconds = [math]::Round($forecastWatch.Elapsed.TotalSeconds, 3)
    $forecastPeakWorkingSetMb = [math]::Round($peakBytes / 1MB, 2)
    $forecastPassed = $forecastProcess.ExitCode -eq 0 -and $forecastSeconds -le 180 -and $forecastPeakWorkingSetMb -le 450
    if (-not $forecastPassed) {
        $forecastError = if (Test-Path -LiteralPath $forecastStderr) { Get-Content -Raw -LiteralPath $forecastStderr } else { '' }
        throw "Forecast acceptance failed (exit=$($forecastProcess.ExitCode), seconds=$forecastSeconds, peak_mb=$forecastPeakWorkingSetMb). $forecastError"
    }

    $serverStartedAt = Get-Date
    $serverArguments = "/d /s /c `"`"$python`" -m gabay.launcher`""
    $server = Start-Process -FilePath $env:ComSpec -ArgumentList $serverArguments -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    $null = $server.Handle
    $healthUri = "http://127.0.0.1:$Port/health/"
    $ready = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        if ($server.HasExited) { break }
        try {
            $health = Invoke-WebRequest -Uri $healthUri -UseBasicParsing -TimeoutSec 1
            if ($health.StatusCode -eq 200) { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) {
        $errorLog = if (Test-Path -LiteralPath $stderr) { Get-Content -Raw -LiteralPath $stderr } else { '' }
        throw "GABAY Lite did not become healthy within 60 seconds. $errorLog"
    }

    $startupSeconds = [math]::Round(((Get-Date) - $serverStartedAt).TotalSeconds, 3)
    Start-Sleep -Seconds 2
    $server.Refresh()
    $serverSnapshot = Get-ProcessTreeSnapshot -RootProcessId $server.Id
    $idleWorkingSetMb = [math]::Round($serverSnapshot.WorkingSetBytes / 1MB, 2)

    foreach ($path in @('/', '/history/', '/methodology/', '/check-offer/', '/offline/', '/service-worker.js')) {
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
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}

$routesOk = ($routeResults | Where-Object { -not $_.ok }).Count -eq 0
$combinedWorkingSetEstimateMb = [math]::Round($idleWorkingSetMb + $forecastPeakWorkingSetMb, 2)
$budgetsOk = $startupSeconds -le 60 -and $idleWorkingSetMb -le 180 -and $forecastPassed -and $combinedWorkingSetEstimateMb -le 600
$isDeclaredTarget = $computer.Manufacturer -match 'Lenovo' -and $processor.Name -match 'Pentium' -and $totalRamMb -le 5120
$report = [ordered]@{
    generated_at = (Get-Date).ToString('o')
    computer = [ordered]@{
        manufacturer = $computer.Manufacturer
        model = $computer.Model
        processor = $processor.Name
        processor_architecture = $env:PROCESSOR_ARCHITECTURE
        operating_system = $operatingSystem.Caption
        operating_system_version = $operatingSystem.Version
        total_ram_mb = $totalRamMb
        free_disk_mb = $freeDiskMb
        is_64_bit = $architectureOk
    }
    measurements = [ordered]@{
        startup_seconds = $startupSeconds
        idle_server_working_set_mb = $idleWorkingSetMb
        forecast_seconds = $forecastSeconds
        forecast_peak_working_set_mb = $forecastPeakWorkingSetMb
        conservative_combined_working_set_mb = $combinedWorkingSetEstimateMb
        routes = $routeResults
    }
    limits = [ordered]@{
        minimum_ram_mb = 3500
        minimum_free_disk_mb = 2048
        maximum_startup_seconds = 60
        maximum_idle_server_working_set_mb = 180
        maximum_forecast_worker_peak_mb = 450
        maximum_combined_working_set_mb = 600
    }
    result = [ordered]@{
        preflight_passed = $preflightOk
        routes_passed = $routesOk
        measured_budgets_passed = $budgetsOk
        overall_passed = $preflightOk -and $routesOk -and $budgetsOk
        forecast_budget = if ($forecastPassed) { 'PASSED on this machine' } else { 'FAILED on this machine' }
        physical_lenovo_acceptance = if ($isDeclaredTarget) { 'ASSESSED on a Lenovo Pentium system with at most 5 GB RAM' } else { 'UNASSESSED - rerun this script on the target Lenovo Pentium 4 GB laptop' }
    }
}

$reportPath = Join-Path $reportDir "lite-acceptance-$stamp.json"
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding utf8
Write-Host "Acceptance report: $reportPath"
if (-not $report.result.overall_passed) {
    throw 'Windows Lite acceptance checks did not all pass. Review the report and logs.'
}
Write-Host 'Measured Windows Lite web and forecast checks passed on this machine. Target-Lenovo acceptance is reported separately.' -ForegroundColor Green
