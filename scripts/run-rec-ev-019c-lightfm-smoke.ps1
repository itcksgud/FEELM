$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$contractPath = Join-Path $repoRoot 'docs/recommendation/contracts/rec-ev-019c-validation-artifacts.json'
if (-not (Test-Path -LiteralPath $contractPath -PathType Leaf)) {
    throw 'REC-EV-019C contract is missing from the resolved repository root.'
}

$image = 'python:3.12.5-slim-bookworm@sha256:c24c34b502635f1f7c4e99dc09a2cbd85d480b7dcfd077198c6b5af138906390'
$mount = "type=bind,source=$repoRoot,target=/workspace"
$containerCommand = @'
python -m pip install --disable-pip-version-check --no-cache-dir --require-hashes -r requirements-rec-ev-019c.lock
python scripts/smoke_rec_ev_019c_lightfm.py
'@

& docker run --rm --platform linux/amd64 --mount $mount --workdir /workspace $image sh -ec $containerCommand
if ($LASTEXITCODE -ne 0) {
    throw "REC-EV-019C LightFM smoke container failed with exit code $LASTEXITCODE."
}

& py -3 scripts/verify_rec_ev_019c_dependency_smoke.py --manifest docs/recommendation/evidence/manifests/rec-ev-019c-lightfm-linux-smoke.json
if ($LASTEXITCODE -ne 0) {
    throw "REC-EV-019C LightFM smoke verifier failed with exit code $LASTEXITCODE."
}
