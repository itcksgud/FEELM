$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}

function Assert-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    if ($null -eq (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "REQUIRED_COMMAND_MISSING:$Name"
    }
}

function Reset-ScopedVenv {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$AllowedRoot
    )

    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $resolvedRoot = [System.IO.Path]::GetFullPath($AllowedRoot).TrimEnd('\') + '\'
    if (-not $resolvedPath.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'VENV_PATH_OUTSIDE_SCOPED_TEMP_ROOT'
    }
    if (Test-Path -LiteralPath $resolvedPath) {
        Remove-Item -LiteralPath $resolvedPath -Recurse -Force
    }
    Invoke-Checked py @('-3.12', '-m', 'venv', $resolvedPath)
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$temporaryRoot = Join-Path $projectRoot '.codex-tmp\reproduction'
$dataVenv = Join-Path $temporaryRoot 'data'
$recommenderVenv = Join-Path $temporaryRoot 'recommender'
$dataPython = Join-Path $dataVenv 'Scripts\python.exe'
$recommenderPython = Join-Path $recommenderVenv 'Scripts\python.exe'

Push-Location $projectRoot
try {
    foreach ($command in @('node', 'npm', 'py', 'java', 'docker', 'powershell')) {
        Assert-Command $command
    }
    Invoke-Checked docker @('compose', 'version')
    New-Item -ItemType Directory -Path $temporaryRoot -Force | Out-Null

    Invoke-Checked npm @('ci')
    Invoke-Checked npm @('ci', '--prefix', 'frontend')
    Invoke-Checked npm @('ci', '--prefix', 'e2e')

    Reset-ScopedVenv $dataVenv $temporaryRoot
    Invoke-Checked $dataPython @(
        '-m', 'pip', 'install', '--disable-pip-version-check', '--require-hashes',
        '-r', 'scripts\requirements-build-tools.lock'
    )
    Invoke-Checked $dataPython @(
        '-m', 'pip', 'install', '--disable-pip-version-check', '--no-build-isolation',
        '--require-hashes', '-r', 'requirements-data.lock'
    )
    Invoke-Checked $dataPython @(
        '-m', 'pip', 'install', '--disable-pip-version-check', '--require-hashes',
        '-r', 'requirements-ml.lock'
    )
    Invoke-Checked $dataPython @(
        '-m', 'pip', 'install', '--disable-pip-version-check', '--no-deps',
        '--no-build-isolation', '-e', 'data-pipeline'
    )

    Reset-ScopedVenv $recommenderVenv $temporaryRoot
    Invoke-Checked $recommenderPython @(
        '-m', 'pip', 'install', '--disable-pip-version-check', '--require-hashes',
        '-r', 'recommender\requirements-test.lock'
    )
    Invoke-Checked $recommenderPython @(
        '-m', 'pip', 'install', '--disable-pip-version-check', '--no-deps',
        '--no-build-isolation', '-e', 'recommender'
    )

    $previousEvidencePython = $env:FEELM_PYTHON
    $previousDataPython = $env:FEELM_DATA_PYTHON
    $previousRecommenderPython = $env:FEELM_RECOMMENDER_PYTHON
    try {
        $env:FEELM_PYTHON = $dataPython
        $env:FEELM_DATA_PYTHON = $dataPython
        $env:FEELM_RECOMMENDER_PYTHON = $recommenderPython
        Invoke-Checked npm @('run', 'verify')
    } finally {
        $env:FEELM_PYTHON = $previousEvidencePython
        $env:FEELM_DATA_PYTHON = $previousDataPython
        $env:FEELM_RECOMMENDER_PYTHON = $previousRecommenderPython
    }

    Invoke-Checked npm @('run', 'install:browsers', '--prefix', 'e2e')
    Invoke-Checked npm @('run', 'verify:e2e:fresh')
    Write-Host 'FEELM clean bootstrap, full verification, browser E2E, and C2A Compose probe: PASS'
}
finally {
    Pop-Location
}
