from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from recommendation_evidence_paths import artifact_matches, repository_path


MANIFEST = Path("docs/recommendation/evidence/manifests/rec-ev-018.json")


def verify() -> dict[str, object]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("evidence_id") != "REC-EV-018":
        raise RuntimeError("unexpected evidence id")
    for name, record in manifest["artifacts"].items():
        path = repository_path(record["path"])
        if not artifact_matches(path, record):
            raise RuntimeError(f"artifact mismatch: {name}")

    result_path = repository_path(manifest["artifacts"]["result"]["path"])
    users_path = repository_path(manifest["artifacts"]["user_results"]["path"])
    result = json.loads(result_path.read_text(encoding="utf-8"))
    users = pd.read_parquet(users_path)
    expected_users = int(result["cohort"]["users"])
    if len(users) != expected_users or not users["user_alias"].is_unique:
        raise RuntimeError("user artifact row contract failed")
    if "user_id" in users.columns:
        raise RuntimeError("raw user id must not be stored")

    for policy in ("hybrid", "tag_content"):
        effect = result["effects_vs_popularity"][policy]
        rate_sum = effect["benefit_rate"] + effect["tie_rate"] + effect["harm_rate"]
        if not np.isclose(rate_sum, 1.0, atol=1e-6):
            raise RuntimeError(f"rank effect rates do not sum to one: {policy}")
        ndcg = effect["top10_ndcg_user_effect"]
        ndcg_sum = ndcg["benefit_rate"] + ndcg["tie_rate"] + ndcg["harm_rate"]
        if not np.isclose(ndcg_sum, 1.0, atol=1e-6):
            raise RuntimeError(f"NDCG effect rates do not sum to one: {policy}")

    for policy in ("popularity", "hybrid", "tag_content"):
        measured = round(float(users[f"{policy}_ndcg_at_10"].mean()), 6)
        if measured != result["metrics"][policy]["ndcg_at_10"]:
            raise RuntimeError(f"NDCG mismatch: {policy}")

    if not all(result["validation"]["aggregate_parity_with_rec_ev_017"].values()):
        raise RuntimeError("REC-EV-017 aggregate parity is not complete")
    if not result["validation"]["raw_user_id_absent"]:
        raise RuntimeError("result raw user validation failed")

    return {
        "status": "PASS",
        "evidence_id": "REC-EV-018",
        "users": expected_users,
        "artifacts": sorted(manifest["artifacts"]),
        "hybrid_rank_effect": {
            key: result["effects_vs_popularity"]["hybrid"][key]
            for key in ("benefit_rate", "tie_rate", "harm_rate")
        },
        "hybrid_top10_effect": result["effects_vs_popularity"]["hybrid"]["top10_ndcg_user_effect"],
    }


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, sort_keys=True))
