param([switch]$RequireRevisionReady)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

function Fail-Safe {
    param([Parameter(Mandatory = $true)][string]$Code)
    throw $Code
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [System.IO.File]::OpenRead($Path)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha256.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha256.Dispose()
        $stream.Dispose()
    }
}

function Invoke-GitleaksQuiet {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Executable @ArgumentList *> $null
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorPreference
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$version = '8.29.1'
$archiveName = "gitleaks_${version}_windows_x64.zip"
$expectedSha256 = 'e4b7d556f0cddbe23d10d8fac2ab0f29f68f019091c6599ffbeaa8a4fb71ac78'
$toolRoot = Join-Path $projectRoot ".codex-tmp\gitleaks\$version"
$archivePath = Join-Path $toolRoot $archiveName
$executable = Join-Path $toolRoot 'gitleaks.exe'
$downloadUrl = "https://github.com/gitleaks/gitleaks/releases/download/v$version/$archiveName"

Push-Location $projectRoot
try {
    New-Item -ItemType Directory -Path $toolRoot -Force | Out-Null
    if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
        Invoke-WebRequest -UseBasicParsing -Uri $downloadUrl -OutFile $archivePath
    }
    $actualSha256 = Get-Sha256 $archivePath
    if ($actualSha256 -ne $expectedSha256) {
        Fail-Safe 'GITLEAKS_ARCHIVE_CHECKSUM_MISMATCH'
    }
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        Expand-Archive -LiteralPath $archivePath -DestinationPath $toolRoot -Force
    }

    $controlRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("feelm-gitleaks-control-" + [Guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Path $controlRoot | Out-Null
        $controlToken = 'ghp_' + [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N').Substring(0, 4)
        Set-Content -LiteralPath (Join-Path $controlRoot 'control.txt') `
            -Value ("github_token = `"" + $controlToken + "`"") -Encoding utf8
        $controlExit = Invoke-GitleaksQuiet $executable @('dir', '--no-banner', '--redact', '--exit-code', '23', $controlRoot)
        if ($controlExit -ne 23) {
            Fail-Safe 'GITLEAKS_POSITIVE_CONTROL_NOT_DETECTED'
        }
    } finally {
        if (Test-Path -LiteralPath $controlRoot) {
            Remove-Item -LiteralPath $controlRoot -Recurse -Force
        }
    }

    $historyExit = Invoke-GitleaksQuiet $executable @('git', '--no-banner', '--redact', '.')
    if ($historyExit -ne 0) {
        Fail-Safe 'GITLEAKS_GIT_HISTORY_FINDING'
    }

    $readinessJson = & node scripts/check-revision-readiness.mjs
    if ($LASTEXITCODE -ne 0) {
        Fail-Safe 'REVISION_READINESS_QUERY_FAILED'
    }
    $readiness = $readinessJson | ConvertFrom-Json
    if ($RequireRevisionReady -and $readiness.status -ne 'READY') {
        Fail-Safe 'REVISION_NOT_READY_FOR_HISTORY_CLAIM'
    }

    [ordered]@{
        status = if ($readiness.status -eq 'READY') { 'PASS' } else { 'PASS_HISTORY_ONLY_REVISION_PENDING' }
        scanner = "gitleaks-$version"
        scannerArchiveSha256 = $expectedSha256
        positiveControl = 'PASS'
        gitHistory = 'PASS'
        revisionReadiness = $readiness.status
        head = $readiness.head
    } | ConvertTo-Json -Compress
}
finally {
    Pop-Location
}
