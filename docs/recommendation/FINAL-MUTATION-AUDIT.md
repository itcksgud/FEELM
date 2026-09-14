# Discovery v2 final and mutation independent result audit

Status: DRAFT — research evidence, not a product contract. Audit outcome: **PASS**.

This completes the final/freeze/actual-serving/mutation portion of the earlier
audit handoff. The descriptive report and separate profile-controls audit are
owned by another reviewer and are not included in this PASS.

Source: `C:/higher/projects/FEELM-standalone/.codex-tmp/fixed-k8-discovery-v2-20260913`.
Run: `outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2`.
Mutation source: `C:/higher/projects/FEELM-standalone/.codex-tmp/discovery-v2-prefix-drift-20260913/outputs/mutation-prefix-drift-r2`.
All source inputs remained read-only. No fitting, policy selection, source-result
mutation, commit, push, deployment, or parsing of future target-rating values.

## Verified outcome

- The exact reviewed source, code snapshots, preparation/cluster/final inventories,
  previous frozen top8 source, final bundle material and selected cluster arrays
  match their pins. Source inventories and code pins were checked again afterward.
- All237,817 assignment rows match prepared IDs and metadata; CSV and Parquet match.
  All16 unique group descriptors, group labels and empirical genre/keyword counts
  were independently reconstructed. The JSON audit includes genre frequencies for
  presentation authors; a rare secondary genre is not a dominant group meaning.
- Independent preprocessing and fixed-axis nearest-center calculations matched
  all237,817 rows in batches6553, all237,817 in reverse order after reload with
  batches4093, and1,024 shuffled rows with batches17. After removing the exact9
  fixture IDs, all237,808 remaining classifications matched in batches8191.
- Runtime append preserved the existing classifications and Q scores. The null/OOV
  fixtures have no trained content support. Tombstones remove candidates while
  retaining metadata for historical input. Invalid removal is atomic; empty input
  and cap0 states are correct. Frozen bundle, C/m and rule material are unchanged.
- All114 saved final profile rank perturbations match independent reconstruction.
- All20 users ×3 mutation scenarios match:297 full-pool/prefix rows,297 group-delta
  rows,60 user transition comparisons, candidate orders, similarity values and all
  summary cells. Append changes2 full pools and0 prefixes; append→delete changes56
  full pools and1 prefix. Candidate lists are unchanged for all20 users.

Actual serving was independently invoked with the pinned research runtime and
the frozen predictor. This is distinct from an independently implemented predictor;
the prior predictor native/source parity audit remains separate evidence.

| User | Candidate movies | Returned movies | Maximum Top10 score error | ALS head rows | GBT head rows |
|---|---:|---:|---:|---:|---:|
|1215|50|10|0|36|50|
|2607|50|10|0|37|50|
|2834|50|10|0|29|50|

Exactly3 predictor request calls scored150 candidate movie rows. Because shrink100
can compute both heads, these correspond to102 ALS and150 GBT head rows. All Top10
movie IDs and ranking scores match CHECK exactly. The audit took79.0466seconds;
this is an audit duration, not a service latency measurement.

## Scope limits

The final bundle is the existing technical exemplar
`v1-fixed16:group:mean:B50:Q25` with `shrink100`. This audit does not freeze a
different childK. The mutation fixture is mild and finite: zero candidate changes
here do not prove absence of drift after future catalog updates. Human discovery
value, Korean user satisfaction, production behavior and untouched temporal test
performance remain unmeasured.

## Evidence and reproduction

- Result: `outputs/discovery-v2-result-audit/final-mutation-audit.json`,
  SHA256 `928941e21d485e1e2a0692651142867cfca1f949dde4fd4b2909eb7bb2d0c636`,14994bytes.
- Executed code: `scripts/audit_dv2_final_mutation.py`,
  SHA256 `4f4180539fdef1dca27cbe91ca69e62be7f03a3d54d4836e90ab0bf689f4d337`,34601bytes.
- Independent static pre-execution reviewer: `/root/service_transition_decision`;
  exact pin recorded in `final-mutation-audit-execution-review.json`.
- Five audit counterexample tests passed: geometric tie ID, all-viewed underseen
  denominator and quota/budget, negative/no-history profile, ordered drift versus
  set retention, Q ties and tombstones.
- Log: `outputs/discovery-v2-result-audit/final-mutation-audit-attempt1.log`.
- Source final seal:
  `84916471ba353e29e82948dca977b59195fd0d31bab5e99cbd6ceab1070b7730`.

```powershell
py -3.12 -B -X utf8 -m unittest discover -s scripts -p test_audit_dv2_final_mutation.py -v
py -3.12 -B -X utf8 scripts/audit_dv2_final_mutation.py --execute --review docs/recommendation/final-mutation-audit-execution-review.json
```

The execution command deliberately refuses to overwrite the existing audit JSON.
Preserve this run; a future replay needs a separately reviewed fresh output path.
