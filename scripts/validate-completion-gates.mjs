import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import YAML from 'yaml'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const gatePath = process.env.FEELM_COMPLETION_GATE_TEST_PATH
  ? path.resolve(process.env.FEELM_COMPLETION_GATE_TEST_PATH)
  : path.join(root, 'docs/planning/project-completion-gates.yaml')
const c1AcTestMapPath = process.env.FEELM_C1_AC_TEST_MAP_TEST_PATH
  ? path.resolve(process.env.FEELM_C1_AC_TEST_MAP_TEST_PATH)
  : path.join(root, 'docs/testing/c1-ac-test-map.csv')
const failures = []

const expectedGateIds = [
  'GATE-C0-CATALOG',
  'GATE-C1-RATING_FILM',
  'GATE-C2A-INTERNAL_RECOMMENDATION',
  'GATE-C2B-PERSONAL_DISCOVERY',
  'GATE-C3-PARTY_OTT_COMPARE',
  'GATE-C4-MEMBERSHIP_ONBOARDING',
  'GATE-C5-REPORT_PROFILE',
  'GATE-C6-RECOMMENDATION_INTERPRETATION_EXPERIMENT',
  'GATE-SYSTEM-REPRODUCTION',
]

const localMvpGateIds = [
  'GATE-C2B-PERSONAL_DISCOVERY',
  'GATE-C3-PARTY_OTT_COMPARE',
  'GATE-C4-MEMBERSHIP_ONBOARDING',
  'GATE-C5-REPORT_PROFILE',
]

const requiredLocalMvpRemaining = new Map([
  ['GATE-C2B-PERSONAL_DISCOVERY', [
    'blocked_personalization_xai_expected_star_exposure_and_action_extensions',
    'production_topology_and_operational_auth_validation',
  ]],
  ['GATE-C3-PARTY_OTT_COMPARE', [
    'blocked_party_public_champion_and_typed_signal_weighting',
    'production_auth_invitation_and_topology_validation',
  ]],
  ['GATE-C4-MEMBERSHIP_ONBOARDING', [
    'blocked_oauth_restart_password_recovery_change_and_delete',
    'production_email_origin_key_cookie_and_auth_validation',
  ]],
  ['GATE-C5-REPORT_PROFILE', [
    'blocked_account_lifecycle_expected_star_satisfaction_and_taste_diagnosis',
    'production_provider_public_origin_storage_and_notification_validation',
  ]],
])

const allowedGateStatuses = new Set([
  'IMPLEMENTED_AWAITING_REVISION_REPRODUCTION',
  'DRAFT_CONTRACT_BLOCKED_BY_DECISIONS_AND_EVIDENCE',
  'DRAFT_DECISIONS_REQUIRED',
  'DRAFT_DECISION_INVENTORY',
  'BLOCKED_BY_OPEN_SLICES_AND_REVISION',
  'BLOCKED_BY_REVISION_AND_LOCAL_E2E',
  'BLOCKED_BY_REVISION',
  'COMPLETE',
])

const requiredCompletionDefinitions = new Set([
  'contract_is_approved_and_machine_validated',
  'database_api_and_ui_follow_the_same_semantics',
  'happy_path_failure_path_and_authorization_are_automated',
  'compose_e2e_uses_real_services_not_mocks',
  'performance_or_recommendation_claims_have_versioned_evidence',
  'secrets_and_external_identifiers_do_not_leak',
  'a_clean_revision_can_be_reproduced_in_a_new_checkout',
])

const requiredBlockedActions = new Set([
  'commit_or_push',
  'deployment',
  'operational_email_or_oauth_credentials',
  'activation_of_product_semantics_not_supported_by_evidence',
])

function fail(message) {
  failures.push(message)
}

function asUniqueStrings(value, label) {
  if (!Array.isArray(value) || value.length === 0 || value.some((item) => typeof item !== 'string' || item.length === 0)) {
    fail(`${label} must be a non-empty string array`)
    return []
  }
  if (new Set(value).size !== value.length) {
    fail(`${label} contains duplicates`)
  }
  return value
}

function assertExactSet(actual, expected, label) {
  const actualSet = new Set(actual)
  const missing = [...expected].filter((item) => !actualSet.has(item))
  const extra = [...actualSet].filter((item) => !expected.has(item))
  if (missing.length || extra.length) {
    fail(`${label} differs (missing=${JSON.stringify(missing)}, extra=${JSON.stringify(extra)})`)
  }
}

