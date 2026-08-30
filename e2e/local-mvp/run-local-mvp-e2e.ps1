param(
    [ValidatePattern('^feelm-local-mvp-e2e-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$')]
    [string]$ProjectName
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

$project = if ($ProjectName) {
    $ProjectName
} else {
    "feelm-local-mvp-e2e-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))-$PID"
}
if ($project.Length -gt 63) { throw 'Compose project name must be 63 characters or fewer.' }
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$compose = Join-Path $PSScriptRoot 'docker-compose.local-mvp.yml'
$volumeNames = @("${project}_postgres-data", "${project}_recommender-artifacts")
$started = $false
$defaultProject = 'feelm-standalone'
$defaultVolumesBefore = @()
$defaultVolumeSnapshotTaken = $false

function Invoke-Checked {
    param([string]$FilePath, [string[]]$ArgumentList)
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) { throw "$FilePath failed with exit code $LASTEXITCODE" }
}

Push-Location $root
try {
    $defaultVolumesBefore = @(& docker volume ls --quiet --filter "label=com.docker.compose.project=$defaultProject" | Sort-Object)
    if ($LASTEXITCODE -ne 0) { throw 'Unable to snapshot developer default Compose volumes.' }
    $defaultVolumeSnapshotTaken = $true
    Write-Host "EVIDENCE isolated_project=$project"
    Write-Host "EVIDENCE developer_default_volumes_before=$($defaultVolumesBefore -join ',')"

    $existingContainers = & docker compose -p $project -f $compose ps -aq
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect the isolated Compose project.' }
    if ($existingContainers) { throw "Freshness check failed: Compose project '$project' already has containers." }
    foreach ($volume in $volumeNames) {
        $existingVolume = & docker volume ls --quiet --filter "name=^${volume}$"
        if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect Docker volumes.' }
        if ($existingVolume -contains $volume) { throw "Freshness check failed: isolated volume '$volume' already exists; it was not deleted." }
    }

    $started = $true
    Invoke-Checked docker @('compose', '-p', $project, '-f', $compose, 'up', '-d', '--build', '--wait')
    $env:E2E_BASE_URL = 'http://127.0.0.1:55173'
    $env:E2E_MAILPIT_URL = 'http://127.0.0.1:58025'
    Invoke-Checked npm @('run', 'test:local-mvp', '--prefix', 'e2e')
}
catch {
    Write-Warning $_
    & docker compose -p $project -f $compose ps -a
    throw
}
finally {
    Remove-Item Env:E2E_BASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:E2E_MAILPIT_URL -ErrorAction SilentlyContinue
    if ($started) { & docker compose -p $project -f $compose down --remove-orphans }
    if ($defaultVolumeSnapshotTaken) {
        $defaultVolumesAfter = @(& docker volume ls --quiet --filter "label=com.docker.compose.project=$defaultProject" | Sort-Object)
        if ($LASTEXITCODE -ne 0) { throw 'Unable to verify developer default Compose volumes.' }
        Write-Host "EVIDENCE developer_default_volumes_after=$($defaultVolumesAfter -join ',')"
        if (Compare-Object $defaultVolumesBefore $defaultVolumesAfter) {
            Write-Host 'EVIDENCE developer_default_volumes_unchanged=false'
            throw "Developer default Compose volumes changed during isolated project '$project'."
        }
        Write-Host 'EVIDENCE developer_default_volumes_unchanged=true'
    }
    Pop-Location
}
