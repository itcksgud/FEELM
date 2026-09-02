#!/usr/bin/env python3
"""Verify tracked REC-EV-019C LightFM Linux smoke evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs/recommendation/contracts/rec-ev-019c-validation-artifacts.json"
DEFAULT_MANIFEST = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019c-lightfm-linux-smoke.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify_manifest(manifest_path: Path, *, root: Path = ROOT) -> dict[str, Any]:
    contract_path = root / CONTRACT_PATH.relative_to(ROOT)
    contract = read_json(contract_path)
    dependency = contract["models"]["B8_LIGHTFM"]["dependency"]
    manifest = read_json(manifest_path)
    contract_sha = sha256_file(contract_path)
    lock_sha = sha256_file(root / dependency["hash_lock"])

    require(manifest.get("schema_version") == 1, "unexpected dependency smoke schema")
    require(manifest.get("evidence_id") == "REC-EV-019C-LIGHTFM-LINUX-SMOKE", "unexpected evidence id")
    require(manifest.get("status") == "PASS_DEPENDENCY_SMOKE", "dependency smoke is not PASS")
    require(manifest.get("contract_sha256") == contract_sha, "dependency manifest contract hash is stale")
    require(manifest.get("runtime_lock_sha256") == lock_sha, "dependency runtime lock hash is stale")
    artifacts = manifest.get("artifacts", [])
    require(len(artifacts) == 1, "dependency smoke must track one result")
    artifact = artifacts[0]
    require(artifact.get("path") == contract["dependency_smoke_artifacts"]["result"], "result path changed")
    result_path = (root / artifact["path"]).resolve()
    try:
        result_path.relative_to(root.resolve())
    except ValueError as error:
        raise RuntimeError("dependency smoke result escapes repository") from error
    require(result_path.is_file(), "dependency smoke result is missing")
    require(artifact.get("bytes") == result_path.stat().st_size, "dependency result byte count mismatch")
    require(artifact.get("sha256") == sha256_file(result_path), "dependency result checksum mismatch")

    result = read_json(result_path)
    require(result.get("status") == "PASS_DEPENDENCY_SMOKE", "dependency result is not PASS")
    require(result.get("contract_sha256") == contract_sha, "dependency result contract hash is stale")
    require(result.get("runtime_lock_sha256") == lock_sha, "dependency result lock hash is stale")
    require(result.get("runtime_image") == dependency["runtime_image"], "dependency runtime image changed")
    require(result.get("distribution") == "lightfm-next", "dependency distribution changed")
    require(result.get("distribution_version") == "1.19.0", "dependency version changed")
    require(result.get("import_package") == "lightfm", "dependency import changed")
    require(result.get("loss") == "logistic", "dependency smoke used pairwise loss")
    require(result.get("signed_interaction_values") == [-1.0, 1.0], "signed labels changed")
    for check in contract["dependency_smoke_artifacts"]["required_checks"]:
        require(result.get("checks", {}).get(check) is True, f"dependency smoke check failed: {check}")
    for key in ("real_validation_executed", "locked_test_opened", "product_policy_changed"):
        require(result.get(key) is False, f"dependency smoke crossed boundary: {key}")
    require(result.get("product_champion") is None, "dependency smoke selected a champion")
    require(
        result.get("next_gate") == "RESOURCE_DRY_RUN_AND_CONTRACT_AMENDMENT_REVIEW",
        "dependency smoke next Gate changed",
    )
    adoption = manifest.get("adoption", {})
    require(adoption.get("champion") is None, "dependency manifest selected a champion")
    require(adoption.get("product_policy_changed") is False, "dependency manifest changed product policy")
    require(adoption.get("real_validation_authorized") is False, "dependency manifest authorized Validation")
    return {
        "status": "PASS",
        "evidence_id": result["evidence_id"],
        "distribution": f"{result['distribution']}=={result['distribution_version']}",
        "checks": len(contract["dependency_smoke_artifacts"]["required_checks"]),
        "real_validation_executed": False,
        "locked_test_opened": False,
        "next_gate": result["next_gate"],
        "product_champion": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify REC-EV-019C LightFM dependency smoke")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_manifest(args.manifest.resolve()), sort_keys=True))
        return 0
    except Exception as error:
        print(f"REC-EV-019C LightFM smoke verification failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