function resolveRepositoryFile(relativePath, label) {
  if (path.isAbsolute(relativePath) || relativePath.includes('\\') || relativePath.split('/').includes('..')) {
    fail(`${label} must be a safe repository-relative POSIX path: ${relativePath}`)
    return null
  }
  const resolved = path.resolve(root, relativePath)
  if (!resolved.startsWith(`${root}${path.sep}`) || !fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
    fail(`${label} does not resolve to a repository file: ${relativePath}`)
    return null
  }
  return resolved
}

function parseCsv(text) {
  const rows = []
  let row = []
  let field = ''
  let quoted = false

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index]
    if (quoted) {
      if (character === '"' && text[index + 1] === '"') {
        field += '"'
        index += 1
      } else if (character === '"') {
        quoted = false
      } else {
        field += character
      }
    } else if (character === '"') {
      quoted = true
    } else if (character === ',') {
      row.push(field)
      field = ''
    } else if (character === '\n') {
      row.push(field.replace(/\r$/, ''))
      rows.push(row)
      row = []
      field = ''
    } else {
      field += character
    }
  }

  if (quoted) throw new Error('unterminated quoted CSV field')
  if (field.length > 0 || row.length > 0) {
    row.push(field.replace(/\r$/, ''))
    rows.push(row)
  }
  return rows.filter((item) => item.some((value) => value.length > 0))
}

function validateImplementedC1Evidence() {
  if (!fs.existsSync(c1AcTestMapPath) || !fs.statSync(c1AcTestMapPath).isFile()) {
    fail('GATE-C1-RATING_FILM implemented gate requires docs/testing/c1-ac-test-map.csv')
    return
  }

  let rows
  try {
    rows = parseCsv(fs.readFileSync(c1AcTestMapPath, 'utf8').replace(/^\uFEFF/, ''))
  } catch (error) {
    fail(`cannot parse C1 acceptance evidence CSV: ${error.message}`)
    return
  }
  if (rows.length < 2) {
    fail('GATE-C1-RATING_FILM implemented gate requires at least one C1 acceptance evidence row')
    return
  }

  const header = rows[0]
  const requiredColumns = ['acceptance_id', 'test_source', 'test_locator', 'evidence_state']
  const columnIndexes = new Map(requiredColumns.map((name) => [name, header.indexOf(name)]))
  const missingColumns = requiredColumns.filter((name) => columnIndexes.get(name) === -1)
  if (missingColumns.length > 0) {
    fail(`C1 acceptance evidence CSV misses columns: ${missingColumns.join(', ')}`)
    return
  }

  const seenAcceptanceIds = new Set()
  for (const [offset, row] of rows.slice(1).entries()) {
    const lineNumber = offset + 2
    const acceptanceId = row[columnIndexes.get('acceptance_id')]?.trim() ?? ''
    const evidenceState = row[columnIndexes.get('evidence_state')]?.trim() ?? ''
    const testSource = row[columnIndexes.get('test_source')]?.trim() ?? ''
    const testLocator = row[columnIndexes.get('test_locator')]?.trim() ?? ''
    const label = acceptanceId || `line ${lineNumber}`

    if (!acceptanceId) {
      fail(`C1 acceptance evidence row ${lineNumber} has no acceptance_id`)
    } else if (seenAcceptanceIds.has(acceptanceId)) {
      fail(`C1 acceptance evidence contains duplicate acceptance_id: ${acceptanceId}`)
    }
    seenAcceptanceIds.add(acceptanceId)

    if (evidenceState !== 'AUTOMATED' || !testSource || !testLocator) {
      fail(
        `GATE-C1-RATING_FILM implemented gate requires AUTOMATED evidence with non-empty ` +
        `test_source and test_locator: ${label}`,
      )
    }
  }
}

let document
try {
  document = YAML.parse(fs.readFileSync(gatePath, 'utf8'))
} catch (error) {
  console.error(`Completion gate validation: FAIL\n- cannot parse gate YAML: ${error.message}`)
  process.exit(1)
}

if (document?.schema_version !== 1) fail('schema_version must be 1')
if (!['IN_PROGRESS', 'COMPLETE'].includes(document?.status)) fail('root status must be IN_PROGRESS or COMPLETE')

