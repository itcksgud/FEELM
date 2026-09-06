"""Materialize REC-EV-028A attribution membership without rating values."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any
import zipfile

import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_rec_ev_019b_features import _zip_member, sha256_file  # noqa: E402
from preflight_rec_ev_027_membership import fold_of, hash_order  # noqa: E402
from rec_ev_022a_core import build_structured_full, old_user_bucket, user_key, user_role_bucket  # noqa: E402
from validate_rec_ev_028a_contract import DEFAULT, ROOT, validate  # noqa: E402


MAX_USER_ID = 300_000
OUTERS = (
    ("R0", "RANDOM_ITEM_COLD", "0"),
    ("R1", "RANDOM_ITEM_COLD", "1"),
    ("R2", "RANDOM_ITEM_COLD", "2"),
    ("R3", "RANDOM_ITEM_COLD", "3"),
    ("R4", "RANDOM_ITEM_COLD", "4"),
    ("KR", "KOREAN_ORIGIN_COLD", "KR"),
    ("RECENT", "RELEASE_2020_2023_COLD", "2020_2023"),
)


def phase_bucket(user_id: int, salt: str = "rec-ev-028-user-phase-v1") -> int:
    payload = f"{salt}|{int(user_id)}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest(), "big", signed=False) % 10_000


def common_sets(parent: dict[str, Any]) -> tuple[set[int], set[int], set[int], set[int]]:
    outputs = parent["catalog_content_build"]["outputs"]
    structured = pd.read_parquet(ROOT / outputs["structured"])
    embeddings = pd.read_parquet(ROOT / outputs["embeddings"])
    domains = pd.read_parquet(
        ROOT / outputs["domain_projection"],
        columns=["movie_id", "tmdb_korean_origin_proxy"],
    )
    candidates = structured.loc[structured["feature_eligible"], "movie_id"].astype(int).to_numpy()
    matrix = build_structured_full(structured, candidates)
    norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
    structured_ids = set(map(int, candidates[norms > 0]))
    e5_ids: set[int] = set()
    for row in embeddings.itertuples(index=False):
        vector = np.asarray(row.embedding, dtype=np.float32)
        if (
            bool(row.feature_eligible)
            and row.model_id == parent["item_universe"]["e5_model_id"]
            and row.model_revision == parent["item_universe"]["e5_revision"]
            and vector.shape == (384,)
            and bool(np.isfinite(vector).all())
            and abs(float(np.linalg.norm(vector)) - 1.0) <= 0.0001
        ):
            e5_ids.add(int(row.movie_id))
    universe = structured_ids & e5_ids
    by_movie = structured.set_index("movie_id", verify_integrity=True).reindex(sorted(universe))
    domain = domains.set_index("movie_id", verify_integrity=True).reindex(sorted(universe))
    korean = set(map(int, domain.index[domain["tmdb_korean_origin_proxy"].fillna(False)]))
    recent = set(
        int(movie) for movie, year in by_movie["release_year"].items()
        if pd.notna(year) and 2020 <= int(year) <= 2023
    )
    pre2020 = set(
        int(movie) for movie, year in by_movie["release_year"].items()
        if pd.notna(year) and int(year) <= 2019
    )
    if len(universe) != 85_517:
        raise RuntimeError("common support drift")
    return universe, korean, recent, pre2020


def scan_attribution_membership(
    archive_path: Path,
    universe: set[int],
    *,
    max_user_id: int = MAX_USER_ID,
) -> tuple[dict[int, list[int]], dict[str, int]]:
    old_allowed = np.fromiter(
        (old_user_bucket(uid) <= 59 for uid in range(max_user_id + 1)),
        dtype=bool,
        count=max_user_id + 1,
    )
    parent_roles = np.fromiter(
        (user_role_bucket(uid) for uid in range(max_user_id + 1)),
        dtype=np.uint16,
        count=max_user_id + 1,
    )
    phases = np.fromiter(
        (phase_bucket(uid) for uid in range(max_user_id + 1)),
        dtype=np.uint16,
        count=max_user_id + 1,
    )
    histories: dict[int, list[int]] = {}
    future_users: set[int] = set()
    fit_users: set[int] = set()
    counters = {
        "raw_lines_seen": 0,
        "excluded_after_user_id_only": 0,
        "fit_rows_discarded_after_user_id_only": 0,
        "future_rows_discarded_after_user_id_only": 0,
        "attribution_user_movie_memberships": 0,
        "rating_values_parsed": 0,
        "timestamps_parsed": 0,
    }
    with zipfile.ZipFile(archive_path) as archive:
        member = _zip_member(archive, "ratings.csv")
        with archive.open(member) as handle:
            if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
                raise RuntimeError("MovieLens header drift")
            for raw in handle:
                counters["raw_lines_seen"] += 1
                first = raw.find(b",")
                if first <= 0:
                    raise RuntimeError("malformed ratings row before user firewall")
                uid = int(raw[:first])
                if uid > max_user_id:
                    raise RuntimeError("MovieLens user ID exceeds pinned maximum")
                if not bool(old_allowed[uid]) or int(parent_roles[uid]) > 5999:
                    counters["excluded_after_user_id_only"] += 1
                    continue
                phase = int(phases[uid])
                if phase <= 7999:
                    fit_users.add(uid)
                    counters["fit_rows_discarded_after_user_id_only"] += 1
                    continue
                if phase >= 9000:
                    future_users.add(uid)
                    counters["future_rows_discarded_after_user_id_only"] += 1
                    continue
                second = raw.find(b",", first + 1)
                if second <= first + 1:
                    raise RuntimeError("malformed movie field")
                movie = int(raw[first + 1 : second])
                if movie in universe:
                    histories.setdefault(uid, []).append(movie)
                    counters["attribution_user_movie_memberships"] += 1
    counters["unique_fit_users_user_id_only"] = len(fit_users)
    counters["unique_future_users_user_id_only"] = len(future_users)
    counters["unique_attribution_users_with_common_item"] = len(histories)
    return histories, counters


def outer_pools(
    movies: list[int],
    *,
    slug: str,
    track: str,
    fold: str,
    fold_salt: str,
    universe: set[int],
    korean: set[int],
    recent: set[int],
    pre2020: set[int],
) -> tuple[list[int], list[int]]:
    if track == "RANDOM_ITEM_COLD":
        cold = [movie for movie in movies if fold_of(movie, fold_salt) == int(fold)]
        warm = [movie for movie in movies if fold_of(movie, fold_salt) != int(fold)]
        return warm, cold
    if slug == "KR":
        return [movie for movie in movies if movie in universe and movie not in korean], [movie for movie in movies if movie in korean]
    if slug == "RECENT":
        return [movie for movie in movies if movie in pre2020], [movie for movie in movies if movie in recent]
    raise ValueError(slug)


def effective_items(values: list[list[int]]) -> float:
    counts = Counter(movie for row in values for movie in row)
    total = float(sum(counts.values()))
    return total * total / float(sum(value * value for value in counts.values()))


def build_membership(contract: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    parent_spec = contract["inputs"]["parent_contract"]
    parent = json.loads((ROOT / parent_spec["path"]).read_text(encoding="utf-8"))
    universe, korean, recent, pre2020 = common_sets(parent)
    archive = Path(parent["allowed_input_artifacts"]["movielens_archive"]["path"])
    histories, reader = scan_attribution_membership(archive, universe)
    fold_salt = parent["strict_item_firewall"]["fold_salt"]
    profile_salt = contract["membership"]["profile_order_salt"]
    target_salt = contract["membership"]["target_order_salt"]
    rows: list[dict[str, Any]] = []
    for uid in sorted(histories):
        movies = histories[uid]
        if len(movies) != len(set(movies)):
            raise RuntimeError("duplicate attribution membership")
        key = user_key(uid)
        for slug, track, fold in OUTERS:
            warm, cold = outer_pools(
                movies, slug=slug, track=track, fold=fold, fold_salt=fold_salt,
                universe=universe, korean=korean, recent=recent, pre2020=pre2020,
            )
            target_n = 20 if track == "RANDOM_ITEM_COLD" else 4
            if len(warm) >= 12 and len(cold) >= target_n:
                rows.append(
                    {
                        "outer": slug,
                        "track": track,
                        "fold_or_domain": fold,
                        "user_key": key,
                        "profile_movie_ids": hash_order(
                            warm, salt=profile_salt, track=track, fold_or_domain=fold, key=key
                        )[:12],
                        "target_movie_ids": hash_order(
                            cold, salt=target_salt, track=track, fold_or_domain=fold, key=key
                        )[:target_n],
                    }
                )
    frame = pd.DataFrame(rows).sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    if frame.empty or frame.duplicated(["outer", "user_key"]).any():
        raise RuntimeError("REC-EV-028A membership identity drift")
    donors: dict[tuple[str, str], str] = {}
    for outer, group in frame.groupby("outer", sort=False):
        keys = group["user_key"].tolist()
        if len(keys) < 2:
            raise RuntimeError("cyclic donor needs at least two users")
        for index, key in enumerate(keys):
            donors[(str(outer), str(key))] = str(keys[(index + 1) % len(keys)])
    frame["donor_user_key"] = [donors[(row.outer, row.user_key)] for row in frame.itertuples(index=False)]
    for row in frame.itertuples(index=False):
        if set(row.profile_movie_ids) & set(row.target_movie_ids):
            raise RuntimeError("profile target overlap")
        if row.donor_user_key == row.user_key:
            raise RuntimeError("donor derangement failure")

    coverage: dict[str, Any] = {}
    thresholds = contract["coverage_gates"]
    for slug, group in frame.groupby("outer", sort=False):
        targets = group["target_movie_ids"].tolist()
        unique = len({movie for row in targets for movie in row})
        effective = effective_items(targets)
        if slug.startswith("R"):
            minimum_users = int(thresholds["minimum_users"]["EACH_RANDOM_FOLD"])
            minimum_unique = int(thresholds["minimum_unique_target_items"]["EACH_RANDOM_FOLD"])
            minimum_effective = float(thresholds["minimum_effective_target_items"]["EACH_RANDOM_FOLD"])
        else:
            minimum_users = int(thresholds["minimum_users"][slug])
            minimum_unique = int(thresholds["minimum_unique_target_items"][slug])
            minimum_effective = float(thresholds["minimum_effective_target_items"][slug])
        broad = len(group) >= minimum_users and unique >= minimum_unique and effective >= minimum_effective
        coverage[str(slug)] = {
            "users": len(group),
            "target_slots": int(sum(map(len, targets))),
            "unique_target_items": unique,
            "effective_target_items": effective,
            "coverage_truth": "BROAD_ITEM_ELIGIBLE" if broad else "POPULAR_RATED_PROXY_ONLY",
        }
    random_broad = all(coverage.get(f"R{fold}", {}).get("coverage_truth") == "BROAD_ITEM_ELIGIBLE" for fold in range(5))
    coverage["RANDOM_POOLED"] = {
        "users": int(frame.loc[frame["track"].eq("RANDOM_ITEM_COLD"), "user_key"].nunique()),
        "unique_target_items": int(len({
            movie for values in frame.loc[frame["track"].eq("RANDOM_ITEM_COLD"), "target_movie_ids"] for movie in values
        })),
        "effective_target_items": effective_items(
            frame.loc[frame["track"].eq("RANDOM_ITEM_COLD"), "target_movie_ids"].tolist()
        ),
        "coverage_truth": "BROAD_ITEM_ELIGIBLE" if random_broad else "POPULAR_RATED_PROXY_ONLY",
        "gate": "CONJUNCTION_OF_R0_TO_R4",
    }
    summary = {
        "schema_version": 1,
        "evidence_id": "REC-EV-028A-MEMBERSHIP",
        "status": "MEMBERSHIP_ONLY_PREFLIGHT_COMPLETE",
        "common_support_items": len(universe),
        "korean_origin_proxy_items": len(korean),
        "release_2020_2023_items": len(recent),
        "coverage": coverage,
        "reader": reader,
        "rating_values_parsed": False,
        "timestamps_parsed": False,
        "future_membership_materialized": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    return frame, summary


def atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/recommendation-evidence/rec-ev-028a")
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    validate(contract, args.contract.resolve())
    frame, summary = build_membership(contract)
    output_root = args.output_root.resolve()
    membership_path = output_root / "cache/membership.parquet"
    atomic_parquet(membership_path, frame)
    summary["membership"] = {
        "path": membership_path.relative_to(ROOT).as_posix(),
        "bytes": membership_path.stat().st_size,
        "sha256": sha256_file(membership_path),
    }
    atomic_json(output_root / "membership-preflight.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
