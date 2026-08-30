[CmdletBinding()]
param(
    [ValidateRange(100, 5000)]
    [int]$Requests = 200,

    [ValidateRange(1, 100)]
    [int]$WarmupRequests = 10,

    [ValidateRange(1024, 65535)]
    [int]$DatabasePort = 55432,

    [ValidateRange(1024, 65535)]
    [int]$BackendPort = 18081
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$performanceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = Split-Path -Parent $performanceRoot
$seedPath = Join-Path $performanceRoot 'seed-87585.sql'
$temporaryRoot = Join-Path $performanceRoot '.tmp'
$resultRoot = Join-Path $performanceRoot 'results'
$containerName = "feelm-catalog-perf-$PID"
$databaseName = 'feelm_performance'
$databaseUser = 'feelm_performance'
$databasePassword = 'feelm_performance_local'
$backendProcess = $null

function Assert-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command is unavailable: $Name"
    }
}

function Assert-PortAvailable([int]$Port) {
    $existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($existing) {
        throw "Port $Port is already in use. Choose another port parameter."
    }
}

function Invoke-Docker([string[]]$DockerArguments) {
    & docker @DockerArguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker command failed: docker $($DockerArguments -join ' ')"
    }
}

function Wait-Database([string]$Name, [int]$TimeoutSeconds = 60) {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        & docker exec $Name pg_isready -U $databaseUser -d $databaseName *> $null
        if ($LASTEXITCODE -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "PostgreSQL did not become ready within $TimeoutSeconds seconds."
}

function Wait-Http([string]$Url, [int]$TimeoutSeconds = 120) {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                return
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "HTTP endpoint did not become ready within $TimeoutSeconds seconds: $Url"
}

function Invoke-Api([System.Net.Http.HttpClient]$Client, [string]$Path) {
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $response = $Client.GetAsync($Path).GetAwaiter().GetResult()
        $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        $stopwatch.Stop()
        $status = [int]$response.StatusCode
        $response.Dispose()
        return [pscustomobject]@{
            Milliseconds = $stopwatch.Elapsed.TotalMilliseconds
            StatusCode = $status
            Body = $body
            Error = $null
        }
    } catch {
        $stopwatch.Stop()
        return [pscustomobject]@{
            Milliseconds = $stopwatch.Elapsed.TotalMilliseconds
            StatusCode = 0
            Body = ''
            Error = $_.Exception.Message
        }
    }
}

function Get-Percentile([double[]]$Sorted, [double]$Percentile) {
    $index = [Math]::Max(0, [Math]::Ceiling($Percentile * $Sorted.Count) - 1)
    return [Math]::Round($Sorted[$index], 3)
}

function Measure-Endpoint(
    [System.Net.Http.HttpClient]$Client,
    [string]$Name,
    [string]$Path,
    [int]$Iterations,
    [int]$Warmups
) {
    for ($index = 0; $index -lt $Warmups; $index++) {
        $warmup = Invoke-Api -Client $Client -Path $Path
        if ($warmup.StatusCode -ne 200) {
            throw "$Name warmup failed with HTTP $($warmup.StatusCode): $($warmup.Error)"
        }
    }

    $durations = [System.Collections.Generic.List[double]]::new()
    $errors = 0
    for ($index = 0; $index -lt $Iterations; $index++) {
        $sample = Invoke-Api -Client $Client -Path $Path
        $durations.Add($sample.Milliseconds)
        if ($sample.StatusCode -ne 200) {
            $errors++
        }
    }
    [double[]]$sorted = $durations.ToArray() | Sort-Object
    return [ordered]@{
        name = $Name
        path = $Path
        requests = $Iterations
        errors = $errors
        minMs = [Math]::Round($sorted[0], 3)
        p50Ms = Get-Percentile -Sorted $sorted -Percentile 0.50
        p95Ms = Get-Percentile -Sorted $sorted -Percentile 0.95
        p99Ms = Get-Percentile -Sorted $sorted -Percentile 0.99
        maxMs = [Math]::Round($sorted[-1], 3)
        meanMs = [Math]::Round(($durations | Measure-Object -Average).Average, 3)
    }
}

