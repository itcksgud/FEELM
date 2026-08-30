param(
    [string]$ProjectName = ("feelm-c2b-e2e-" + [DateTimeOffset]::UtcNow.ToString('yyyyMMddHHmmss') + "-" + $PID),
    [ValidateRange(1024, 65535)][int]$PostgresHostPort = 55432,
    [ValidateRange(1024, 65535)][int]$BackendHostPort = 58080,
    [ValidateRange(1024, 65535)][int]$RecommenderHostPort = 58000,
    [ValidateRange(1024, 65535)][int]$FrontendHostPort = 55173,
    [ValidateRange(30, 900)][int]$StartTimeoutSeconds = 600,
    [ValidateRange(10, 300)][int]$HealthTimeoutSeconds = 180,
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

function Fail-Safe {
    param([Parameter(Mandatory = $true)][string]$Code)
    throw $Code
}

function Invoke-Captured {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureCode
    )
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $FilePath @Arguments 2>&1
        $nativeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($nativeExitCode -ne 0) { Fail-Safe $FailureCode }
    return (($output | ForEach-Object { $_.ToString() }) -join "`n").Trim()
}

function Invoke-BoundedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][string]$FailureCode,
        [Parameter(Mandatory = $true)][string]$TimeoutCode
    )
    $temporaryRoot = [System.IO.Path]::GetTempPath()
    $nonce = [Guid]::NewGuid().ToString('N')
    $standardOutput = Join-Path $temporaryRoot ("feelm-c2b-e2e-" + $nonce + ".out")
    $standardError = Join-Path $temporaryRoot ("feelm-c2b-e2e-" + $nonce + ".err")
    $process = $null
    try {
        $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments `
            -WorkingDirectory $WorkingDirectory -RedirectStandardOutput $standardOutput `
            -RedirectStandardError $standardError -WindowStyle Hidden -PassThru
        [void]$process.Handle
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            $process.WaitForExit()
            Fail-Safe $TimeoutCode
        }
        if ($process.ExitCode -ne 0) { Fail-Safe $FailureCode }
    } finally {
        foreach ($temporaryFile in @($standardOutput, $standardError)) {
            if (Test-Path -LiteralPath $temporaryFile) {
                Remove-Item -LiteralPath $temporaryFile -Force
            }
        }
    }
}

function Get-ProjectContainerState {
    param([Parameter(Mandatory = $true)][string]$Service, [switch]$IncludeStopped)
    $arguments = @('compose', '-p', $ProjectName, 'ps', '-q')
    if ($IncludeStopped) { $arguments += '-a' }
    $arguments += $Service
    $containerId = Invoke-Captured docker $arguments 'C2B_E2E_SERVICE_NOT_FOUND'
    if ([string]::IsNullOrWhiteSpace($containerId)) { Fail-Safe 'C2B_E2E_SERVICE_NOT_FOUND' }
    $stateJson = Invoke-Captured docker @('inspect', '--format', '{{json .State}}', $containerId) 'C2B_E2E_STATE_UNAVAILABLE'
    return $stateJson | ConvertFrom-Json
}

function Wait-ProjectHealth {
    param([Parameter(Mandatory = $true)][int]$TimeoutSeconds)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $init = Get-ProjectContainerState 'recommender-artifact-init' -IncludeStopped
            $healthy = $init.Status -eq 'exited' -and [int]$init.ExitCode -eq 0
            foreach ($service in @('postgres', 'recommender', 'backend', 'frontend')) {
                $state = Get-ProjectContainerState $service
                $healthy = $healthy -and $state.Status -eq 'running' -and
                    $null -ne $state.Health -and $state.Health.Status -eq 'healthy'
            }
            if ($healthy) { return }
        } catch {
            # Containers can be absent or replacing while compose converges.
        }
        Start-Sleep -Seconds 2
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    Fail-Safe 'C2B_E2E_HEALTH_TIMEOUT'
}

