param(
    [switch]$Build,
    [ValidateRange(30, 900)][int]$BuildTimeoutSeconds = 300,
    [ValidateRange(10, 300)][int]$HealthTimeoutSeconds = 120
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
        # Native stderr is captured and discarded. PowerShell 7 can otherwise promote harmless
        # Docker client diagnostics to a terminating RemoteException before LASTEXITCODE is checked.
        $ErrorActionPreference = 'Continue'
        $output = & $FilePath @Arguments 2>&1
        $nativeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($nativeExitCode -ne 0) {
        Fail-Safe $FailureCode
    }
    return (($output | ForEach-Object { $_.ToString() }) -join "`n").Trim()
}

function Get-ContainerState {
    param([Parameter(Mandatory = $true)][string]$Service, [switch]$IncludeStopped)
    $psArgs = @('compose', 'ps', '-q')
    if ($IncludeStopped) { $psArgs += '-a' }
    $psArgs += $Service
    $containerId = Invoke-Captured docker $psArgs 'COMPOSE_SERVICE_NOT_FOUND'
    if ([string]::IsNullOrWhiteSpace($containerId)) {
        Fail-Safe 'COMPOSE_SERVICE_NOT_FOUND'
    }
    $stateJson = Invoke-Captured docker @('inspect', '--format', '{{json .State}}', $containerId) 'COMPOSE_STATE_UNAVAILABLE'
    return [pscustomobject]@{ Id = $containerId; State = ($stateJson | ConvertFrom-Json) }
}

function Invoke-DbScalar {
    param([Parameter(Mandatory = $true)][string]$Sql)
    return Invoke-Captured docker @(
        'compose', 'exec', '-T', 'postgres', 'psql', '-X', '-q', '-At',
        '-v', 'ON_ERROR_STOP=1', '-U', $script:DatabaseUser, '-d', $script:DatabaseName,
        '-c', $Sql
    ) 'POSTGRES_PROBE_FAILED'
}

