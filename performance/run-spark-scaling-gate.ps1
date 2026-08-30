[CmdletBinding()]
param(
    [ValidateRange(3, 10)][int]$Repetitions = 3,
    [ValidateRange(1, 3)][int]$WarmupRuns = 1,
    [ValidateRange(1, 100)][int]$SampleBuckets = 20,
    [ValidateRange(1, 16)][int]$CoresPerWorker = 2,
    [ValidateRange(1, 16)][int]$WorkerMemoryGiB = 4,
    [ValidateRange(1024, 65535)][int]$MasterPort = 17077,
    [ValidateRange(1024, 65535)][int]$MasterUiPort = 18085
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$performanceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = Split-Path -Parent $performanceRoot
$temporaryRoot = Join-Path $performanceRoot '.tmp\spark-scaling'
$outputRoot = Join-Path $repositoryRoot 'outputs\spark-scaling'
$splitRoot = Join-Path $repositoryRoot 'outputs\recommendation-evidence\global-time-v1'
$trainPath = Join-Path $splitRoot 'train.parquet'
$validationPath = Join-Path $splitRoot 'validation.parquet'
$startedProcesses = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
$measuredPaths = @{
    1 = [System.Collections.Generic.List[string]]::new()
    2 = [System.Collections.Generic.List[string]]::new()
}

function Assert-PortAvailable([int]$Port) {
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $Port is already in use. Choose another port."
    }
}

function Wait-Port([int]$Port, [int]$TimeoutSeconds = 30) {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $client = [System.Net.Sockets.TcpClient]::new()
            $client.Connect('127.0.0.1', $Port)
            $client.Dispose()
            return
        } catch {
            Start-Sleep -Milliseconds 250
        }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Spark service did not listen on port $Port within $TimeoutSeconds seconds."
}

function Start-SparkClass([string]$SparkClass, [string[]]$Arguments, [string]$LogStem) {
    $stdout = Join-Path $temporaryRoot "$LogStem.stdout.log"
    $stderr = Join-Path $temporaryRoot "$LogStem.stderr.log"
    $process = Start-Process -FilePath $SparkClass -ArgumentList $Arguments `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr `
        -WindowStyle Hidden -PassThru
    $startedProcesses.Add($process)
    Start-Sleep -Milliseconds 750
    if ($process.HasExited) {
        throw "Spark process exited during startup: $LogStem"
    }
    return $process
}

function Stop-StartedProcesses {
    $all = @(Get-CimInstance Win32_Process)
    $known = @{}
    foreach ($process in $all) { $known[[int]$process.ProcessId] = $process }
    $targets = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($root in $startedProcesses) {
        [void]$targets.Add([int]$root.Id)
    }
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($process in $all) {
            if ($targets.Contains([int]$process.ParentProcessId) -and $targets.Add([int]$process.ProcessId)) {
                $changed = $true
            }
        }
    }
    foreach ($pidToStop in @($targets) | Sort-Object -Descending) {
        if ($known.ContainsKey($pidToStop)) {
            Stop-Process -Id $pidToStop -Force -ErrorAction SilentlyContinue
        }
    }
    $startedProcesses.Clear()
}

function Invoke-Benchmark([int]$Workers, [int]$Index, [bool]$Warmup) {
    $kind = if ($Warmup) { 'warmup' } else { 'run' }
    $topology = "${Workers}w-${kind}-${Index}"
    $output = Join-Path $outputRoot "$topology.json"
    & py -3.12 (Join-Path $repositoryRoot 'scripts\spark_als_scaling_benchmark.py') `
        --train $trainPath --validation $validationPath `
        --master "spark://127.0.0.1:$MasterPort" `
        --topology-id $topology --expected-workers $Workers `
        --sample-modulus 100 --sample-buckets $SampleBuckets `
        --input-partitions 32 --rank 16 --max-iter 3 --reg-param 0.1 --seed 42 `
        --driver-memory 6g --output $output
    if ($LASTEXITCODE -ne 0) { throw "Spark benchmark failed for $topology" }
    if (-not $Warmup) {
        $measuredPaths[$Workers].Add($output)
    }
}

if (-not (Test-Path $trainPath) -or -not (Test-Path $validationPath)) {
    throw 'REC-EV-001 Train/Validation Parquet is required under outputs/recommendation-evidence/global-time-v1.'
}
$pysparkRoot = (& py -3.12 -c "import pathlib,pyspark; print(pathlib.Path(pyspark.__file__).parent)").Trim()
if ($LASTEXITCODE -ne 0) { throw 'PySpark 4.2 runtime is unavailable.' }
$sparkClass = Join-Path $pysparkRoot 'bin\spark-class.cmd'
if (-not (Test-Path $sparkClass)) { throw "spark-class.cmd not found: $sparkClass" }

New-Item -ItemType Directory -Force -Path $temporaryRoot, $outputRoot | Out-Null
$requestedPorts = @($MasterPort, $MasterUiPort, $MasterUiPort + 10, $MasterUiPort + 11)
if (($requestedPorts | Where-Object { $_ -gt 65535 }).Count -ne 0) {
    throw 'MasterUiPort leaves no valid room for both worker UI ports.'
}
if (($requestedPorts | Sort-Object -Unique).Count -ne $requestedPorts.Count) {
    throw 'Master, master UI, and worker UI ports must be distinct.'
}
foreach ($port in $requestedPorts) {
    Assert-PortAvailable $port
}

Push-Location $repositoryRoot
try {
    foreach ($workerCount in @(1, 2)) {
        try {
            Start-SparkClass $sparkClass @(
                'org.apache.spark.deploy.master.Master', '--host', '127.0.0.1',
                '--port', "$MasterPort", '--webui-port', "$MasterUiPort"
            ) "master-${workerCount}w" | Out-Null
            Wait-Port $MasterPort
            for ($workerIndex = 0; $workerIndex -lt $workerCount; $workerIndex++) {
                $workerUi = $MasterUiPort + 10 + $workerIndex
                Start-SparkClass $sparkClass @(
                    'org.apache.spark.deploy.worker.Worker', "spark://127.0.0.1:$MasterPort",
                    '--cores', "$CoresPerWorker", '--memory', "${WorkerMemoryGiB}g",
                    '--webui-port', "$workerUi"
                ) "${workerCount}w-worker-$workerIndex" | Out-Null
            }
            Start-Sleep -Seconds 2
            for ($index = 1; $index -le $WarmupRuns; $index++) {
                Invoke-Benchmark $workerCount $index $true
            }
            for ($index = 1; $index -le $Repetitions; $index++) {
                Invoke-Benchmark $workerCount $index $false
            }
        } finally {
            Stop-StartedProcesses
            Start-Sleep -Seconds 2
        }
    }
    [string[]]$oneWorker = $measuredPaths[1]
    [string[]]$twoWorkers = $measuredPaths[2]
    $aggregatePath = Join-Path $outputRoot 'aggregate.json'
    & py -3.12 (Join-Path $repositoryRoot 'scripts\spark_als_scaling_aggregate.py') `
        --one-worker $oneWorker --two-workers $twoWorkers --output $aggregatePath `
        --minimum-speedup 1.20 --maximum-rmse-difference 0.01
    if ($LASTEXITCODE -ne 0) { throw 'Spark scaling aggregate failed.' }
    Write-Host "Spark scaling evidence written to $aggregatePath"
} finally {
    Stop-StartedProcesses
    Pop-Location
}
