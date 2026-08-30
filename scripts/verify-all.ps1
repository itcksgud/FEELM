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

function Invoke-ConfiguredPython {
    param(
        [Parameter(Mandatory = $true)][string]$EnvironmentVariable,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )

    $configured = [Environment]::GetEnvironmentVariable($EnvironmentVariable)
    if ([string]::IsNullOrWhiteSpace($configured)) {
        Invoke-Checked py (@('-3.12') + $ArgumentList)
    } else {
        if (-not (Test-Path -LiteralPath $configured -PathType Leaf)) {
            throw "$EnvironmentVariable does not point to a Python executable"
        }
        Invoke-Checked $configured $ArgumentList
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    Invoke-Checked npm @('run', 'contracts:check')
    Invoke-Checked npm @('run', 'c1:contracts:check')
    Invoke-Checked npm @('run', 'c2:contracts:check')
    Invoke-Checked npm @('run', 'c2b:contracts:check')
    Invoke-Checked npm @('run', 'c2b:decisions:check')
    Invoke-Checked npm @('run', 'c3:contracts:check')
    Invoke-Checked npm @('run', 'c3:decisions:check')
    Invoke-Checked npm @('run', 'c4:contracts:check')
    Invoke-Checked npm @('run', 'c4:decisions:check')
    Invoke-Checked npm @('run', 'c5:contracts:check')
    Invoke-Checked npm @('run', 'c6:contracts:check')
    Invoke-Checked npm @('run', 'approvals:check')
    Invoke-Checked npm @('run', 'completion:gates:check')
    Invoke-Checked npm @('run', 'completion:gates:mutation:check')
    Invoke-Checked npm @('run', 'revision:readiness:check')
    Invoke-Checked npm @('run', 'verification:parity:check')
    Invoke-Checked npm @('run', 'ci:workflow:check')
    Invoke-Checked npm @('run', 'supply-chain:check')
    Invoke-Checked npm @('run', 'recommendation:evidence:check')
    Invoke-Checked npm @('run', 'security:secrets:check')
    Invoke-Checked npm @('run', 'security:history:check')
    Invoke-Checked npm @('run', 'security:java:check')
    Invoke-Checked npm @('run', 'openapi:lint')
    Invoke-Checked npm @('run', 'openapi:mock:check')
    Invoke-Checked npm @('run', 'frontend:api-schema:check')
    Invoke-Checked docker @('compose', 'config', '--quiet')

    Invoke-Checked '.\backend\gradlew.bat' @('-p', 'backend', '--dependency-verification', 'strict', 'test')
    Invoke-Checked npm @('run', 'test', '--prefix', 'frontend')
    Invoke-Checked npm @('run', 'build', '--prefix', 'frontend')

    Invoke-ConfiguredPython 'FEELM_DATA_PYTHON' @('-m', 'unittest', 'discover', '-s', 'data-pipeline\tests', '-p', 'test_*.py')

    $previousPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = 'recommender\src;recommender\tests'
        Invoke-ConfiguredPython 'FEELM_RECOMMENDER_PYTHON' @('-m', 'unittest', 'discover', '-s', 'recommender\tests', '-p', 'test_*.py')
    }
    finally {
        $env:PYTHONPATH = $previousPythonPath
    }
}
finally {
    Pop-Location
}
