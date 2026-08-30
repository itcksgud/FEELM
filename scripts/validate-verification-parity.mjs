import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const verify = fs.readFileSync(path.join(root, 'scripts/verify-all.ps1'), 'utf8')
const reproduce = fs.readFileSync(path.join(root, 'scripts/verify-reproduction.ps1'), 'utf8')
const freshE2e = fs.readFileSync(path.join(root, 'scripts/verify-e2e-fresh.ps1'), 'utf8')
const ci = fs.readFileSync(path.join(root, '.github/workflows/ci.yml'), 'utf8')
const packageJson = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'))
const failures = []

function requireText(text, marker, label) {
  if (!text.includes(marker)) failures.push(`${label} misses ${marker}`)
}

const commonNpmChecks = [
  'contracts:check',
  'c1:contracts:check',
  'c2:contracts:check',
  'c2b:contracts:check',
  'c2b:decisions:check',
  'c3:contracts:check',
  'c3:decisions:check',
  'c4:contracts:check',
  'c4:decisions:check',
  'c5:contracts:check',
  'c6:contracts:check',
  'approvals:check',
  'completion:gates:check',
  'completion:gates:mutation:check',
  'verification:parity:check',
  'supply-chain:check',
  'recommendation:evidence:check',
  'security:secrets:check',
  'openapi:lint',
  'openapi:mock:check',
]

for (const script of commonNpmChecks) {
  if (typeof packageJson.scripts?.[script] !== 'string') failures.push(`package.json misses script ${script}`)
  requireText(verify, `Invoke-Checked npm @('run', '${script}')`, 'verify-all.ps1')
  requireText(ci, `npm run ${script}`, '.github/workflows/ci.yml')
}

requireText(verify, "Invoke-Checked npm @('run', 'revision:readiness:check')", 'verify-all.ps1')
requireText(ci, 'npm run revision:readiness:require', '.github/workflows/ci.yml')
requireText(verify, "Invoke-Checked npm @('run', 'ci:workflow:check')", 'verify-all.ps1')
requireText(ci, '"$tool_root/actionlint" -no-color .github/workflows/ci.yml', '.github/workflows/ci.yml workflow lint boundary')
requireText(verify, "Invoke-Checked npm @('run', 'security:java:check')", 'verify-all.ps1')
requireText(verify, "Invoke-Checked npm @('run', 'security:history:check')", 'verify-all.ps1')
requireText(ci, 'gitleaks git --no-banner --redact .', '.github/workflows/ci.yml history security job')
requireText(ci, 'fetch-depth: 0', '.github/workflows/ci.yml history security job')
requireText(verify, "Invoke-Checked npm @('run', 'frontend:api-schema:check')", 'verify-all.ps1')
requireText(ci, 'npm run frontend:api-schema:check', '.github/workflows/ci.yml frontend job')
for (const marker of ['writeRuntimeCycloneDx', 'osv-scanner scan source']) {
  requireText(ci, marker, '.github/workflows/ci.yml Java security job')
}

for (const marker of [
  "'.\\backend\\gradlew.bat' @('-p', 'backend', '--dependency-verification', 'strict', 'test')",
  "npm @('run', 'test', '--prefix', 'frontend')",
  "npm @('run', 'build', '--prefix', 'frontend')",
  "Invoke-ConfiguredPython 'FEELM_DATA_PYTHON'",
  "Invoke-ConfiguredPython 'FEELM_RECOMMENDER_PYTHON'",
]) {
  requireText(verify, marker, 'verify-all.ps1 execution boundary')
}

for (const marker of [
  'bash backend/gradlew -p backend --dependency-verification strict test',
  'npm run test --prefix frontend',
  'npm run build --prefix frontend',
  "python -m unittest discover -s data-pipeline/tests -p 'test_*.py' -v",
  "python -m unittest discover -s recommender/tests -p 'test_*.py' -v",
]) {
  requireText(ci, marker, '.github/workflows/ci.yml execution boundary')
}

requireText(freshE2e, "Invoke-Checked npm @('test', '--prefix', 'e2e')", 'verify-e2e-fresh.ps1')
requireText(freshE2e, 'verify-c2-compose.ps1', 'verify-e2e-fresh.ps1')
requireText(ci, 'npm test --prefix e2e', '.github/workflows/ci.yml')
requireText(ci, 'verify-c2-compose.ps1', '.github/workflows/ci.yml')

for (const marker of [
  "Invoke-Checked npm @('ci')",
  "Invoke-Checked npm @('ci', '--prefix', 'frontend')",
  "Invoke-Checked npm @('ci', '--prefix', 'e2e')",
  'scripts\\requirements-build-tools.lock',
  'requirements-data.lock',
  'recommender\\requirements-test.lock',
  "Invoke-Checked npm @('run', 'verify')",
  "Invoke-Checked npm @('run', 'verify:e2e:fresh')",
]) {
  requireText(reproduce, marker, 'verify-reproduction.ps1 clean bootstrap')
}

if (failures.length) {
  console.error('Verification parity validation: FAIL')
  for (const failure of failures) console.error(`- ${failure}`)
  process.exit(1)
}

console.log(`Verification parity validation: PASS (${commonNpmChecks.length} common gates + backend/frontend/data/recommender/E2E/C2A boundaries)`)
