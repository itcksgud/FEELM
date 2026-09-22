"""Check a transferred GBT/FM package without DB, Redis, network or private ratings."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
import time


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def verify_files(root):
    root = Path(root).resolve(strict=True)
    manifest = json.loads((root / "handoff.json").read_text(encoding="utf-8"))
    if manifest["format"] != "feelm-v12-server-handoff-v1" or manifest["adoption"] != "NOT_ADOPTED":
        raise ValueError("unsupported package")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != set(manifest["files"]) | {"handoff.json"}:
        raise ValueError("package file inventory mismatch; keep outputs/venv outside this directory")
    for name, record in manifest["files"].items():
        path = (root / name).resolve(strict=True)
        if not path.is_relative_to(root) or path.stat().st_size != record["bytes"] or digest(path) != record["sha256"]:
            raise ValueError(f"package file checksum failed: {name}")
    return manifest


def score_error(actual, expected):
    if len(actual) != len(expected):
        raise AssertionError("score vector length mismatch")
    left = [row["score"] for row in actual]
    right = [row["score"] for row in expected]
    if not all(math.isfinite(value) for value in left + right):
        raise AssertionError("nonfinite inference/expected score")
    error = max((abs(a - b) for a, b in zip(left, right)), default=0.)
    if not math.isfinite(error) or error > 1e-5:
        raise AssertionError(f"prediction parity failed: {error}")
    return error


def verify(root, report_path, family="both"):
    root = Path(root).resolve(strict=True)
    report_path = Path(report_path).resolve()
    if report_path.exists() or report_path.is_relative_to(root):
        raise ValueError("use a NEW report file OUTSIDE the package directory")
    started = time.monotonic()
    manifest = verify_files(root)
    # Prevent __pycache__ modifications to the source-pinned package, including
    # when this script is run without the documented python -B option.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root / "bundle"))
    from service_model_v12 import PersonalScorer, merge_inputs
    scorer = PersonalScorer(root / "bundle", family)
    results = []
    for case in manifest["checks"]:
        request = json.loads((root / case["request"]).read_text(encoding="utf-8"))
        expected = json.loads((root / case["expected"]).read_text(encoding="utf-8"))
        actual = scorer.recommend(request, limit=100)
        merged, excluded = merge_inputs(request)
        if actual["input"] != merged or actual["inputCount"] != case["effective_k"]:
            raise AssertionError("history truncation/precedence mismatch")
        if actual["status"] != expected["status"]:
            raise AssertionError("cold-start status differs")
        model_results = {}
        for name in scorer.families:
            got, want = actual["models"][name], expected["models"][name]
            if [row["movieId"] for row in got["candidates"]] != [row["movieId"] for row in want["candidates"]]:
                raise AssertionError(f"Top100 IDs/order differ: {case['name']} {name}")
            if [row.get("rawRank") for row in got["candidates"]] != [row.get("rawRank") for row in want["candidates"]]:
                raise AssertionError("raw ranks differ")
            error = score_error(got["candidates"], want["candidates"])
            for key in ("qualifiedCount", "candidateCount", "shortfall"):
                if got.get(key) != want.get(key):
                    raise AssertionError(f"{key} mismatch")
            if excluded.intersection(row["movieId"] for row in got["candidates"]):
                raise AssertionError("seen/dismissed film returned")
            model_results[name] = {"top100_equal": True, "max_score_error": error,
                                   "returned": len(got["candidates"])}
        row = {"case": case["name"], "effective_k": actual["inputCount"], "models": model_results}
        results.append(row)
        print(json.dumps(row), flush=True)
    verify_files(root)
    report = {"status": "TRANSFER_INFERENCE_PARITY_PASS", "adoption": "NOT_ADOPTED",
        "platform": platform.platform(), "python": platform.python_version(),
        "package_manifest_sha256": digest(root / "handoff.json"), "cases": results,
        "new_quality_evidence": False, "seconds": time.monotonic() - started}
    with report_path.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2, allow_nan=False)
    print(json.dumps({"status": report["status"], "seconds": report["seconds"]}), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--model", choices=("gbt", "fm", "both"), default="both")
    args = parser.parse_args()
    verify(args.package, args.report, args.model)
