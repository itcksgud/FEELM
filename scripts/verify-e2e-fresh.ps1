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

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    Write-Host 'Resetting only FEELM standalone Compose volumes for deterministic E2E fixtures.'
    Invoke-Checked docker @('compose', 'down', '--volumes')
    Invoke-Checked docker @('compose', 'up', '-d', '--build', '--wait')
    Invoke-Checked npm @('test', '--prefix', 'e2e')
    Invoke-Checked powershell @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        (Join-Path $PSScriptRoot 'verify-c2-compose.ps1')
    )
}
finally {
    Pop-Location
}
