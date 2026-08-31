from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_path(value: str) -> Path:
    path = Path(value.replace("\\", "/"))
    return path if path.is_absolute() else PROJECT / path


def run(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if manifest.get("evidence_id") != "REC-EV-016":
        errors.append("manifest evidence_id must be REC-EV-016")
    if manifest.get("conclusion", {}).get("personal_ranking_champion", "missing") is not None:
        errors.append("single-user case study must not select a champion")
    if manifest.get("protocol", {}).get("raw_user_id_tracked") is not False:
        errors.append("raw MovieLens user id must not be tracked")

    artifacts: dict[str, Path] = {}
    for name, record in manifest.get("artifacts", {}).items():
        path = project_path(record["path"])
        artifacts[name] = path
        if not path.is_file():
            errors.append(f"missing {name} artifact: {path}")
            continue
        if path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            errors.append(f"{name} artifact checksum mismatch")

    if "result" in artifacts and artifacts["result"].is_file():
        result = json.loads(artifacts["result"].read_text(encoding="utf-8"))
        case = result.get("case", {})
        if case.get("raw_user_id_tracked") is not False or case.get("selection_uses_model_outcome") is not False:
            errors.append("case selection must be deidentified and outcome-independent")
        if case.get("eligible_intersection_users", 0) <= 0:
            errors.append("eligible intersection must be non-empty")
        warm = result.get("warm_full_catalog", {})
        cold = result.get("cold_start_k10", {})
        if warm.get("candidate_universe") != 50_977 or cold.get("candidate_universe") != 50_977:
            errors.append("both diagnostics must use the 50,977 Train-known universe")
        if warm.get("positive_injection") is not False or cold.get("positive_injection") is not False:
            errors.append("held-out positives must not be injected")
        expected_warm = {
            "POPULARITY", "CONTENT_GENRE", "HYBRID_CONTENT_25", "ALS_WARM", "EXPLORE_05_ON_POPULARITY"
        }
        if set(warm.get("policies", {})) != expected_warm:
            errors.append("warm policy set changed")
        if set(cold.get("policies", {})) != {"POPULARITY", "FOLDIN_BLEND_ALPHA_0_2"}:
            errors.append("cold policy set changed")
        for scope in (warm, cold):
            for name, policy in scope.get("policies", {}).items():
                top = policy.get("top10", [])
                if len(top) != 10 or len({item["title"] for item in top}) != 10:
                    errors.append(f"{name} must expose ten distinct movie titles")
        if result.get("decision", {}).get("case_can_select_champion") is not False:
            errors.append("case_can_select_champion must remain false")

    if "evidence" in artifacts and artifacts["evidence"].is_file():
        report = artifacts["evidence"].read_text(encoding="utf-8")
        for phrase in (
            "어떤 데이터를 어떻게 나눴나",
            "사용자 A의 취향·평점 성향",
            "같은 사용자, 같은 Test 정답, 알고리즘만 변경",
            "이 한 사람에서 실제로 드러난 변화",
            "무엇을 채용했고 무엇을 버렸나",
        ):
            if phrase not in report:
                errors.append(f"evidence report is missing section: {phrase}")

    if errors:
        raise SystemExit("REC-EV-016 verification failed:\n- " + "\n- ".join(errors))
    print("REC-EV-016 verification passed: deidentified fixed user, full-catalog policy lists, and adoption boundary are intact.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=PROJECT / "docs/recommendation/evidence/manifests/rec-ev-016.json",
    )
    arguments = parser.parse_args()
    run(arguments.manifest if arguments.manifest.is_absolute() else PROJECT / arguments.manifest)