function Write-MarkdownResult([System.Collections.IDictionary]$Result, [string]$Path) {
    $lines = @(
        '# C0 Catalog 87,585편 Performance Gate',
        '',
        "> 측정 시각(UTC): $($Result.measuredAtUtc)",
        "> Git commit: $($Result.gitCommit)",
        "> Working tree dirty: $($Result.gitWorkingTreeDirty)",
        '',
        "- Dataset: $($Result.dataset.movieCount)편, active catalog $($Result.dataset.catalogVersion)",
        "- Backend: Spring Boot postgres profile, 별도 포트 $($Result.environment.backendPort)",
        "- PostgreSQL: 17.6-alpine ephemeral container, 별도 포트 $($Result.environment.databasePort), volume 없음",
        "- Warm 반복: endpoint당 $($Result.parameters.requests)회, 사전 warmup $($Result.parameters.warmupRequests)회",
        "- 오류: $($Result.summary.totalErrors)",
        '',
        '| Scenario | p50 | p95 | p99 | max | Gate |',
        '| --- | ---: | ---: | ---: | ---: | --- |'
    )
    foreach ($metric in $Result.metrics) {
        $gateText = if ($metric.gateTargetMs) {
            if ($metric.gatePassed) { "PASS (≤ $($metric.gateTargetMs)ms)" } else { "FAIL (≤ $($metric.gateTargetMs)ms)" }
        } else {
            '관찰값'
        }
        $lines += "| $($metric.name) | $($metric.p50Ms)ms | $($metric.p95Ms)ms | $($metric.p99Ms)ms | $($metric.maxMs)ms | $gateText |"
    }
    $lines += @(
        '',
        "- Initial cache build: $($Result.initialCacheBuild.milliseconds)ms, HTTP $($Result.initialCacheBuild.statusCode), items $($Result.initialCacheBuild.itemCount)",
        "- 최종 판정: **$($Result.summary.status)**",
        '',
        '초기 요청은 PostgreSQL에서 active version 전체 projection을 읽어 immutable in-memory snapshot을 만드는 시간을 포함한다.',
        'Warm 요청은 매번 active version UUID를 확인한 뒤 현재 in-memory 선형 검색·정렬 경로를 사용한다.'
    )
    if ($Result.summary.status -ne 'PASS') {
        $lines += @(
            '',
            '현재 Gate 실패 시 전체 projection 메모리 선형 검색을 채택하지 않는다. PostgreSQL의 `search_vector`/정렬 index를',
            '직접 사용하는 query port로 전환하고 같은 harness를 다시 실행해야 한다.'
        )
    }
    Set-Content -LiteralPath $Path -Value ($lines -join "`n") -Encoding utf8
}

Assert-Command 'docker'
Assert-Command 'java'
Assert-PortAvailable -Port $DatabasePort
Assert-PortAvailable -Port $BackendPort