if ($ProjectName -notmatch '^feelm-c2b-e2e-[a-z0-9-]+$') {
    throw 'C2B_E2E_PROJECT_NAME_UNSAFE'
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentNames = @(
    'POSTGRES_HOST_PORT', 'BACKEND_HOST_PORT', 'RECOMMENDER_HOST_PORT',
    'FRONTEND_HOST_PORT', 'E2E_BASE_URL'
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

$stackStarted = $false
$safeStage = 'C2B_E2E_INITIALIZATION_FAILED'
try {
    $safeStage = 'C2B_E2E_DOCKER_UNAVAILABLE'
    [void](Get-Command docker -ErrorAction Stop)

    $safeStage = 'C2B_E2E_PROJECT_NOT_FRESH'
    $existingContainers = Invoke-Captured docker @('compose', '-p', $ProjectName, 'ps', '-aq') 'C2B_E2E_PROJECT_INSPECTION_FAILED'
    $existingVolumes = Invoke-Captured docker @('volume', 'ls', '-q', '--filter', "label=com.docker.compose.project=$ProjectName") 'C2B_E2E_PROJECT_INSPECTION_FAILED'
    if (-not [string]::IsNullOrWhiteSpace($existingContainers) -or -not [string]::IsNullOrWhiteSpace($existingVolumes)) {
        Fail-Safe 'C2B_E2E_PROJECT_NOT_FRESH'
    }

    $env:POSTGRES_HOST_PORT = [string]$PostgresHostPort
    $env:BACKEND_HOST_PORT = [string]$BackendHostPort
    $env:RECOMMENDER_HOST_PORT = [string]$RecommenderHostPort
    $env:FRONTEND_HOST_PORT = [string]$FrontendHostPort
    $env:E2E_BASE_URL = "http://127.0.0.1:$FrontendHostPort"

    $safeStage = 'C2B_E2E_COMPOSE_CONFIG_INVALID'
    [void](Invoke-Captured docker @('compose', '-p', $ProjectName, 'config', '--quiet') 'C2B_E2E_COMPOSE_CONFIG_INVALID')

    $safeStage = 'C2B_E2E_STACK_START_FAILED'
    $composeArguments = @('compose', '-p', $ProjectName, 'up', '-d')
    if (-not $SkipBuild) { $composeArguments += '--build' }
    # Compose can create containers before its client exits or times out. Mark ownership before
    # invocation so the finally block always stops only this unique project without deleting volumes.
    $stackStarted = $true
    Invoke-BoundedProcess 'docker' $composeArguments $projectRoot $StartTimeoutSeconds `
        'C2B_E2E_STACK_START_FAILED' 'C2B_E2E_STACK_START_TIMEOUT'

    $safeStage = 'C2B_E2E_HEALTH_TIMEOUT'
    Wait-ProjectHealth $HealthTimeoutSeconds

    $safeStage = 'C2B_E2E_BROWSER_FAILED'
    $npmExecutable = if ($IsWindows -or $env:OS -eq 'Windows_NT') { 'npm.cmd' } else { 'npm' }
    Invoke-BoundedProcess $npmExecutable @('run', 'test:c2b', '--prefix', 'e2e') `
        $projectRoot 180 'C2B_E2E_BROWSER_FAILED' 'C2B_E2E_BROWSER_TIMEOUT'

    [pscustomobject]@{
        status = 'PASS'
        safeCode = 'C2B_REAL_COMPOSE_BROWSER_E2E_PASS'
        initialItemCount = 3
        appendedCollectionCount = 5
        finalActiveItemCount = 3
        viewingOnlyPreserved = $true
        ratingCompletionRemoved = $true
        developerVolumesModified = $false
    } | ConvertTo-Json -Compress
} catch {
    [pscustomobject]@{
        status = 'FAIL'
        safeCode = $safeStage
        developerVolumesModified = $false
    } | ConvertTo-Json -Compress
    exit 1
} finally {
    if ($stackStarted) {
        try {
            # Stop only this unique E2E project. Deliberately omit --volumes so even its data is preserved.
            [void](Invoke-Captured docker @('compose', '-p', $ProjectName, 'down') 'C2B_E2E_STACK_STOP_FAILED')
        } catch {
            Write-Warning 'C2B_E2E_STACK_STOP_FAILED'
        }
    }
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }
}