function Invoke-BoundedComposeBuild {
    param(
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )
    $temporaryRoot = [System.IO.Path]::GetTempPath()
    $nonce = [Guid]::NewGuid().ToString('N')
    $standardOutput = Join-Path $temporaryRoot ("feelm-compose-" + $nonce + ".out")
    $standardError = Join-Path $temporaryRoot ("feelm-compose-" + $nonce + ".err")
    $process = $null
    try {
        $process = Start-Process -FilePath 'docker' `
            -ArgumentList @('compose', 'up', '-d', '--build') `
            -WorkingDirectory $WorkingDirectory `
            -RedirectStandardOutput $standardOutput `
            -RedirectStandardError $standardError `
            -WindowStyle Hidden -PassThru
        # Windows PowerShell 5 can leave ExitCode null unless the native handle is
        # materialized before WaitForExit.
        [void]$process.Handle
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            $process.WaitForExit()
            Fail-Safe 'COMPOSE_BUILD_TIMEOUT'
        }
        if ($process.ExitCode -ne 0) {
            Fail-Safe 'COMPOSE_BUILD_OR_START_FAILED'
        }
    } finally {
        foreach ($temporaryFile in @($standardOutput, $standardError)) {
            if (Test-Path -LiteralPath $temporaryFile) {
                Remove-Item -LiteralPath $temporaryFile -Force
            }
        }
    }
}

function Wait-ComposeHealth {
    param([Parameter(Mandatory = $true)][int]$TimeoutSeconds)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $init = Get-ContainerState 'recommender-artifact-init' -IncludeStopped
            $states = @{}
            foreach ($service in @('postgres', 'recommender', 'backend', 'frontend')) {
                $states[$service] = Get-ContainerState $service
            }
            $allHealthy = $init.State.Status -eq 'exited' -and [int]$init.State.ExitCode -eq 0
            foreach ($service in @('postgres', 'recommender', 'backend', 'frontend')) {
                $state = $states[$service].State
                $allHealthy = $allHealthy -and $state.Status -eq 'running' -and
                    $null -ne $state.Health -and $state.Health.Status -eq 'healthy'
            }
            if ($allHealthy) {
                return [pscustomobject]@{ Init = $init; Services = $states }
            }
        } catch {
            # A container can be absent or replacing while compose up is still converging.
        }
        Start-Sleep -Seconds 2
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    Fail-Safe 'COMPOSE_HEALTH_TIMEOUT'
}

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $safeStage = 'COMPOSE_PROBE_INITIALIZATION_FAILED'
    try {
        $safeStage = 'DOCKER_CLI_UNAVAILABLE'
        [void](Get-Command docker -ErrorAction Stop)
        $safeStage = 'COMPOSE_CONFIG_INVALID'
        [void](Invoke-Captured docker @('compose', 'config', '--quiet') 'COMPOSE_CONFIG_INVALID')
        if ($Build) {
            # Non-destructive: rebuild/recreate containers but never down, delete, or replace named volumes.
            $safeStage = 'COMPOSE_BUILD_OR_START_FAILED'
            Invoke-BoundedComposeBuild $BuildTimeoutSeconds $projectRoot
        }

        $safeStage = 'COMPOSE_HEALTH_TIMEOUT'
        $healthyStack = Wait-ComposeHealth $HealthTimeoutSeconds
        $init = $healthyStack.Init
        $serviceStates = $healthyStack.Services

        $safeStage = 'RECOMMENDER_COMPOSE_EXEC_FAILED'
        $recommenderProbeJson = Invoke-Captured docker @(
            'compose', 'exec', '-T', 'recommender',
            'python', '-m', 'feelm_recommender.compose_probe'
        ) 'RECOMMENDER_COMPOSE_EXEC_FAILED'
        $safeStage = 'RECOMMENDER_COMPOSE_PARSE_FAILED'
        $recommenderProbe = $recommenderProbeJson | ConvertFrom-Json
        $safeStage = 'RECOMMENDER_COMPOSE_INVARIANT_FAILED'
        if ($recommenderProbe.status -ne 'PASS' -or [int]$recommenderProbe.failClosedCheckCount -ne 3) {
            Fail-Safe 'RECOMMENDER_COMPOSE_INVARIANT_FAILED'
        }

        $safeStage = 'BACKEND_TO_RECOMMENDER_CONTAINER_ID_FAILED'
        $backendContainerId = [string]($serviceStates['backend'].Id)
        if ($backendContainerId -notmatch '^[a-f0-9]{12,64}$') {
            Fail-Safe 'BACKEND_TO_RECOMMENDER_CONTAINER_ID_FAILED'
        }
        # Use a token-free TCP probe from the backend container. Authenticated readiness and
        # ranking are exercised by compose_probe; JDK client auth is covered by backend tests.
        $safeStage = 'BACKEND_TO_RECOMMENDER_TCP_FAILED'
        [void](Invoke-Captured docker @(
            'exec', $backendContainerId, 'nc', '-z', '-w', '5', 'recommender', '8000'
        ) 'BACKEND_TO_RECOMMENDER_TCP_FAILED')

        $safeStage = 'BACKEND_MOUNT_INSPECTION_FAILED'
        $backendMounts = (Invoke-Captured docker @(
            'inspect', '--format', '{{json .Mounts}}', $serviceStates['backend'].Id
        ) 'BACKEND_MOUNT_INSPECTION_FAILED') | ConvertFrom-Json
        $candidateMount = @($backendMounts | Where-Object { $_.Destination -eq '/c2-artifacts' })
        if ($candidateMount.Count -ne 1 -or [bool]$candidateMount[0].RW) {
            Fail-Safe 'BACKEND_CANDIDATE_MOUNT_NOT_READ_ONLY'
        }

        $safeStage = 'BACKEND_ENV_INSPECTION_FAILED'
        $backendEnvironment = (Invoke-Captured docker @(
            'inspect', '--format', '{{json .Config.Env}}', $serviceStates['backend'].Id
        ) 'BACKEND_ENV_INSPECTION_FAILED') | ConvertFrom-Json
        $environmentKeys = @($backendEnvironment | ForEach-Object { ($_ -split '=', 2)[0] })
        foreach ($requiredKey in @(
            'C2_CANDIDATE_STORE_PATH', 'C2_RECOMMENDER_BASE_URL', 'C2_CLIENT_AUTH_MODE',
            'C2_SERVICE_TOKEN', 'C2_RECOMMENDER_TIMEOUT_MS', 'C2_CLIENT_LOCAL_FAKE_ENABLED',
            'OUTBOX_WORKER_ENABLED'
        )) {
            if ($environmentKeys -notcontains $requiredKey) {
                Fail-Safe 'BACKEND_C2_ENV_BOUNDARY_INCOMPLETE'
            }
        }

        $safeStage = 'POSTGRES_ENV_UNAVAILABLE'
        $script:DatabaseUser = Invoke-Captured docker @('compose', 'exec', '-T', 'postgres', 'printenv', 'POSTGRES_USER') 'POSTGRES_ENV_UNAVAILABLE'
        $script:DatabaseName = Invoke-Captured docker @('compose', 'exec', '-T', 'postgres', 'printenv', 'POSTGRES_DB') 'POSTGRES_ENV_UNAVAILABLE'

        $safeStage = 'OUTBOX_TO_RATING_SNAPSHOT_NOT_OBSERVED'
        $projectionReady = $false
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            $applicationCount = [int](Invoke-DbScalar @'
SELECT count(*)
  FROM c2_rating_input_event_application a
  JOIN domain_outbox o ON o.event_id = a.event_id
 WHERE o.event_type IN ('RATING_CREATED','RATING_UPDATED','RATING_DELETED')
   AND o.status = 'PROCESSED';
'@)
            if ($applicationCount -gt 0) {
                $projectionReady = $true
                break
            }
            Start-Sleep -Seconds 1
        }
        if (-not $projectionReady) {
            Fail-Safe 'OUTBOX_TO_RATING_SNAPSHOT_NOT_OBSERVED'
        }

        $safeStage = 'C2_DATABASE_COMPOSE_INVARIANT_FAILED'
        $databaseSummaryJson = Invoke-DbScalar @'
WITH required(version) AS (
    VALUES ('1'), ('2'), ('3'), ('4'), ('5'), ('100'), ('101'), ('102')
), active AS (
    SELECT id, public_version FROM catalog_version WHERE status = 'ACTIVE'
), ui_ready AS (
    SELECT count(*) AS item_count
      FROM movie_catalog_projection p
      JOIN active a ON a.id = p.catalog_version_id
     WHERE p.identity_status = 'IDENTITY_VERIFIED'
       AND p.visibility_status = 'UI_READY'
       AND p.deleted = false
), linked AS (
    SELECT count(*) AS application_count
      FROM c2_rating_input_event_application a
      JOIN domain_outbox o ON o.event_id = a.event_id
     WHERE o.event_type IN ('RATING_CREATED','RATING_UPDATED','RATING_DELETED')
       AND o.status = 'PROCESSED'
), consistency AS (
    SELECT NOT EXISTS (
        SELECT 1
          FROM c2_rating_input_snapshot s
         WHERE s.rating_count <> (SELECT count(*) FROM c2_rating_input_item i WHERE i.user_id = s.user_id)
            OR s.input_version !~ '^c2-active-rating-input-v1:sha256:[a-f0-9]{64}$'
    ) AS valid
)
SELECT json_build_object(
    'status', 'PASS',
    'safeCode', 'C2_DATABASE_COMPOSE_READY',
    'activeCatalogVersion', (SELECT public_version FROM active),
    'uiReadyCount', (SELECT item_count FROM ui_ready),
    'requiredMigrationCount', (
        SELECT count(*) FROM required r
         WHERE EXISTS (SELECT 1 FROM flyway_schema_history h WHERE h.version = r.version AND h.success)
    ),
    'ratingSnapshotCount', (SELECT count(*) FROM c2_rating_input_snapshot),
    'ratingEventApplicationCount', (SELECT application_count FROM linked),
    'ratingSnapshotConsistent', (SELECT valid FROM consistency),
    'exposureSchemaTableCount', (
        SELECT count(*) FROM (VALUES
          (to_regclass('public.recommendation_exposure_batch')),
          (to_regclass('public.recommendation_exposure_item'))
        ) AS tables(name) WHERE name IS NOT NULL
    )
)::text;
'@
        $databaseSummary = $databaseSummaryJson | ConvertFrom-Json
        if ($databaseSummary.activeCatalogVersion -ne $recommenderProbe.catalogVersion -or
            [int]$databaseSummary.uiReadyCount -ne [int]$recommenderProbe.candidateCount -or
            [int]$databaseSummary.requiredMigrationCount -ne 8 -or
            [int]$databaseSummary.ratingSnapshotCount -lt 1 -or
            [int]$databaseSummary.ratingEventApplicationCount -lt 1 -or
            -not [bool]$databaseSummary.ratingSnapshotConsistent -or
            [int]$databaseSummary.exposureSchemaTableCount -ne 2) {
            Fail-Safe 'C2_DATABASE_COMPOSE_INVARIANT_FAILED'
        }

        $safeStage = 'HOST_HEALTH_PROBE_FAILED'
        try {
            $backendHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/actuator/health' -TimeoutSec 5
            $frontendHealth = Invoke-WebRequest -Uri 'http://127.0.0.1:5173/healthz' -TimeoutSec 5 -UseBasicParsing
        } catch {
            Fail-Safe 'HOST_HEALTH_PROBE_FAILED'
        }
        if ($backendHealth.status -ne 'UP' -or $frontendHealth.StatusCode -ne 200) {
            Fail-Safe 'HOST_HEALTH_PROBE_FAILED'
        }

        [ordered]@{
            status = 'PASS'
            safeCode = 'C2_COMPOSE_INTEGRATION_READY'
            artifactInitExitCode = 0
            healthyServiceCount = 4
            activeCatalogVersion = $databaseSummary.activeCatalogVersion
            uiReadyCount = [int]$databaseSummary.uiReadyCount
            candidateCount = [int]$recommenderProbe.candidateCount
            rankedItemCount = [int]$recommenderProbe.rankedItemCount
            artifactCheckCount = [int]$recommenderProbe.artifactCheckCount
            failClosedCheckCount = [int]$recommenderProbe.failClosedCheckCount
            requiredMigrationCount = [int]$databaseSummary.requiredMigrationCount
            ratingSnapshotCount = [int]$databaseSummary.ratingSnapshotCount
            ratingEventApplicationCount = [int]$databaseSummary.ratingEventApplicationCount
            exposureSchemaTableCount = [int]$databaseSummary.exposureSchemaTableCount
            backendToRecommenderTcpReady = $true
            candidateMountReadOnly = $true
            rankingPolicy = $recommenderProbe.rankingPolicy
            rankingAlpha = 0
            expectedStarStatus = 'NOT_COMPUTED'
            publicSpringRecommendationEndpoint = 'NOT_IMPLEMENTED_BY_CONTRACT'
        } | ConvertTo-Json -Compress
    } catch {
        $safeCode = $_.Exception.Message
        if ($safeCode -notmatch '^[A-Z0-9_]+$') {
            $safeCode = $safeStage
        }
        [ordered]@{ status = 'FAIL'; safeCode = $safeCode } | ConvertTo-Json -Compress
        exit 2
    }
} finally {
    Pop-Location
}
