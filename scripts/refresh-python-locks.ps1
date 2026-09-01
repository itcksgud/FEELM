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
$toolVenv = Join-Path $projectRoot '.codex-tmp\python-lock-tools'
$toolPython = Join-Path $toolVenv 'Scripts\python.exe'
$pipCompile = Join-Path $toolVenv 'Scripts\pip-compile.exe'

Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $toolPython -PathType Leaf)) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $toolVenv) -Force | Out-Null
        Invoke-Checked py @('-3.12', '-m', 'venv', $toolVenv)
    }

    Invoke-Checked $toolPython @(
        '-m', 'pip', 'install', '--disable-pip-version-check', '--require-hashes',
        '-r', 'scripts\requirements-lock-tools.lock'
    )

    Invoke-Checked $pipCompile @(
        '--quiet', '--generate-hashes', '--allow-unsafe', '--strip-extras',
        '--output-file', 'scripts\requirements-audit-tools.lock',
        'scripts\requirements-audit-tools.in'
    )
    Invoke-Checked $pipCompile @(
        '--quiet', '--generate-hashes', '--allow-unsafe', '--strip-extras',
        '--output-file', 'scripts\requirements-build-tools.lock',
        'scripts\requirements-build-tools.in'
    )
    Invoke-Checked $pipCompile @(
        '--quiet', '--generate-hashes', '--all-build-deps', '--allow-unsafe', '--strip-extras',
        '--output-file', 'recommender\requirements.lock', 'recommender\pyproject.toml'
    )
    Invoke-Checked $pipCompile @(
        '--quiet', '--generate-hashes', '--all-build-deps', '--allow-unsafe', '--strip-extras',
        '--extra', 'test', '--output-file', 'recommender\requirements-test.lock',
        'recommender\pyproject.toml'
    )
    Invoke-Checked $pipCompile @(
        '--quiet', '--generate-hashes', '--allow-unsafe', '--strip-extras',
        '--output-file', 'requirements-data.lock', 'requirements-data.txt'
    )
    Invoke-Checked $pipCompile @(
        '--quiet', '--generate-hashes', '--strip-extras',
        '--constraint', 'requirements-data.txt',
        '--output-file', 'requirements-ml.lock', 'requirements-ml.txt'
    )

    Write-Host 'Python lock refresh complete. Review the lock diff and run npm run supply-chain:check.'
}
finally {
    Pop-Location
}