const authority = document?.authority
if (!authority || typeof authority !== 'object' || Array.isArray(authority)) {
  fail('authority must be an object')
} else {
  const expectedAuthorityKeys = new Set([
    'product_plan',
    'product_owner_approval_request',
    'repository_rules',
    'local_runbook',
  ])
  assertExactSet(Object.keys(authority), expectedAuthorityKeys, 'authority keys')
  for (const [key, relativePath] of Object.entries(authority)) {
    if (typeof relativePath !== 'string') fail(`authority.${key} must be a string path`)
    else resolveRepositoryFile(relativePath, `authority.${key}`)
  }
}

const completionDefinitions = asUniqueStrings(document?.completion_definition, 'completion_definition')
assertExactSet(completionDefinitions, requiredCompletionDefinitions, 'completion_definition')

if (!Array.isArray(document?.gates)) fail('gates must be an array')
const gates = Array.isArray(document?.gates) ? document.gates : []
const gateIds = gates.map((gate) => gate?.id)
if (gateIds.some((id) => typeof id !== 'string')) fail('every gate must have a string id')
assertExactSet(gateIds.filter((id) => typeof id === 'string'), new Set(expectedGateIds), 'gate ids')

const gateById = new Map()
for (const gate of gates) {
  if (!gate || typeof gate !== 'object' || Array.isArray(gate)) {
    fail('every gate must be an object')
    continue
  }
  if (gateById.has(gate.id)) fail(`duplicate gate id: ${gate.id}`)
  gateById.set(gate.id, gate)
  if (typeof gate.title !== 'string' || gate.title.length === 0) fail(`${gate.id}.title is required`)
  if (!allowedGateStatuses.has(gate.status)) fail(`${gate.id}.status is not allowed: ${gate.status}`)

  const evidence = asUniqueStrings(gate.evidence, `${gate.id}.evidence`)
  for (const relativePath of evidence) resolveRepositoryFile(relativePath, `${gate.id}.evidence`)

  if (gate.status === 'COMPLETE') {
    if (Array.isArray(gate.remaining) && gate.remaining.length > 0) fail(`${gate.id} is COMPLETE but has remaining items`)
  } else {
    asUniqueStrings(gate.remaining, `${gate.id}.remaining`)
  }
}

for (const id of [
  'GATE-C0-CATALOG',
  'GATE-C1-RATING_FILM',
  'GATE-C2A-INTERNAL_RECOMMENDATION',
  ...localMvpGateIds,
  'GATE-C6-RECOMMENDATION_INTERPRETATION_EXPERIMENT',
]) {
  const gate = gateById.get(id)
  if (!gate) continue
  if (gate.status !== 'IMPLEMENTED_AWAITING_REVISION_REPRODUCTION') {
    fail(`${id} must remain implemented-awaiting-revision until clean checkout evidence exists`)
  }
  for (const item of ['user_authorized_revision', 'clean_checkout_blind_handoff']) {
    if (!gate.remaining?.includes(item)) fail(`${id}.remaining must include ${item}`)
  }
}

const c6Gate = gateById.get('GATE-C6-RECOMMENDATION_INTERPRETATION_EXPERIMENT')
if (c6Gate) {
  if (c6Gate.product_scope !== 'APPROVED_LOCAL_EXPERIMENT') {
    fail('GATE-C6-RECOMMENDATION_INTERPRETATION_EXPERIMENT.product_scope must be APPROVED_LOCAL_EXPERIMENT')
  }
  if (c6Gate.implementation_scope !== 'LOCAL_DEV_ONLY') {
    fail('GATE-C6-RECOMMENDATION_INTERPRETATION_EXPERIMENT.implementation_scope must be LOCAL_DEV_ONLY')
  }
  if (c6Gate.production_readiness !== 'BLOCKED') {
    fail('GATE-C6-RECOMMENDATION_INTERPRETATION_EXPERIMENT.production_readiness must remain BLOCKED')
  }
  for (const item of [
    'product_activation_requires_paired_scale_and_user_evidence',
    'production_topology_and_operational_auth_validation',
  ]) {
    if (!c6Gate.remaining?.includes(item)) {
      fail(`GATE-C6-RECOMMENDATION_INTERPRETATION_EXPERIMENT.remaining must include ${item}`)
    }
  }
  for (const evidence of [
    'docs/c6-recommendation-interpretation/local-contract.md',
    'docs/recommendation/evidence/REC-EV-014-local-interpretation-lab.md',
    'docs/recommendation/evidence/REC-EV-015-relative-utility.md',
    'docs/recommendation/evidence/manifests/rec-ev-015.json',
    'scripts/verify-c6-production-bundle.mjs',
    'e2e/local-mvp/local-mvp.spec.ts',
    'docs/testing/local-mvp-compose-e2e-20260830.md',
  ]) {
    if (!c6Gate.evidence?.includes(evidence)) {
      fail(`GATE-C6-RECOMMENDATION_INTERPRETATION_EXPERIMENT.evidence must include ${evidence}`)
    }
  }
}