New-Item -ItemType Directory -Force -Path $temporaryRoot, $resultRoot | Out-Null
$runRoot = Join-Path $temporaryRoot (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
$stdoutPath = Join-Path $runRoot 'backend.stdout.log'
$stderrPath = Join-Path $runRoot 'backend.stderr.log'

try {
    Push-Location $repositoryRoot
    try {
        & .\backend\gradlew.bat -p backend --no-daemon bootJar
        if ($LASTEXITCODE -ne 0) {
            throw 'Backend bootJar build failed.'
        }
    } finally {
        Pop-Location
    }

    $jar = Get-ChildItem -LiteralPath (Join-Path $repositoryRoot 'backend\build\libs') -Filter '*.jar' |
        Where-Object { $_.Name -notlike '*-plain.jar' } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $jar) {
        throw 'Backend executable jar was not produced.'
    }

    Invoke-Docker @(
        'run', '--detach', '--name', $containerName,
        '--publish', "127.0.0.1:${DatabasePort}:5432",
        '--env', "POSTGRES_DB=$databaseName",
        '--env', "POSTGRES_USER=$databaseUser",
        '--env', "POSTGRES_PASSWORD=$databasePassword",
        'postgres:17.6-alpine'
    )
    Wait-Database -Name $containerName

    $backendArguments = @(
        '-Xms512m', '-Xmx2048m', '-jar', $jar.FullName,
        '--spring.profiles.active=postgres',
        "--server.port=$BackendPort",
        "--spring.datasource.url=jdbc:postgresql://127.0.0.1:${DatabasePort}/$databaseName",
        "--spring.datasource.username=$databaseUser",
        "--spring.datasource.password=$databasePassword",
        '--catalog.fixed-clock=2026-08-29T12:00:00Z',
        '--catalog.auth-mode=fake'
    )
    $backendProcess = Start-Process -FilePath 'java' -ArgumentList $backendArguments -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    Wait-Http -Url "http://127.0.0.1:${BackendPort}/actuator/health"

    Invoke-Docker @('cp', $seedPath, "${containerName}:/tmp/seed-87585.sql")
    Invoke-Docker @(
        'exec', $containerName, 'psql', '--username', $databaseUser, '--dbname', $databaseName,
        '--file', '/tmp/seed-87585.sql'
    )

    $handler = [System.Net.Http.SocketsHttpHandler]::new()
    $handler.PooledConnectionLifetime = [TimeSpan]::FromMinutes(5)
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.BaseAddress = [Uri]"http://127.0.0.1:${BackendPort}"
    $client.Timeout = [TimeSpan]::FromMinutes(3)
    try {
        $initial = Invoke-Api -Client $client -Path '/api/v1/movies?query=needle&limit=20'
        $initialItems = 0
        if ($initial.StatusCode -eq 200) {
            $initialPayload = $initial.Body | ConvertFrom-Json
            $initialItems = @($initialPayload.items).Count
        }

        $metrics = @(
            Measure-Endpoint -Client $client -Name 'search-query-20' -Path '/api/v1/movies?query=needle&limit=20' -Iterations $Requests -Warmups $WarmupRequests
            Measure-Endpoint -Client $client -Name 'search-blank-query-20' -Path '/api/v1/movies?query=%20%20%20&limit=20' -Iterations $Requests -Warmups $WarmupRequests
            Measure-Endpoint -Client $client -Name 'movie-detail' -Path '/api/v1/movies/82000000-0000-0000-0000-000000000001' -Iterations $Requests -Warmups $WarmupRequests
        )
    } finally {
        $client.Dispose()
        $handler.Dispose()
    }

    foreach ($metric in $metrics) {
        switch ($metric.name) {
            'search-query-20' { $metric.gateTargetMs = 300.0 }
            'movie-detail' { $metric.gateTargetMs = 200.0 }
            default { $metric.gateTargetMs = $null }
        }
        $metric.gatePassed = $metric.errors -eq 0 -and (
            $null -eq $metric.gateTargetMs -or $metric.p95Ms -le $metric.gateTargetMs
        )
    }

    $totalErrors = 0
    foreach ($metric in $metrics) {
        $totalErrors += [int]$metric.errors
    }
    if ($initial.StatusCode -ne 200) {
        $totalErrors++
    }
    $allGatesPassed = $initial.StatusCode -eq 200 -and $initialItems -eq 20 -and $totalErrors -eq 0 -and
        (@($metrics | Where-Object { $_.gateTargetMs -ne $null -and -not $_.gatePassed }).Count -eq 0)

    $gitCommit = (& git -C $repositoryRoot rev-parse HEAD).Trim()
    $gitWorkingTreeDirty = @(& git -C $repositoryRoot status --porcelain).Count -gt 0
    $result = [ordered]@{
        schemaVersion = 1
        measuredAtUtc = [DateTimeOffset]::UtcNow.ToString('o')
        gitCommit = $gitCommit
        gitWorkingTreeDirty = $gitWorkingTreeDirty
        dataset = [ordered]@{
            movieCount = 87585
            catalogVersion = 'catalog-performance-87585-v1'
            generator = 'performance/seed-87585.sql'
        }
        environment = [ordered]@{
            operatingSystem = [System.Environment]::OSVersion.VersionString
            javaVersion = (& java -version 2>&1 | Select-Object -First 1).ToString()
            postgresImage = 'postgres:17.6-alpine'
            databasePort = $DatabasePort
            backendPort = $BackendPort
            cacheMode = 'active-version check per request; immutable full-catalog in-memory snapshot'
        }
        parameters = [ordered]@{
            requests = $Requests
            warmupRequests = $WarmupRequests
            concurrency = 1
        }
        initialCacheBuild = [ordered]@{
            milliseconds = [Math]::Round($initial.Milliseconds, 3)
            statusCode = $initial.StatusCode
            itemCount = $initialItems
            error = $initial.Error
        }
        metrics = $metrics
        summary = [ordered]@{
            status = if ($allGatesPassed) { 'PASS' } else { 'FAIL' }
            totalErrors = $totalErrors
            searchP95TargetMs = 300.0
            detailP95TargetMs = 200.0
        }
    }

    $jsonPath = Join-Path $resultRoot 'latest.json'
    $markdownPath = Join-Path $resultRoot 'latest.md'
    Set-Content -LiteralPath $jsonPath -Value ($result | ConvertTo-Json -Depth 10) -Encoding utf8
    Write-MarkdownResult -Result $result -Path $markdownPath
    Write-Host "Performance result: $markdownPath"
    Write-Host "Gate: $($result.summary.status)"

    if (-not $allGatesPassed) {
        throw 'C0 Catalog performance Gate failed. See performance/results/latest.md.'
    }
} finally {
    if ($backendProcess -and -not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue
        $backendProcess.WaitForExit(10000) | Out-Null
    }
    $remainingListeners = Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $remainingListeners) {
        $listenerProcess = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
        if ($listenerProcess -and $listenerProcess.ProcessName -eq 'java') {
            Stop-Process -Id $listenerProcess.Id -Force -ErrorAction SilentlyContinue
        }
    }
    & docker rm --force $containerName *> $null
}
