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
    if manifest.get("evidence_id") != "REC-EV-017":
        errors.append("manifest evidence_id must be REC-EV-017")
    protocol = manifest.get("protocol", {})
    if protocol.get("positive_injection") is not False or protocol.get("raw_user_id_tracked") is not False:
        errors.append("REC-EV-017 must remain positive-noninjected and deidentified")
    if protocol.get("tag_content", {}).get("evaluation_user_tag_contributions_excluded") is not True:
        errors.append("evaluation user tag contributions must remain excluded")

    artifacts: dict[str, Path] = {}
    for name, record in manifest.get("artifacts", {}).items():
        path = project_path(record["path"])
        artifacts[name] = path
        if not path.is_file():
            errors.append(f"missing {name} artifact")
        elif path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            errors.append(f"{name} artifact checksum mismatch")

    result: dict = {}
    if "result" in artifacts and artifacts["result"].is_file():
        result = json.loads(artifacts["result"].read_text(encoding="utf-8"))
        if result.get("candidate_universe") != 50_977:
            errors.append("candidate universe must remain 50,977")
        case = result.get("case", {})
        if case.get("raw_user_id_tracked") is not False or case.get("selection_uses_model_outcome") is not False:
            errors.append("case A must be deidentified and outcome-independent")
        if len(case.get("movie_associations", [])) != 3:
            errors.append("case A must have three predeclared movie anchors")
        if len(case.get("association_recommendations", {}).get("top10", [])) != 10:
            errors.append("association recommendation must expose ten actual titles")
        if not case.get("genre_associations") or not case.get("tag_profile", {}).get("positive"):
            errors.append("genre relation and tag semantics must both be present")
        ablation = result.get("tag_ablation", {})
        selected = str(ablation.get("selected_alpha"))
        if selected not in ablation.get("validation", {}).get("metrics", {}):
            errors.append("selected alpha must come from Validation grid")
        if set(ablation.get("test", {}).get("metrics", {})) != {"0.0", selected, "1.0"}:
            errors.append("Test must contain Popularity, selected Hybrid, and Tag-only")
        paired = ablation.get("test", {}).get("paired_ndcg_vs_popularity", {}).get(selected, {})
        if paired.get("ci95_low", 0) <= 0:
            errors.append("tracked overall paired signal changed or disappeared")
        positive_segments = ablation.get("test", {}).get("segments", {}).get(selected, {}).get("positive_segment", {})
        if not any(value.get("difference_ci95_high", 0) < 0 for value in positive_segments.values()):
            errors.append("tracked popularity segment regression disappeared")
        decision = result.get("decision", {})
        if decision.get("personal_ranking_champion", "missing") is not None:
            errors.append("REC-EV-017 must not select a champion")
        if decision.get("tag_hybrid_adopted") is not False or decision.get("tag_hybrid_offline_candidate") is not False:
            errors.append("segment-regressed Tag Hybrid must not be adopted or promoted")
        if result.get("tmdb_feature_gate", {}).get("status") != "BLOCKED_NO_FULL_TRAIN_KNOWN_FEATURE_ARTIFACT":
            errors.append("TMDB ablation must remain blocked without full feature artifact")

    if "evidence" in artifacts and artifacts["evidence"].is_file():
        report = artifacts["evidence"].read_text(encoding="utf-8")
        for phrase in (
            "사용자 A의 영화 anchor → 연관 영화",
            "사용자 A의 선호 장르 → 다른 장르",
            "장르 밖의 자유 태그 취향",
            "Tag Hybrid aggregate",
            "전체 개선이 어느 구간에서 발생했는지를 숨기지 않는다",
        ):
            if phrase not in report:
                errors.append(f"report missing section: {phrase}")

    if errors:
        raise SystemExit("REC-EV-017 verification failed:\n- " + "\n- ".join(errors))
    print("REC-EV-017 verification passed: relation anchors, tag cutoff, paired signal, segment regression, and TMDB gate are intact.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=PROJECT / "docs/recommendation/evidence/manifests/rec-ev-017.json",
    )
    args = parser.parse_args()
    run(args.manifest if args.manifest.is_absolute() else PROJECT / args.manifest)
