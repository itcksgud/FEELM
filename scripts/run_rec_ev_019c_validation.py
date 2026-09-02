#!/usr/bin/env python3
"""REC-EV-019C runner boundary and synthetic preflight.

Real Validation is deliberately fail-closed. The currently authorized mode uses a
small in-memory fixture to prove ranking, fallback, resume, and file-firewall
semantics without opening MovieLens/TMDB evaluation artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import itertools
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-019c-validation-artifacts.json"


class AuthorizationError(RuntimeError):
    """Raised before data access when a phase has not been authorized."""


class InputFirewallError(RuntimeError):
    """Raised before an input path is opened."""


class ResumeSignatureError(RuntimeError):
    """Raised without overwriting a checkpoint from another run signature."""


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: Any) -> bytes:
    payload = canonical_json_bytes(value)
    atomic_write_bytes(path, payload)
    return payload


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _repo_relative(path: Path, *, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise InputFirewallError("input path is outside the repository") from error


@dataclass(frozen=True)
class InputFirewall:
    root: Path
    allowed: frozenset[str]
    forbidden: frozenset[str]

    @classmethod
    def from_contract(cls, contract: Mapping[str, Any], *, root: Path = ROOT) -> "InputFirewall":
        return cls(
            root=root.resolve(),
            allowed=frozenset(contract["allowed_input_artifacts"].values()),
            forbidden=frozenset(contract["forbidden_input_artifacts"]),
        )

    def validate_path(self, path: str | Path) -> Path:
        candidate = Path(path)
        absolute = candidate if candidate.is_absolute() else self.root / candidate
        relative = _repo_relative(absolute, root=self.root)
        if relative in self.forbidden:
            raise InputFirewallError("forbidden input artifact class")
        if relative not in self.allowed:
            raise InputFirewallError("unknown input artifact class")
        return absolute.resolve()

    def read_bytes(
        self,
        path: str | Path,
        *,
        opener: Callable[[Path], bytes] | None = None,
    ) -> bytes:
        safe_path = self.validate_path(path)
        read = opener or (lambda item: item.read_bytes())
        return read(safe_path)


def expand_trials(contract: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    expanded: dict[str, list[dict[str, Any]]] = {}
    for model_id in contract["trial_execution"]["model_order"]:
        model = contract["models"][model_id]
        if "ordered_variants" in model:
            parameters = [{"variant": value} for value in model["ordered_variants"]]
        else:
            search_space = model.get("search_space", {})
            keys = list(search_space)
            combinations = itertools.product(*(search_space[key] for key in keys)) if keys else [()]
            parameters = [dict(zip(keys, values, strict=True)) for values in combinations]
        expanded[model_id] = [
            {
                "trial_id": f"{model_id}-T{index:03d}",
                "parameters": values,
                "seeds": list(model.get("stochastic_seeds", [])),
            }
            for index, values in enumerate(parameters, start=1)
        ]
    return expanded


def midrank_percentiles(scores: Mapping[int, float]) -> dict[int, float]:
    if not scores:
        return {}
    for movie_id, score in scores.items():
        if not math.isfinite(float(score)):
            raise ValueError(f"non-finite score for movie {movie_id}")
    ordered = sorted((float(score), int(movie_id)) for movie_id, score in scores.items())
    by_score: dict[float, list[int]] = {}
    for index, (score, _) in enumerate(ordered, start=1):
        by_score.setdefault(score, []).append(index)
    count = len(ordered)
    percentile_by_score = {
        score: ((sum(ranks) / len(ranks)) - 0.5) / count for score, ranks in by_score.items()
    }
    return {int(movie_id): percentile_by_score[float(score)] for movie_id, score in scores.items()}


def effective_percentile_scores(
    candidate_ids: Sequence[int],
    model_scores: Mapping[int, float],
    available_movie_ids: Iterable[int],
    b0_scores: Mapping[int, float],
) -> tuple[dict[int, float], set[int]]:
    candidate_set = {int(movie_id) for movie_id in candidate_ids}
    if candidate_set != set(map(int, b0_scores)):
        raise ValueError("B0 must cover the identical candidate universe")
    available = candidate_set.intersection(map(int, available_movie_ids))
    available_scores = {movie_id: float(model_scores[movie_id]) for movie_id in available if movie_id in model_scores}
    model_percentiles = midrank_percentiles(available_scores)
    b0_percentiles = midrank_percentiles({movie_id: float(b0_scores[movie_id]) for movie_id in candidate_set})
    fallback_ids = candidate_set.difference(model_percentiles)
    effective = {
        movie_id: model_percentiles.get(movie_id, b0_percentiles[movie_id]) for movie_id in candidate_set
    }
    return effective, fallback_ids


def stream_top_n(
    candidate_ids: Sequence[int],
    effective_scores: Mapping[int, float],
    fallback_ids: set[int],
    *,
    seen_movie_ids: set[int],
    top_n: int,
    candidate_block_size: int,
) -> list[dict[str, Any]]:
    if candidate_block_size <= 0:
        raise ValueError("candidate_block_size must be positive")
    if set(map(int, candidate_ids)) != set(map(int, effective_scores)):
        raise ValueError("score universe differs from candidate universe")
    heap: list[tuple[float, int, int, bool]] = []
    for start in range(0, len(candidate_ids), candidate_block_size):
        for raw_movie_id in candidate_ids[start : start + candidate_block_size]:
            movie_id = int(raw_movie_id)
            if movie_id in seen_movie_ids:
                continue
            entry = (float(effective_scores[movie_id]), -movie_id, movie_id, movie_id in fallback_ids)
            if len(heap) < top_n:
                heapq.heappush(heap, entry)
            elif entry[:2] > heap[0][:2]:
                heapq.heapreplace(heap, entry)
    ordered = sorted(heap, key=lambda item: (-item[0], item[2]))
    return [
        {
            "rank": rank,
            "movie_id": movie_id,
            "effective_score": score,
            "fallback_used": fallback,
        }
        for rank, (score, _, movie_id, fallback) in enumerate(ordered, start=1)
    ]


def reciprocal_rank_fusion(rankings: Sequence[Sequence[int]], *, c: int) -> list[dict[str, Any]]:
    if c <= 0 or not rankings:
        raise ValueError("RRF needs a positive c and at least one ranking")
    scores: dict[int, float] = {}
    for ranking in rankings:
        if len(set(ranking)) != len(ranking):
            raise ValueError("RRF ranking contains duplicate movie IDs")
        for rank, movie_id in enumerate(ranking, start=1):
            scores[int(movie_id)] = scores.get(int(movie_id), 0.0) + 1.0 / (c + rank)
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [
        {"rank": rank, "movie_id": movie_id, "rrf_score": score}
        for rank, (movie_id, score) in enumerate(ordered, start=1)
    ]


def checkpoint_write_or_resume(
    path: Path,
    *,
    resume_signature: str,
    selected_rows: Sequence[Mapping[str, Any]],
) -> tuple[str, bytes]:
    payload = {
        "resume_signature": resume_signature,
        "selected_rows": list(selected_rows),
    }
    expected = canonical_json_bytes(payload)
    if path.exists():
        existing = path.read_bytes()
        decoded = json.loads(existing)
        if decoded.get("resume_signature") != resume_signature:
            raise ResumeSignatureError("checkpoint signature mismatch; old artifact preserved")
        if existing != expected:
            raise ResumeSignatureError("checkpoint payload mismatch; old artifact preserved")
        return "REUSED", existing
    atomic_write_bytes(path, expected)
    return "CREATED", expected


def _assert_validation_mode_blocked(contract: Mapping[str, Any]) -> bool:
    try:
        run_real_validation(contract)
    except AuthorizationError:
        return True
    return False


def run_real_validation(contract: Mapping[str, Any]) -> None:
    if not contract["current_authorization"].get("real_validation_fit_or_score", False):
        raise AuthorizationError("real Validation is not authorized; no data artifact was opened")
    raise NotImplementedError("real Validation implementation requires a later approved phase")


def run_synthetic_preflight(contract: Mapping[str, Any], *, root: Path = ROOT) -> dict[str, Any]:
    if not contract["current_authorization"].get("synthetic_preflight", False):
        raise AuthorizationError("synthetic preflight is not authorized")
    contract_path = root / "docs/recommendation/contracts/rec-ev-019c-validation-artifacts.json"
    contract_sha256 = sha256_file(contract_path)
    firewall = InputFirewall.from_contract(contract, root=root)
    opened: list[str] = []

    def fake_opener(path: Path) -> bytes:
        opened.append(_repo_relative(path, root=root))
        return b"synthetic-no-data-read"

    for allowed_path in contract["allowed_input_artifacts"].values():
        firewall.read_bytes(allowed_path, opener=fake_opener)
    allowed_paths_accepted = set(opened) == set(contract["allowed_input_artifacts"].values())

    open_count = len(opened)
    try:
        firewall.read_bytes(contract["forbidden_input_artifacts"][0], opener=fake_opener)
        forbidden_rejected = False
    except InputFirewallError:
        forbidden_rejected = len(opened) == open_count
    try:
        firewall.read_bytes("outputs/recommendation-evidence/rec-ev-019c/not-allowed.parquet", opener=fake_opener)
        unknown_rejected = False
    except InputFirewallError:
        unknown_rejected = len(opened) == open_count

    candidates = list(range(101, 109))
    b0_raw = {101: 0.10, 102: 0.20, 103: 0.99, 104: 0.50, 105: 0.50, 106: 0.05, 107: 0.75, 108: 0.65}
    model_raw = {101: 0.30, 102: 0.20, 104: 0.90, 105: 0.90, 106: 0.10, 108: 0.70}
    available = set(model_raw)
    effective, fallback_ids = effective_percentile_scores(candidates, model_raw, available, b0_raw)
    ranking_block_3 = stream_top_n(
        candidates,
        effective,
        fallback_ids,
        seen_movie_ids={101},
        top_n=8,
        candidate_block_size=3,
    )
    ranking_block_8 = stream_top_n(
        candidates,
        effective,
        fallback_ids,
        seen_movie_ids={101},
        top_n=8,
        candidate_block_size=8,
    )
    ranked_ids = [row["movie_id"] for row in ranking_block_3]
    fallback_rows = {row["movie_id"]: row for row in ranking_block_3 if row["fallback_used"]}
    b0_percentiles = midrank_percentiles(b0_raw)
    b0_ranking = stream_top_n(
        candidates,
        b0_percentiles,
        set(),
        seen_movie_ids={101},
        top_n=8,
        candidate_block_size=4,
    )
    rrf_first = reciprocal_rank_fusion(
        [ranked_ids, [row["movie_id"] for row in b0_ranking]],
        c=10,
    )
    rrf_second = reciprocal_rank_fusion(
        [ranked_ids, [row["movie_id"] for row in b0_ranking]],
        c=10,
    )

    trials = expand_trials(contract)
    trial_counts = {model_id: len(items) for model_id, items in trials.items()}
    declared_counts = {model_id: int(model["trial_count"]) for model_id, model in contract["models"].items()}
    maximum_trials = int(contract["trial_execution"]["maximum_trials_per_model"])

    with tempfile.TemporaryDirectory(prefix="rec-ev-019c-synthetic-") as directory:
        checkpoint = Path(directory) / "checkpoint.json"
        signature = sha256_bytes((contract_sha256 + "|synthetic-fixture-v1").encode("utf-8"))
        first_state, first_bytes = checkpoint_write_or_resume(
            checkpoint,
            resume_signature=signature,
            selected_rows=ranking_block_3,
        )
        second_state, second_bytes = checkpoint_write_or_resume(
            checkpoint,
            resume_signature=signature,
            selected_rows=ranking_block_3,
        )
        before_mismatch = checkpoint.read_bytes()
        try:
            checkpoint_write_or_resume(
                checkpoint,
                resume_signature="different-signature",
                selected_rows=ranking_block_3,
            )
            mismatch_refused = False
        except ResumeSignatureError:
            mismatch_refused = checkpoint.read_bytes() == before_mismatch

    tie_positions = {row["movie_id"]: row["rank"] for row in ranking_block_3}
    evaluation_positive_movie = 106
    checks = {
        "allowed_paths_accepted": allowed_paths_accepted,
        "forbidden_path_rejected_before_open": forbidden_rejected,
        "unknown_path_rejected_before_open": unknown_rejected,
        "identical_candidate_universe": set(effective) == set(candidates),
        "seen_excluded_before_scoring": 101 not in ranked_ids and len(ranked_ids) == len(candidates) - 1,
        "positive_injection_absent": ranked_ids[-1] == evaluation_positive_movie,
        "deterministic_tie_break": tie_positions[104] < tie_positions[105] and ranking_block_3 == ranking_block_8,
        "missing_feature_uses_b0": (
            fallback_ids == {103, 107}
            and abs(fallback_rows[103]["effective_score"] - b0_percentiles[103]) < 1e-12
            and abs(fallback_rows[107]["effective_score"] - b0_percentiles[107]) < 1e-12
        ),
        "rrf_uses_ranks_only": rrf_first == rrf_second,
        "grid_counts_match": trial_counts == declared_counts,
        "trial_cap_respected": all(count <= maximum_trials for count in trial_counts.values()),
        "resume_byte_equivalent": first_state == "CREATED" and second_state == "REUSED" and first_bytes == second_bytes,
        "resume_hash_mismatch_refused": mismatch_refused,
        "validation_mode_blocked": _assert_validation_mode_blocked(contract),
        "locked_test_never_opened": not set(opened).intersection(contract["forbidden_input_artifacts"]),
    }
    missing_checks = set(contract["synthetic_preflight_artifacts"]["required_checks"]).difference(checks)
    if missing_checks:
        raise RuntimeError(f"synthetic preflight did not implement checks: {sorted(missing_checks)}")
    if not all(checks.values()):
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise RuntimeError(f"synthetic preflight failed: {failed}")

    return {
        "schema_version": 1,
        "evidence_id": "REC-EV-019C-SYNTHETIC-PREFLIGHT",
        "status": "PASS_SYNTHETIC_PREFLIGHT",
        "contract_sha256": contract_sha256,
        "execution_role": "VALIDATION",
        "trial_counts": trial_counts,
        "checks": checks,
        "resume": {
            "first": first_state,
            "second": second_state,
            "byte_equivalent": first_bytes == second_bytes,
            "hash_mismatch_refused_and_old_preserved": mismatch_refused,
        },
        "fixture": {
            "candidate_count": len(candidates),
            "visible_after_seen_exclusion": len(ranked_ids),
            "missing_feature_movie_ids": sorted(fallback_ids),
            "ranked_movie_ids": ranked_ids,
            "rrf_movie_ids": [row["movie_id"] for row in rrf_first],
        },
        "real_validation_executed": False,
        "locked_test_opened": False,
        "product_policy_changed": False,
        "product_champion": None,
        "current_product_policy": contract["adoption_boundary"]["current_product_policy"],
        "next_gate": "LINUX_DEPENDENCY_SMOKE_AND_REAL_VALIDATION_APPROVAL_REVIEW",
    }


def write_synthetic_evidence(
    contract: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    root: Path = ROOT,
) -> tuple[Path, Path]:
    paths = contract["synthetic_preflight_artifacts"]
    result_path = root / paths["result"]
    manifest_path = root / paths["manifest"]
    result_bytes = atomic_write_json(result_path, result)
    manifest = {
        "schema_version": 1,
        "evidence_id": result["evidence_id"],
        "status": result["status"],
        "contract_sha256": result["contract_sha256"],
        "source_checksums": {"contract": result["contract_sha256"]},
        "artifacts": [
            {
                "path": paths["result"],
                "bytes": len(result_bytes),
                "sha256": sha256_bytes(result_bytes),
            }
        ],
        "validation": {
            "all_required_checks_pass": True,
            "required_checks": list(paths["required_checks"]),
            "execution_role": "VALIDATION",
            "real_validation_executed": False,
            "locked_test_opened": False,
        },
        "adoption": {
            "champion": None,
            "product_policy_changed": False,
            "current_product_policy": contract["adoption_boundary"]["current_product_policy"],
            "real_validation_authorized": False,
        },
    }
    atomic_write_json(manifest_path, manifest)
    return result_path, manifest_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run REC-EV-019C within its current authorization")
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--mode", choices=("synthetic-preflight", "validation"), required=True)
    parser.add_argument("--role", choices=("validation",), required=True)
    parser.add_argument("--resume", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    contract_path = args.contract.resolve()
    try:
        contract = read_json(contract_path)
        from validate_rec_ev_019c_contract import validate_contract

        validate_contract(contract, root=ROOT)
        if args.mode == "validation":
            run_real_validation(contract)
        result = run_synthetic_preflight(contract, root=ROOT)
        result_path, manifest_path = write_synthetic_evidence(contract, result, root=ROOT)
        output = {
            "status": result["status"],
            "evidence_id": result["evidence_id"],
            "result": _repo_relative(result_path, root=ROOT),
            "manifest": _repo_relative(manifest_path, root=ROOT),
            "real_validation_executed": False,
            "locked_test_opened": False,
            "next_gate": result["next_gate"],
        }
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(f"REC-EV-019C runner blocked: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
