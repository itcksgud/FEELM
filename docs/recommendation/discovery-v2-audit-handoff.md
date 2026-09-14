# Discovery v2 audit handoff

Status: completed sweep/check audits; remaining final work transferred by user instruction.

The user directed final service work and remaining reviews to task `01a07a26-f229-7260-b38c-bc6bf0fd7b2f`. The active CHECK audit was allowed to finish naturally. It exited0. No audit process remains running. Final/report/mutation post-execution audit and independent serving replay were not started.

## Read-only source and audit ownership

- Experiment source: `C:/higher/projects/FEELM-standalone/.codex-tmp/fixed-k8-discovery-v2-20260913`.
- Source run: `outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2`.
- Audit worktree: `C:/higher/projects/FEELM-standalone/.codex-tmp/discovery-v2-result-audit-20260913`.
- Audit branch: `research/discovery-v2-result-audit-20260913`.
- Only the audit worktree was written by this reviewer. No commits, push, deployment, model fitting or source-output changes.

## Completed results

SWEEP: PASS,15,390 requests,1,085,005 candidate rows,10,575 nonempty requests,90 users. All actual E_u/prefix/candidate ordering and hard constraints were reconstructed; saved affine rankings, model accounting, observed metrics,171 summaries and168-primary finite gate/Pareto/selection decisions matched. Audit loop474.32s; trained-model calls0.

- `outputs/discovery-v2-result-audit/sweep-audit.json`: SHA256 `fdc91f285c591cc2b6107b34edc5d3aac5197d519efb3317af460a42cfaa83f6`,194579bytes.
- Original execution code snapshot `outputs/discovery-v2-result-audit/sweep-audit-code.py`: SHA256 `f6cdf1146abe2510fd5be581b30a88c0006004d418f167883a2017c5502945e0`,34843bytes.
- Source sweep seal: `be602f2b41b27068e07b172ac381ab5dc4c1a89e2ab7c7efc3106274e6adf153`.

CHECK: PASS,13,328 requests=11,528 actual main/reference requests+1,800 declared sensitivities;74,431,901 candidate rows;670,218 prefix-drift rows;180 users. Candidate rows are summed over requests, not unique catalog movies. All candidate/order/E_u/prefix/rank/accounting checks matched. Reference links were independently reconstructed including user/finalist rotation, exact hierarchy/space/profile/B/quota/seed, original actual execution and reuse flags. Every prefix-drift row and genre/keyword/collection readout was recomputed. Audit loop553.08s; trained-model calls0.

- `outputs/discovery-v2-result-audit/check-audit.json`: SHA256 `0c99048937f3e9747c4403954b3215e78c6ba8639b20276729e2bc3c23a7dd35`,14196bytes.
- `outputs/discovery-v2-result-audit/check-audit-attempt1.log`: SHA256 `ce530b231ea529e4c7f2c720e3a535b5d7706499a25a6b2b9af59af0b9251656`.
- Current and preserved execution code `scripts/audit_dv2_retrieval_results.py` / `outputs/discovery-v2-result-audit/check-audit-code.py`: SHA256 `49a1449ff836aaf90ac2f13f0052d6537c124db84b77a84a414c701929b3ba4e`,39483bytes.
- Tests `scripts/test_audit_dv2_retrieval_results.py`: SHA256 `697e946368ada5fc32097f67226a74793f60e9921739259cca5e8f386b29f29a`,6319bytes. Eight synthetic tests passed independently, including deliberately wrong reference links/reuse flags and prefix-drift counts.
- Source check seal: `e308b45c19199aca393a60a2dc47d7bb20bdda80005957b1bbef61cd8a6048bb`,116668bytes.

Both audit code versions received independent static cross-review from the asset reviewer. Source code pins and complete source-stage inventories were verified before and after execution. Saved score and latency values are not independent native-model re-inference or timing measurements; that distinction remains explicit.

## Frozen decision and interpretation

- Technical candidate remains `v1-fixed16:group:mean:B50:Q25`, without check-user reselection. Check VALID124,comparable/full10=118/124=95.1613%; overlap.8771186,p95 absolute gap.03863088,worst positive loss.08146334,p95 saved warm latency15.13629ms. All prescribed gates passed, with return coverage near the95% threshold.
- K128 meanB100Q25 andK256 meanB50Q25 also passed all check gates and returned10 for124/124. They were retained, not rejected by a minimum100-member rule.
- Main check ML TRAIN<=20 Top1 is99/118=83.90%; TMDb votes<=20 Top1 is0. A passing shrink alternative does not eliminate all low-ML-support exposure.
- The frozen best-approximation `v1-fixed16:group:multi4:B200:Q10` has a structural degeneracy:16*10<=160<200, so every representative method collects all legal prefixes. Near1e-18 differences arise from floating sum order; they are not evidence of semantic superiority.
- Human discovery value, fresh temporal generalization and production readiness remain unmeasured.

## Reproduction

Run from the audit worktree. The command reads completed source results and prints progress and final JSON; it does not write source artifacts.

```powershell
py -3.12 -B -X utf8 -m unittest discover -s scripts -p test_audit_dv2_retrieval_results.py -v
py -3.12 -B -X utf8 scripts/audit_dv2_retrieval_results.py --stage check --execute
```

For the exact original sweep implementation, use the preserved `sweep-audit-code.py` snapshot with `--stage sweep --execute`. The current script additionally checks novelty readouts and expanded CHECK-only reference/drift semantics.

## Remaining work for the designated task

Parent reported final seal `84916471ba353e29e82948dca977b59195fd0d31bab5e99cbd6ceab1070b7730` and completed report outputs. Their post-execution final/report audit is pending here. Prior final/runtime and report code pre-execution reviews passed, but that is distinct from auditing the generated outputs. Proposed independent actual serving replay for users1215/2607/2834 was not executed.

Parent also reported an approved inference-free mutation diagnostic under `discovery-v2-prefix-drift-20260913/outputs/mutation-prefix-drift-r2`. Its result/hash/numerical-scope review is pending here. Do not claim these remaining audits passed based on the completed sweep/check audits.
