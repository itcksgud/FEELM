import { delimiter } from 'node:path'
import { spawnSync } from 'node:child_process'

const isWindows = process.platform === 'win32'
const configuredPython = process.env.FEELM_PYTHON
const python = configuredPython || (isWindows ? 'py' : 'python3')
const pythonPrefix = configuredPython ? [] : (isWindows ? ['-3'] : [])
const environment = {
  ...process.env,
  PYTHONPATH: ['scripts', process.env.PYTHONPATH].filter(Boolean).join(delimiter),
}

const commands = [
  [python, [...pythonPrefix, '-m', 'unittest', 'discover', '-s', 'scripts/tests', '-p', 'test_recommendation_*.py']],
  [python, [...pythonPrefix, '-m', 'unittest', 'discover', '-s', 'scripts/tests', '-p', 'test_spark_*.py']],
  [python, [...pythonPrefix, '-m', 'unittest', 'scripts/tests/test_build_rec_ev_019b_features.py']],
  [python, [...pythonPrefix, '-m', 'unittest', 'scripts/tests/test_build_rec_ev_019a_cohorts.py']],
  [python, [...pythonPrefix, '-m', 'unittest', 'scripts/tests/test_validate_rec_ev_019c_contract.py']],
  [python, [...pythonPrefix, '-m', 'unittest', 'scripts/tests/test_run_rec_ev_019c_validation.py']],
  [python, [...pythonPrefix, '-m', 'unittest', 'scripts/tests/test_verify_rec_ev_019c_dependency_smoke.py']],
  [python, [...pythonPrefix, '-m', 'unittest', 'scripts/tests/test_verify_rec_ev_019c_resource_dry_run.py']],
  [python, [...pythonPrefix, 'scripts/verify_recommendation_exploration_pareto.py', '--manifest', 'docs/recommendation/evidence/manifests/rec-ev-004.json']],
  [python, [...pythonPrefix, 'scripts/verify_recommendation_exploration_full_catalog.py', '--manifest', 'docs/recommendation/evidence/manifests/rec-ev-004b.json']],
  [python, [...pythonPrefix, 'scripts/verify_recommendation_reason_faithfulness.py', '--manifest', 'docs/recommendation/evidence/manifests/rec-ev-006.json']],
  [python, [...pythonPrefix, 'scripts/verify_rec_ev_007.py']],
  [python, [...pythonPrefix, 'scripts/verify_recommendation_cold_start_full_catalog.py', '--manifest', 'docs/recommendation/evidence/manifests/rec-ev-011.json']],
  [python, [...pythonPrefix, 'scripts/verify_recommendation_constrained_two_plus_one.py', '--manifest', 'docs/recommendation/evidence/manifests/rec-ev-013.json']],
  [python, [...pythonPrefix, 'scripts/verify_recommendation_relative_utility.py', '--manifest', 'docs/recommendation/evidence/manifests/rec-ev-015.json']],
  [python, [...pythonPrefix, 'scripts/verify_recommendation_user_case_study.py', '--manifest', 'docs/recommendation/evidence/manifests/rec-ev-016.json']],
  [python, [...pythonPrefix, 'scripts/verify_recommendation_relational_ablation.py', '--manifest', 'docs/recommendation/evidence/manifests/rec-ev-017.json']],
  [python, [...pythonPrefix, 'scripts/verify_recommendation_user_percentile_audit.py', '--manifest', 'docs/recommendation/evidence/manifests/rec-ev-018.json']],
  [python, [...pythonPrefix, 'scripts/verify_recommendation_binary_onboarding_preflight.py', '--manifest', 'docs/recommendation/evidence/manifests/rec-ev-019p.json']],
  [python, [...pythonPrefix, 'scripts/validate_rec_ev_019c_contract.py']],
  [python, [...pythonPrefix, 'scripts/verify_rec_ev_019c_validation.py', '--manifest', 'docs/recommendation/evidence/manifests/rec-ev-019c-synthetic-preflight.json']],
  [python, [...pythonPrefix, 'scripts/verify_rec_ev_019c_dependency_smoke.py', '--manifest', 'docs/recommendation/evidence/manifests/rec-ev-019c-lightfm-linux-smoke.json']],
  [python, [...pythonPrefix, 'scripts/verify_rec_ev_019c_resource_dry_run.py', '--manifest', 'docs/recommendation/evidence/manifests/rec-ev-019c-resource-dry-run.json']],
  [python, [...pythonPrefix, '-m', 'unittest', 'scripts/tests/test_rec_ev_019d_contract.py', 'scripts/tests/test_run_rec_ev_019d_prefix_ablation.py', 'scripts/tests/test_verify_rec_ev_019d_prefix_ablation.py']],
  [python, [...pythonPrefix, 'scripts/validate_rec_ev_019d_contract.py']],
  [python, [...pythonPrefix, 'scripts/verify_rec_ev_019d_prefix_ablation.py', '--manifest', 'docs/recommendation/evidence/manifests/rec-ev-019d-validation.json']],
  [python, [...pythonPrefix, 'scripts/validate_recommendation_vnext_readiness.py']],
  [python, [...pythonPrefix, 'scripts/verify_spark_als_scaling_evidence.py', '--result', 'performance/results/spark-als-scaling/latest.json']],
  [process.execPath, ['scripts/verify-recommendation-ui-comparison.mjs']],
  [process.execPath, ['scripts/verify-recommendation-product-decision-packet.mjs']],
]

for (const [command, args] of commands) {
  const result = spawnSync(command, args, { stdio: 'inherit', env: environment })
  if (result.error) {
    console.error(result.error.message)
    process.exit(1)
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1)
  }
}

console.log('Recommendation evidence verification passed: unit protocols, REC-EV-004/004B/006/007/008/011/013/015/016/017/018/019P/019A, REC-EV-019C gates, REC-EV-019D independent recomputation, decision packet, and Spark scaling evidence.')
