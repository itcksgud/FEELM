$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $stream = [System.IO.File]::OpenRead($Path)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha256.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
        $stream.Dispose()
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$version = '1.7.12'
$archiveName = "actionlint_${version}_windows_amd64.zip"
$expectedSha256 = '6e7241b51e6817ea6a047693d8e6fed13b31819c9a0dd6c5a726e1592d22f6e9'
$toolRoot = Join-Path $projectRoot ".codex-tmp\actionlint\$version"
$archivePath = Join-Path $toolRoot $archiveName
$executable = Join-Path $toolRoot 'actionlint.exe'
$downloadUrl = "https://github.com/rhysd/actionlint/releases/download/v$version/$archiveName"

Push-Location $projectRoot
try {
    New-Item -ItemType Directory -Path $toolRoot -Force | Out-Null
    if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
        Invoke-WebRequest -UseBasicParsing -Uri $downloadUrl -OutFile $archivePath
    }
    if ((Get-Sha256 $archivePath) -ne $expectedSha256) {
        throw 'ACTIONLINT_ARCHIVE_CHECKSUM_MISMATCH'
    }
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        Expand-Archive -LiteralPath $archivePath -DestinationPath $toolRoot -Force
    }

    & $executable '-no-color' '.github/workflows/ci.yml'
    if ($LASTEXITCODE -ne 0) {
        throw "ACTIONLINT_FAILED_WITH_EXIT_CODE_$LASTEXITCODE"
    }

    [ordered]@{
        status = 'PASS'
        scanner = "actionlint-$version"
        scannerArchiveSha256 = $expectedSha256
        workflow = '.github/workflows/ci.yml'
    } | ConvertTo-Json -Compress
}
finally {
    Pop-Location
}
