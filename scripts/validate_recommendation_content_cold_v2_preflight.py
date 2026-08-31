from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from recommendation_evidence_paths import artifact_matches, repository_path
from recommendation_protocol_v4 import custom_squared_rank_alpha, equal_share_request_weight, linear_ndcg_at_5


REPO_ROOT = Path(__file__).resolve().parents[1]


def validate(manifest_path: Path) -> list[str]:
    errors: list[str] = []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("locked_test_opened") is not False:
        errors.append("Locked Test must remain unopened")
    for record in manifest.get("artifacts", []):
        if not artifact_matches(REPO_ROOT / repository_path(record["path"]), record):
            errors.append(f"artifact checksum mismatch: {record['path']}")
    root = REPO_ROOT / "outputs/recommendation-evidence/rec-ev-021p"
    audit = json.loads((root / "item-firewall-audit.json").read_text(encoding="utf-8"))
    if audit.get("firewall_status") != "PASS":
        errors.append("item firewall did not pass")
    if audit.get("strict_item_locked_test_interactions_read") is not False:
        errors.append("strict Locked Test interactions were read")
    if audit.get("strict_item_validation_interactions_read") is not False:
        errors.append("strict Item Validation interactions were read before prediction lock")
    if audit.get("target_selection_used_locked_test_rating_value") is not False:
        errors.append("target selection used Locked Test outcome")
    panels = pd.read_parquet(root / "panel-sample-summary.parquet")
    if set(panels["panel"].unique()) != {"PANEL_5P", "PANEL_20P", "PANEL_100P"}:
        errors.append("all three density panels must be present")
    if set(panels["firewall_scope"].unique()) != {"SAFE_INTERSECTION_ITEM_TRAIN_X_DENSITY_VALIDATION"}:
        errors.append("panel contains a protected split intersection")
    observed, expected, alpha = custom_squared_rank_alpha([[0, 0], [0, 1, 2]])
    if not (abs(observed - 1.2) < 1e-12 and abs(expected - 1.6) < 1e-12 and abs(alpha - 0.25) < 1e-12):
        errors.append("custom alpha golden fixture mismatch")
    dcg, idcg, ndcg = linear_ndcg_at_5([0.5, 1.0, 0.0, 0.0, 0.0], [1.0, 0.5, 0.0, 0.0, 0.0])
    if not (
        abs(dcg - 1.1309297535714575) < 1e-12
        and abs(idcg - 1.3154648767857289) < 1e-12
        and abs(ndcg - 0.8597186998521972) < 1e-12
    ):
        errors.append("linear NDCG golden fixture mismatch")
    if equal_share_request_weight(2.0, [0.0, 1.0, 2.0]) != 2.0:
        errors.append("equal-share request weight fixture mismatch")
    if equal_share_request_weight(2.0, []) is not None:
        errors.append("empty membership must be NULL")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "docs/recommendation/evidence/manifests/rec-ev-021p.json")
    args = parser.parse_args()
    errors = validate(args.manifest.resolve())
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("PASS: REC-EV-021P artifacts preserve the firewall and all statistical golden fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