if (gateById.get('GATE-C1-RATING_FILM')?.status === 'IMPLEMENTED_AWAITING_REVISION_REPRODUCTION') {
  validateImplementedC1Evidence()
}

for (const id of localMvpGateIds) {
  const gate = gateById.get(id)
  if (!gate) continue
  if (gate.product_scope !== 'APPROVED_LOCAL_MVP') {
    fail(`${id}.product_scope must be APPROVED_LOCAL_MVP`)
  }
  if (gate.implementation_scope !== 'LOCAL_ONLY') {
    fail(`${id}.implementation_scope must be LOCAL_ONLY`)
  }
  if (gate.production_readiness !== 'BLOCKED') {
    fail(`${id}.production_readiness must remain BLOCKED`)
  }
  if (gate.remaining?.includes('compose_local_mvp_e2e_execution_evidence')) {
    fail(`${id}.remaining must not retain compose_local_mvp_e2e_execution_evidence after actual PASS`)
  }
  for (const item of requiredLocalMvpRemaining.get(id) ?? []) {
    if (!gate.remaining?.includes(item)) fail(`${id}.remaining must include ${item}`)
  }
  if (!gate.evidence?.includes('e2e/local-mvp/local-mvp.spec.ts')) {
    fail(`${id}.evidence must point to the prepared isolated local-MVP browser spec`)
  }
  if (!gate.evidence?.includes('docs/testing/local-mvp-compose-e2e-20260830.md')) {
    fail(`${id}.evidence must include actual local-MVP Compose E2E evidence`)
  }
}

const approvalPath = authority?.product_owner_approval_request
const approvalFile = typeof approvalPath === 'string' ? resolveRepositoryFile(approvalPath, 'approval request') : null
const approvalText = approvalFile ? fs.readFileSync(approvalFile, 'utf8') : ''
const approvalPending = approvalText.includes('PENDING_PRODUCT_OWNER')
const approvalRecorded = approvalText.includes('RECORDED_LOCAL_PRODUCT_APPROVAL')
if (approvalPending) fail('product-owner approval must not retain PENDING_PRODUCT_OWNER after recorded local approval')
if (!approvalRecorded) fail('product-owner approval must contain RECORDED_LOCAL_PRODUCT_APPROVAL')
if (document.status === 'COMPLETE') {
  fail('root status cannot be COMPLETE before revision reproduction')
}

const systemGate = gateById.get('GATE-SYSTEM-REPRODUCTION')
if (systemGate) {
  if (systemGate.status !== 'BLOCKED_BY_REVISION') {
    fail('GATE-SYSTEM-REPRODUCTION must remain blocked by revision reproduction')
  }
  for (const item of [
    'fixed_revision_ci_dependency_audit',
    'fixed_revision_secret_history_scan',
    'multi_host_network_and_failure_benchmark_before_production_scale_claim',
    'user_authorized_revision',
    'clean_checkout_blind_handoff',
  ]) {
    if (!systemGate.remaining?.includes(item)) fail(`GATE-SYSTEM-REPRODUCTION.remaining must include ${item}`)
  }
  if (systemGate.remaining?.includes('compose_local_mvp_e2e_execution_evidence')) {
    fail('GATE-SYSTEM-REPRODUCTION must not retain completed Compose E2E evidence')
  }
  if (!systemGate.evidence?.includes('docs/testing/local-mvp-compose-e2e-20260830.md')) {
    fail('GATE-SYSTEM-REPRODUCTION.evidence must include actual local-MVP Compose E2E evidence')
  }
  for (const completedItem of ['one_command_compose_with_all_approved_services', 'full_contract_test_and_e2e']) {
    if (systemGate.remaining?.includes(completedItem)) fail(`verified item must not remain open: ${completedItem}`)
  }
}

const blockedActions = asUniqueStrings(document?.blocked_actions_requiring_user_authority, 'blocked_actions_requiring_user_authority')
assertExactSet(blockedActions, requiredBlockedActions, 'blocked_actions_requiring_user_authority')

