import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import YAML from 'yaml'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const validator = path.join(root, 'scripts/validate-completion-gates.mjs')
const canonicalPath = path.join(root, 'docs/planning/project-completion-gates.yaml')
const canonicalC1AcTestMapPath = path.join(root, 'docs/testing/c1-ac-test-map.csv')
const canonical = YAML.parse(fs.readFileSync(canonicalPath, 'utf8'))
const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'feelm-completion-gates-'))

function clone(value) {
  return structuredClone(value)
}

function run(gatePath, c1AcTestMapPath = canonicalC1AcTestMapPath) {
  return spawnSync(process.execPath, [validator], {
    cwd: root,
    encoding: 'utf8',
    env: {
      ...process.env,
      FEELM_COMPLETION_GATE_TEST_PATH: gatePath,
      FEELM_C1_AC_TEST_MAP_TEST_PATH: c1AcTestMapPath,
    },
  })
}

function mutate(name, mutation, expectedMessage) {
  const document = clone(canonical)
  mutation(document)
  const mutantPath = path.join(tempRoot, `${name}.yaml`)
  fs.writeFileSync(mutantPath, YAML.stringify(document), 'utf8')
  const result = run(mutantPath)
  assert.notEqual(result.status, 0, `${name} unexpectedly passed`)
  assert.match(`${result.stdout}\n${result.stderr}`, new RegExp(expectedMessage), `${name} failed for the wrong reason`)
}

try {
  const canonicalResult = run(canonicalPath)
  assert.equal(canonicalResult.status, 0, canonicalResult.stderr || canonicalResult.stdout)

  mutate('missing-gate', (document) => {
    document.gates = document.gates.filter((gate) => gate.id !== 'GATE-C5-REPORT_PROFILE')
  }, 'gate ids differs')

  mutate('missing-evidence', (document) => {
    document.gates[0].evidence.push('docs/testing/does-not-exist.md')
  }, 'does not resolve to a repository file')

  mutate('local-slice-premature-complete', (document) => {
    const gate = document.gates.find((item) => item.id === 'GATE-C2B-PERSONAL_DISCOVERY')
    gate.status = 'COMPLETE'
    gate.remaining = []
  }, 'must remain implemented-awaiting-revision')

  mutate('revision-gate-removed', (document) => {
    const gate = document.gates.find((item) => item.id === 'GATE-C0-CATALOG')
    gate.remaining = gate.remaining.filter((item) => item !== 'clean_checkout_blind_handoff')
  }, 'must include clean_checkout_blind_handoff')

  mutate('compose-e2e-evidence-removed', (document) => {
    const gate = document.gates.find((item) => item.id === 'GATE-SYSTEM-REPRODUCTION')
    gate.evidence = gate.evidence.filter((item) => item !== 'docs/testing/local-mvp-compose-e2e-20260830.md')
  }, 'must include actual local-MVP Compose E2E evidence')

  mutate('local-scope-expanded-to-production', (document) => {
    const gate = document.gates.find((item) => item.id === 'GATE-C5-REPORT_PROFILE')
    gate.production_readiness = 'READY'
  }, 'production_readiness must remain BLOCKED')

  mutate('local-approval-removed', (document) => {
    const gate = document.gates.find((item) => item.id === 'GATE-C4-MEMBERSHIP_ONBOARDING')
    delete gate.product_scope
  }, 'product_scope must be APPROVED_LOCAL_MVP')

  mutate('c5-exclusion-removed', (document) => {
    const gate = document.gates.find((item) => item.id === 'GATE-C5-REPORT_PROFILE')
    gate.remaining = gate.remaining.filter((item) =>
      item !== 'blocked_account_lifecycle_expected_star_satisfaction_and_taste_diagnosis')
  }, 'must include blocked_account_lifecycle_expected_star_satisfaction_and_taste_diagnosis')

  mutate('c6-experiment-expanded-to-product', (document) => {
    const gate = document.gates.find((item) => item.id === 'GATE-C6-RECOMMENDATION_INTERPRETATION_EXPERIMENT')
    gate.product_scope = 'APPROVED_LOCAL_MVP'
  }, 'product_scope must be APPROVED_LOCAL_EXPERIMENT')

  mutate('c6-v2-evidence-removed', (document) => {
    const gate = document.gates.find((item) => item.id === 'GATE-C6-RECOMMENDATION_INTERPRETATION_EXPERIMENT')
    gate.evidence = gate.evidence.filter((item) =>
      item !== 'docs/recommendation/evidence/REC-EV-015-relative-utility.md')
  }, 'evidence must include docs/recommendation/evidence/REC-EV-015-relative-utility.md')

  mutate('authority-boundary-removed', (document) => {
    document.blocked_actions_requiring_user_authority = document.blocked_actions_requiring_user_authority
      .filter((item) => item !== 'commit_or_push')
  }, 'blocked_actions_requiring_user_authority differs')

  mutate('premature-root-complete', (document) => {
    document.status = 'COMPLETE'
  }, 'root status cannot be COMPLETE')

  const c1GapPath = path.join(tempRoot, 'c1-ac-test-map-gap.csv')
  const c1Rows = fs.readFileSync(canonicalC1AcTestMapPath, 'utf8').split(/\r?\n/)
  const c1Header = c1Rows[0].split(',')
  const sourceIndex = c1Header.indexOf('test_source')
  const locatorIndex = c1Header.indexOf('test_locator')
  const stateIndex = c1Header.indexOf('evidence_state')
  assert.ok(sourceIndex >= 0 && locatorIndex >= 0 && stateIndex >= 0, 'C1 evidence CSV misses mutation columns')
  const automatedRowIndex = c1Rows.findIndex((line, index) => index > 0 && line.includes(',AUTOMATED,'))
  assert.ok(automatedRowIndex > 0, 'C1 evidence CSV has no AUTOMATED row to mutate')
  const gapFields = c1Rows[automatedRowIndex].split(',')
  gapFields[sourceIndex] = ''
  gapFields[locatorIndex] = ''
  gapFields[stateIndex] = 'GAP'
  c1Rows[automatedRowIndex] = gapFields.join(',')
  fs.writeFileSync(c1GapPath, c1Rows.join('\n'), 'utf8')
  const c1GapResult = run(canonicalPath, c1GapPath)
  assert.notEqual(c1GapResult.status, 0, 'c1-gap-evidence unexpectedly passed')
  assert.match(
    `${c1GapResult.stdout}\n${c1GapResult.stderr}`,
    /implemented gate requires AUTOMATED evidence with non-empty test_source and test_locator/,
    'c1-gap-evidence failed for the wrong reason',
  )

  console.log('Completion gate mutation validation: PASS (canonical + 13 rejected mutants)')
} finally {
  fs.rmSync(tempRoot, { recursive: true, force: true })
}
