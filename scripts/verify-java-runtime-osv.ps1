$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

$projectRoot = Split-Path -Parent $PSScriptRoot
$scannerVersion = '2.5.1'
$scannerSha256 = '25e42f5ef6711fd8c0fb45390972205891dd44c6bd02ac93f0f63e8e98d9bfb6'
$scannerDirectory = Join-Path $projectRoot ".codex-tmp\osv-scanner-v$scannerVersion"
$scannerPath = Join-Path $scannerDirectory 'osv-scanner.exe'
$downloadUrl = "https://github.com/google/osv-scanner/releases/download/v$scannerVersion/osv-scanner_windows_amd64.exe"
$sbomPath = Join-Path $projectRoot 'backend\build\reports\runtime.cdx.json'

New-Item -ItemType Directory -Path $scannerDirectory -Force | Out-Null
if (-not (Test-Path -LiteralPath $scannerPath)) {
    Invoke-WebRequest -Uri $downloadUrl -OutFile $scannerPath
}

$stream = [System.IO.File]::OpenRead($scannerPath)
try {
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hashBytes = $sha256.ComputeHash($stream)
    }
    finally {
        $sha256.Dispose()
    }
}
finally {
    $stream.Dispose()
}
$actualSha256 = ([System.BitConverter]::ToString($hashBytes) -replace '-', '').ToLowerInvariant()
if ($actualSha256 -ne $scannerSha256) {
    throw "OSV Scanner checksum mismatch for v$scannerVersion"
}

Push-Location $projectRoot
try {
    & '.\backend\gradlew.bat' -p backend --dependency-verification strict writeRuntimeCycloneDx
    if ($LASTEXITCODE -ne 0) {
        throw "Gradle runtime SBOM generation failed with exit code $LASTEXITCODE"
    }

    & $scannerPath scan source -L $sbomPath --format table
    if ($LASTEXITCODE -ne 0) {
        throw "OSV runtime scan failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