const localMvpE2eEvidence = path.join(root, 'docs/testing/local-mvp-compose-e2e-20260830.md')
const localMvpE2eText = fs.readFileSync(localMvpE2eEvidence, 'utf8')
for (const marker of [
  'LOCAL_MVP_COMPOSE_E2E_PASS',
  'C2B_REAL_COMPOSE_BROWSER_E2E_PASS',
  'feelm-local-mvp-e2e-20260830033147-32512',
  'production readiness: `NO`',
]) {
  if (!localMvpE2eText.includes(marker)) fail(`local-MVP Compose E2E evidence is missing marker: ${marker}`)
}

const reproductionEvidence = path.join(root, 'docs/testing/reproducibility-verification-20260830.md')
if (fs.existsSync(reproductionEvidence)) {
  const text = fs.readFileSync(reproductionEvidence, 'utf8')
  for (const marker of ['LOCAL_WORKING_TREE_PASS_REVISION_PENDING', '`npm run verify:reproduce`']) {
    if (!text.includes(marker)) fail(`reproducibility evidence is missing marker: ${marker}`)
  }
} else {
  fail('working-tree reproducibility evidence is missing')
}

const packageJson = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'))
if (packageJson.scripts?.['completion:gates:check'] !== 'node scripts/validate-completion-gates.mjs') {
  fail('package.json must expose completion:gates:check')
}
if (packageJson.scripts?.['completion:gates:mutation:check'] !== 'node scripts/test-completion-gate-validator.mjs') {
  fail('package.json must expose completion:gates:mutation:check')
}
if (packageJson.scripts?.['verify:reproduce'] !== 'powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-reproduction.ps1') {
  fail('package.json must expose the clean-bootstrap verify:reproduce command')
}
if (packageJson.scripts?.['revision:readiness:check'] !== 'node scripts/check-revision-readiness.mjs' ||
    packageJson.scripts?.['revision:readiness:require'] !== 'node scripts/check-revision-readiness.mjs --require-ready') {
  fail('package.json must expose observational and enforcing revision-readiness commands')
}
const verifyScript = fs.readFileSync(path.join(root, 'scripts/verify-all.ps1'), 'utf8')
if (!verifyScript.includes("Invoke-Checked npm @('run', 'completion:gates:check')")) {
  fail('verify-all.ps1 must run completion:gates:check')
}
if (!verifyScript.includes("Invoke-Checked npm @('run', 'completion:gates:mutation:check')")) {
  fail('verify-all.ps1 must run completion:gates:mutation:check')
}
if (!verifyScript.includes("Invoke-Checked npm @('run', 'revision:readiness:check')")) {
  fail('verify-all.ps1 must report revision readiness')
}
const workflow = fs.readFileSync(path.join(root, '.github/workflows/ci.yml'), 'utf8')
if (!workflow.includes('npm run completion:gates:check')) {
  fail('CI must run completion:gates:check')
}
if (!workflow.includes('npm run completion:gates:mutation:check')) {
  fail('CI must run completion:gates:mutation:check')
}
if (!workflow.includes('npm run revision:readiness:require')) {
  fail('CI must require all project evidence to belong to a clean revision')
}
if (!workflow.includes('npm test --prefix e2e') || !workflow.includes('pwsh -NoProfile -File scripts/verify-c2-compose.ps1')) {
  fail('CI catalog E2E must run both Playwright and the C2A Compose probe')
}

const reproductionScript = fs.readFileSync(path.join(root, 'scripts/verify-reproduction.ps1'), 'utf8')
for (const marker of ["Invoke-Checked npm @('ci')", "Invoke-Checked npm @('run', 'verify')", "Invoke-Checked npm @('run', 'verify:e2e:fresh')"]) {
  if (!reproductionScript.includes(marker)) fail(`verify-reproduction.ps1 is missing orchestration marker: ${marker}`)
}
const freshE2eScript = fs.readFileSync(path.join(root, 'scripts/verify-e2e-fresh.ps1'), 'utf8')
if (!freshE2eScript.includes("Invoke-Checked npm @('test', '--prefix', 'e2e')") || !freshE2eScript.includes('verify-c2-compose.ps1')) {
  fail('verify-e2e-fresh.ps1 must run Playwright and the C2A Compose probe')
}

if (failures.length) {
  console.error('Completion gate validation: FAIL')
  for (const message of failures) console.error(`- ${message}`)
  process.exit(1)
}

console.log(
  `Completion gate validation: PASS (${gates.length} gates, ${completionDefinitions.length} completion definitions, ` +
  `${blockedActions.length} authority boundaries, product approval RECORDED_LOCAL_ONLY, Compose E2E PASS, revision pending)`,
)
