from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_reason(
    *, feature_active: bool, contribution: float, rank_effect: bool,
    provenance_valid: bool, policy_version_match: bool = True, sensitive_evidence: bool = False,
) -> tuple[str, str]:
    if sensitive_evidence:
        return "BLOCKED", "SENSITIVE_EVIDENCE"
    if not provenance_valid:
        return "BLOCKED", "PROVENANCE_INVALID"
    if not policy_version_match:
        return "BLOCKED", "POLICY_VERSION_MISMATCH"
    if not feature_active:
        return "BLOCKED", "FEATURE_NOT_IN_ACTIVE_POLICY"
    if contribution <= 0:
        return "BLOCKED", "NON_POSITIVE_CONTRIBUTION"
    if not rank_effect:
        return "BLOCKED", "NO_RANK_EFFECT"
    return "EMITTABLE_CANDIDATE", "FAITHFUL_SCORE_AND_RANK_EFFECT"


def artifact(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def main() -> None:
    parser = argparse.ArgumentParser(description="REC-EV-006 structured reason faithfulness evidence")
    parser.add_argument("--rec-ev-004-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tracked-result", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--typed-contract", type=Path, required=True)
    parser.add_argument("--failure-fixtures", type=Path, required=True)
    args = parser.parse_args()

    source_manifest = json.loads(args.rec_ev_004_manifest.read_text(encoding="utf-8"))
    if source_manifest.get("evidence_id") != "REC-EV-004" or source_manifest["validation"]["status"] != "PASS":
        raise RuntimeError("REC-EV-004 source is not verified")
    for record in source_manifest["artifacts"].values():
        if record.get("tracked") is False:
            continue
        path = Path(record["path"])
        if not path.is_file() or path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise RuntimeError("REC-EV-004 artifact checksum mismatch")
    results = json.loads(Path(source_manifest["artifacts"]["aggregate_results"]["path"]).read_text(encoding="utf-8"))
    provenance = json.loads(Path(source_manifest["artifacts"]["reason_provenance"]["path"]).read_text(encoding="utf-8"))
    analysis = results.get("reason_analysis")
    if not analysis or provenance.get("reasonUiApproved") is not False:
        raise RuntimeError("actual reason contribution analysis is unavailable")

    total = int(analysis["recommendations"])
    coverage: dict[str, Any] = {}
    for code, values in analysis["reason_coverage"].items():
        positive = int(values["positive_contribution"])
        effect = int(values["rank_effect"])
        coverage[code] = {
            "evaluated_recommendations": total,
            "positive_contribution": positive,
            "positive_contribution_coverage": round(positive / total, 6),
            "faithful_rank_effect": effect,
            "emittable_candidate_coverage": round(effect / total, 6),
            "blocked_no_rank_effect": max(0, positive - effect),
            "blocked_feature_inactive": total if positive == 0 else 0,
        }

    full = analysis["ablation_metrics"]["FULL"]
    ablation = {}
    for name, values in analysis["ablation_metrics"].items():
        ablation[name] = {
            "ndcg_at_10": values["ndcg_at_10"],
            "recall_at_10": values["recall_at_10"],
            "novelty_bits": values["novelty_bits"],
            "intra_list_diversity": values["intra_list_diversity"],
            "delta_ndcg_vs_full": round(values["ndcg_at_10"] - full["ndcg_at_10"], 6),
            "delta_novelty_vs_full": round(values["novelty_bits"] - full["novelty_bits"], 6),
            "delta_diversity_vs_full": round(values["intra_list_diversity"] - full["intra_list_diversity"], 6),
        }

    typed_contract = {
        "schemaVersion": 1,
        "contractId": "REC_REASON_FAITHFULNESS_V1",
        "sourceEvidence": "REC-EV-004",
        "uiCopyApproved": False,
        "displayCountApproved": False,
        "states": ["EMITTABLE_CANDIDATE", "BLOCKED"],
        "emissionRule": {
            "allOf": ["feature active in exact scoring policy", "positive score contribution",
                      "rank or selected-position effect under single-feature ablation",
                      "matching policy/provenance version", "no sensitive evidence"],
            "note": "EMITTABLE_CANDIDATE is structured evidence for REC-EV-008, not UI display approval",
        },
        "reasonTypes": [
            {"code": "POPULARITY_BASELINE", "feature": "BAYESIAN_POPULARITY", "active": True,
             "allowedState": "EMITTABLE_CANDIDATE"},
            {"code": "LESS_POPULAR_DISCOVERY", "feature": "NOVELTY_PRIOR", "active": True,
             "allowedState": "EMITTABLE_CANDIDATE_IF_ABLATION_EFFECT"},
            {"code": "LIST_DIVERSITY", "feature": "MARGINAL_GENRE_DIVERSITY", "active": True,
             "allowedState": "EMITTABLE_CANDIDATE_IF_ABLATION_EFFECT"},
            {"code": "GENRE_AFFINITY", "feature": "GENRE_AFFINITY", "active": False,
             "allowedState": "BLOCKED_FEATURE_NOT_IN_ACTIVE_POLICY"},
        ],
        "blockedCodes": ["SENSITIVE_EVIDENCE", "PROVENANCE_INVALID", "FEATURE_NOT_IN_ACTIVE_POLICY",
                         "NON_POSITIVE_CONTRIBUTION", "NO_RANK_EFFECT", "POLICY_VERSION_MISMATCH"],
        "prohibitedOutputFields": ["userId", "movieLensUserId", "rawRating", "rawFeatureVector",
                                   "filesystemPath", "token", "uiCopy", "displayCount"],
    }
    fixtures = {
        "schemaVersion": 1,
        "contractId": "REC_REASON_FAITHFULNESS_V1",
        "containsRawIds": False,
        "fixtures": [
            {"id": "FX-REASON-VALID-POPULARITY", "input": {"featureActive": True, "contribution": 0.8,
             "rankEffect": True, "provenanceValid": True, "sensitiveEvidence": False},
             "expected": {"state": "EMITTABLE_CANDIDATE", "code": "FAITHFUL_SCORE_AND_RANK_EFFECT"}},
            {"id": "FX-REASON-INACTIVE-GENRE", "input": {"featureActive": False, "contribution": 0.7,
             "rankEffect": True, "provenanceValid": True, "sensitiveEvidence": False},
             "expected": {"state": "BLOCKED", "code": "FEATURE_NOT_IN_ACTIVE_POLICY"}},
            {"id": "FX-REASON-ZERO-CONTRIBUTION", "input": {"featureActive": True, "contribution": 0.0,
             "rankEffect": False, "provenanceValid": True, "sensitiveEvidence": False},
             "expected": {"state": "BLOCKED", "code": "NON_POSITIVE_CONTRIBUTION"}},
            {"id": "FX-REASON-NO-RANK-EFFECT", "input": {"featureActive": True, "contribution": 0.1,
             "rankEffect": False, "provenanceValid": True, "sensitiveEvidence": False},
             "expected": {"state": "BLOCKED", "code": "NO_RANK_EFFECT"}},
            {"id": "FX-REASON-BAD-PROVENANCE", "input": {"featureActive": True, "contribution": 0.2,
             "rankEffect": True, "provenanceValid": False, "policyVersionMatch": True, "sensitiveEvidence": False},
             "expected": {"state": "BLOCKED", "code": "PROVENANCE_INVALID"}},
            {"id": "FX-REASON-POLICY-VERSION-MISMATCH", "input": {"featureActive": True, "contribution": 0.2,
             "rankEffect": True, "provenanceValid": True, "policyVersionMatch": False, "sensitiveEvidence": False},
             "expected": {"state": "BLOCKED", "code": "POLICY_VERSION_MISMATCH"}},
            {"id": "FX-REASON-SENSITIVE", "input": {"featureActive": True, "contribution": 0.2,
             "rankEffect": True, "provenanceValid": True, "policyVersionMatch": True, "sensitiveEvidence": True},
             "expected": {"state": "BLOCKED", "code": "SENSITIVE_EVIDENCE"}},
        ],
    }
    for fixture in fixtures["fixtures"]:
        inputs = fixture["input"]
        actual = classify_reason(
            feature_active=inputs["featureActive"], contribution=inputs["contribution"],
            rank_effect=inputs["rankEffect"], provenance_valid=inputs["provenanceValid"],
            policy_version_match=inputs.get("policyVersionMatch", True),
            sensitive_evidence=inputs["sensitiveEvidence"],
        )
        if actual != (fixture["expected"]["state"], fixture["expected"]["code"]):
            raise RuntimeError("reason failure fixture does not match classifier")

    args.typed_contract.parent.mkdir(parents=True, exist_ok=True)
    args.failure_fixtures.parent.mkdir(parents=True, exist_ok=True)
    args.typed_contract.write_text(json.dumps(typed_contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.failure_fixtures.write_text(json.dumps(fixtures, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result_path = args.output_dir / "reason-faithfulness-results.json"
    result = {"schema_version": 1, "evidence_id": "REC-EV-006", "policy": analysis["policy"],
              "recommendations": total, "coverage": coverage, "ablation": ablation,
              "by_position": analysis["by_position"]}
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.tracked_result.parent.mkdir(parents=True, exist_ok=True)
    args.tracked_result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1, "evidence_id": "REC-EV-006",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {"rec_ev_004_manifest": str(args.rec_ev_004_manifest),
                   "rec_ev_004_manifest_sha256": sha256(args.rec_ev_004_manifest),
                   "candidate_scope": source_manifest["protocol"]["candidate_scope"],
                   "test_used": True},
        "protocol": {"version": "rec-ev-006-score-contribution-ablation-v1",
                     "faithfulness_contract": "REC_REASON_FAITHFULNESS_V1",
                     "emission_requires_rank_effect": True},
        "metrics": result,
        "artifacts": {"results": artifact(args.tracked_result),
                      "large_output_copy": {"path": str(result_path), "tracked": False},
                      "typed_contract": artifact(args.typed_contract),
                      "failure_fixtures": artifact(args.failure_fixtures)},
        "validation": {"status": "PASS", "actual_rec_ev_004_contributions_used": True,
                       "single_feature_ablations_present": True, "failure_fixtures_pass": True,
                       "raw_ids_tracked": False, "reason_ui_approved": False},
        "conclusion": {"reason_ui_decision": "WAITING_FOR_REC_EV_008",
                       "display_count": None, "ui_copy": None, "ranking_champion": None},
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(evidence_markdown(manifest), encoding="utf-8")


def evidence_markdown(manifest: dict[str, Any]) -> str:
    result = manifest["metrics"]
    rows = []
    for code, values in result["coverage"].items():
        rows.append(f"| `{code}` | {values['positive_contribution_coverage']:.2%} | "
                    f"{values['emittable_candidate_coverage']:.2%} | {values['blocked_no_rank_effect']:,} | "
                    f"{values['blocked_feature_inactive']:,} |")
    ablation_rows = []
    for name, values in result["ablation"].items():
        ablation_rows.append(f"| `{name}` | {values['ndcg_at_10']:.4f} | {values['novelty_bits']:.3f} | "
                             f"{values['intra_list_diversity']:.4f} | {values['delta_ndcg_vs_full']:+.4f} |")
    return f"""# REC-EV-006 — 구조화 추천 이유 coverage·faithfulness

> 상태: `COMPLETED_OFFLINE_EVIDENCE`  
> Source: `REC-EV-004` actual Test scoring contributions  
> Reason UI approved: `NO`

## 1. 결론

{result['recommendations']:,}개 sampled Test recommendation position에서 실제 score contribution과
single-feature ablation rank effect를 함께 검사했다. feature가 존재하거나 점수 기여가 양수라는 이유만으로
표시 가능하다고 하지 않는다. active policy, 양의 contribution, rank/position effect, provenance version,
민감정보 부재를 모두 만족한 경우만 `EMITTABLE_CANDIDATE`이며, 이는 REC-EV-008 화면 비교 입력이지
실제 UI 표시 승인이 아니다.

## 2. 이유별 coverage

| Reason | Positive contribution | Emittable candidate | Blocked: no rank effect | Blocked: inactive |
| --- | --- | --- | --- | --- |
{chr(10).join(rows)}

`GENRE_AFFINITY`는 REC-EV-004의 선택 정책이 Popularity 기반이므로 차단한다. novelty/diversity도 실제
ablation에서 위치 효과가 없는 행은 `NO_RANK_EFFECT`로 차단한다.

## 3. Ablation

| Variant | NDCG@10 | Novelty bits | Diversity | ΔNDCG vs full |
| --- | --- | --- | --- | --- |
{chr(10).join(ablation_rows)}

이 수치는 `{manifest['source']['candidate_scope']}` 범위이며 full-catalog나 온라인 설명 만족 근거가 아니다.

## 4. Typed contract와 실패 fixture

- `rec-ev-006-reason-contract.json`: `EMITTABLE_CANDIDATE`와 `BLOCKED` 상태, 차단 code, 금지 필드
- `rec-ev-006-failure-fixtures.json`: inactive feature, zero contribution, no rank effect, bad provenance,
  sensitive evidence를 fail-closed 검증
- raw MovieLens user/movie ID, Rating row, feature vector, token, path는 tracked artifact에 없다.

## 5. 재현

```powershell
py -3.12 scripts/recommendation_reason_faithfulness.py `
  --rec-ev-004-manifest docs/recommendation/evidence/manifests/rec-ev-004.json `
  --output-dir outputs/recommendation-evidence/rec-ev-006 `
  --tracked-result docs/recommendation/evidence/results/rec-ev-006-aggregate.json `
  --manifest docs/recommendation/evidence/manifests/rec-ev-006.json `
  --evidence docs/recommendation/evidence/REC-EV-006-reason-faithfulness.md `
  --typed-contract docs/recommendation/evidence/manifests/rec-ev-006-reason-contract.json `
  --failure-fixtures docs/recommendation/evidence/manifests/rec-ev-006-failure-fixtures.json

py -3.12 scripts/verify_recommendation_reason_faithfulness.py `
  --manifest docs/recommendation/evidence/manifests/rec-ev-006.json
```

## 6. 남은 Gate

UI 문구, 이유 표시 개수, 펼치기, reason ordering은 REC-EV-008에서 비교하고 제품 소유자가 결정한다.
`EMITTABLE_CANDIDATE`를 공개 reason으로 자동 승격하지 않는다.
"""


if __name__ == "__main__":
    main()
