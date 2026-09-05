#!/usr/bin/env python3
"""ID-only exposure registry and membership feasibility for REC-EV-026."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import zipfile

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

try:
    from rec_ev_022a_core import old_user_bucket, user_key, user_role
    from run_rec_ev_023ef_preflight import ResumeError, atomic_write_json, canonical_json_bytes, read_json, sha256_file
    from validate_rec_ev_026_design import validate
except ImportError:
    from scripts.rec_ev_022a_core import old_user_bucket, user_key, user_role
    from scripts.run_rec_ev_023ef_preflight import ResumeError, atomic_write_json, canonical_json_bytes, read_json, sha256_file
    from scripts.validate_rec_ev_026_design import validate


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-026-content-cf-alignment-design.json"
IMPLEMENTATIONS = [
    "docs/recommendation/contracts/rec-ev-026-content-cf-alignment-design.json",
    "scripts/validate_rec_ev_026_design.py",
    "scripts/tests/test_rec_ev_026_design.py",
    "scripts/rec_ev_022a_core.py",
    "scripts/run_rec_ev_026_preflight.py",
    "scripts/tests/test_rec_ev_026_preflight.py",
]
MEMBERSHIP_PATH = "cache/membership.parquet"
REGISTRY_INTEGRITY_PATH = "exposure-registry.integrity.json"
MEMBERSHIP_INTEGRITY_PATH = "cache/membership.integrity.json"
KEY019_PREFIX = "feelm-ml32m-user-v1|"
ROLE_LITERALS = {"profile": "PROFILE", "target": "TARGET", "control": "CONTROL"}


def resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def output_root(contract: Mapping[str, Any]) -> Path:
    return resolve(str(contract["output_root"]))


def output_path(contract: Mapping[str, Any], key: str) -> Path:
    return output_root(contract) / str(contract["outputs"][key])


def key019(raw_user: int) -> str:
    return hashlib.sha256(f"{KEY019_PREFIX}{int(raw_user)}".encode("utf-8")).hexdigest()


def current_user_allowed(raw_user: int) -> bool:
    return 40 <= old_user_bucket(raw_user) <= 59 and user_role(raw_user) in {"STAGE1_SELECTION", "STAGE2_DEVELOPMENT"}


def _all_pinned_specs(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    specs.extend(dict(value) for value in contract["allowed_input_artifacts"].values())
    specs.extend(dict(value) for value in contract["teacher"]["factor_artifacts"].values())
    specs.extend(dict(value) for value in contract["exposure_registry"]["sources"])
    for proof in contract["exposure_registry"]["reuse_proofs"].values():
        specs.extend(dict(value) for value in proof.get("files", [proof] if "path" in proof else []))
    for proof in contract["exposure_registry"]["no_outcome_proofs"].values():
        specs.extend(dict(value) for value in proof.get("files", [proof] if "path" in proof else []))
    unique: dict[str, dict[str, Any]] = {}
    for spec in specs:
        unique[str(spec["path"])] = {"path": str(spec["path"]), "bytes": int(spec["bytes"]), "sha256": str(spec["sha256"])}
    return [unique[key] for key in sorted(unique)]


def _artifact_rows(specs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in specs:
        path = resolve(str(spec["path"]))
        if not path.is_file() or path.stat().st_size != int(spec["bytes"]) or sha256_file(path) != str(spec["sha256"]):
            raise RuntimeError(f"source drift: {path}")
        rows.append({"path": str(spec["path"]), "bytes": int(spec["bytes"]), "sha256": str(spec["sha256"])})
    return rows


def _implementation_rows() -> list[dict[str, Any]]:
    rows = []
    for relative in IMPLEMENTATIONS:
        path = resolve(relative)
        if not path.is_file():
            raise RuntimeError(f"missing implementation: {path}")
        rows.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return rows


def expected_lock_state(contract: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    validate(dict(contract))
    sources = _artifact_rows(_all_pinned_specs(contract))
    implementations = _implementation_rows()
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-026-PREFLIGHT",
        "design_audit_thread_id": contract["design_audit"]["thread_id"],
        "design_audit_verdict": "REC_EV_026_DESIGN_PASS_EXACT_CONTRACT",
        "sources": sources,
        "implementation_artifacts": implementations,
        "rating_value_bytes_parsed": 0,
        "timestamp_bytes_parsed": 0,
        "locked_test_opened": False,
        "final_reserve_opened": False,
    }
    lock = {
        "schema_version": 1,
        "evidence_id": "REC-EV-026-PREFLIGHT",
        "status": "LOCKED_ID_ONLY_PREFLIGHT",
        "contract_sha256": hashlib.sha256(canonical_json_bytes(contract)).hexdigest(),
        "source_artifacts_sha256": hashlib.sha256(canonical_json_bytes(sources)).hexdigest(),
        "implementation_artifacts_sha256": hashlib.sha256(canonical_json_bytes(implementations)).hexdigest(),
        "source_manifest_sha256": hashlib.sha256(canonical_json_bytes(manifest)).hexdigest(),
        "rating_value_bytes_parsed": 0,
        "timestamp_bytes_parsed": 0,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }
    return manifest, lock


def create_or_verify_lock(contract: Mapping[str, Any], *, resume: bool) -> dict[str, Any]:
    lock_path = output_path(contract, "protocol_lock")
    manifest_path = output_path(contract, "source_manifest")
    present = [lock_path.exists(), manifest_path.exists()]
    if any(present) and not all(present):
        raise ResumeError("partial preflight lock")
    if resume and not any(present):
        raise ResumeError("--resume before preflight lock")
    if not any(present):
        root = output_root(contract)
        downstream = sorted(
            path for path in root.rglob("*")
            if path.is_file() and path not in {lock_path, manifest_path}
        ) if root.exists() else []
        if downstream:
            raise ResumeError("downstream preflight artifacts exist without lock")
    manifest, lock = expected_lock_state(contract)
    if all(present):
        if not resume:
            raise ResumeError("preflight lock exists; use --resume")
        if read_json(lock_path) != lock or read_json(manifest_path) != manifest:
            raise ResumeError("preflight lock drift")
        return lock
    atomic_write_json(manifest_path, manifest)
    atomic_write_json(lock_path, lock)
    return lock


def run_signature(contract: Mapping[str, Any]) -> str:
    lock = read_json(output_path(contract, "protocol_lock"))
    payload = {key: lock[key] for key in ("contract_sha256", "source_artifacts_sha256", "implementation_artifacts_sha256", "source_manifest_sha256")}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _list_values(value: Any) -> list[int]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    if isinstance(value, np.ndarray):
        return [int(item) for item in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [int(item) for item in value]
    return [int(value)]


def build_exposure_registry(contract: Mapping[str, Any]) -> pd.DataFrame:
    records: set[tuple[str, str, int]] = set()
    for spec in contract["exposure_registry"]["sources"]:
        path = resolve(str(spec["path"]))
        namespace = str(spec["namespace"])
        columns = list(spec["columns"])
        if namespace == "RAW_TO_BOTH":
            parquet = pq.ParquetFile(path)
            for row_group in range(parquet.num_row_groups):
                users = parquet.read_row_group(row_group, columns=["user_id"]).column("user_id").to_numpy(zero_copy_only=False)
                mask = np.fromiter((current_user_allowed(int(value)) for value in users), dtype=bool, count=len(users))
                if not bool(mask.any()):
                    continue
                movies = parquet.read_row_group(row_group, columns=["movie_id"]).column("movie_id").to_numpy(zero_copy_only=False)
                for raw_user, movie in zip(users[mask], movies[mask]):
                    records.add(("019", key019(int(raw_user)), int(movie)))
                    records.add(("022", user_key(int(raw_user)), int(movie)))
            continue
        frame = pd.read_parquet(path, columns=columns)
        user_column = columns[0]
        movie_columns = columns[1:]
        for row in frame.itertuples(index=False, name=None):
            anonymous = str(row[0])
            if len(anonymous) != 64 or anonymous.lower() != anonymous:
                raise RuntimeError(f"invalid registry user key in {spec['id']}")
            for value in row[1:]:
                for movie in _list_values(value):
                    records.add((namespace, anonymous, int(movie)))
    return pd.DataFrame(sorted(records), columns=["namespace", "user_key", "movie_id"])


def build_common_support(contract: Mapping[str, Any]) -> pd.DataFrame:
    structured = pd.read_parquet(resolve(str(contract["allowed_input_artifacts"]["structured_features"]["path"])))
    required = {"movie_id", "feature_eligible", "release_year", "genre_ids", "original_language", "director_ids", "top5_cast_ids", "keyword_ids"}
    if not required.issubset(structured.columns) or structured["movie_id"].duplicated().any():
        raise RuntimeError("structured schema or identity drift")

    def nonempty(value: Any) -> bool:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return False
        try:
            return len(value) > 0
        except TypeError:
            return False

    current_full_nonzero = (
        structured["genre_ids"].map(nonempty) | structured["original_language"].notna()
        | structured["release_year"].notna()
        | structured.get("runtime_minutes", pd.Series(False, index=structured.index)).notna()
        | structured["director_ids"].map(nonempty) | structured["top5_cast_ids"].map(nonempty)
        | structured["keyword_ids"].map(nonempty)
    )
    structured_ok = structured["feature_eligible"].fillna(False).astype(bool) & structured["release_year"].notna() & current_full_nonzero
    structured_frame = structured.loc[structured_ok, ["movie_id", "release_year"]].copy()
    dimension = int(contract["common_support"]["e5_dimension"])
    table = pq.read_table(resolve(str(contract["allowed_input_artifacts"]["text_embeddings"]["path"])), columns=["movie_id", "model_id", "model_revision", "embedding", "feature_eligible"])
    expected_types = {"movie_id": pa.int32(), "model_id": pa.string(), "model_revision": pa.string(), "embedding": pa.list_(pa.float32(), dimension), "feature_eligible": pa.bool_()}
    for name, expected_type in expected_types.items():
        if table.schema.field(name).type != expected_type or table.column(name).null_count:
            raise RuntimeError(f"E5 {name} schema/null drift")
    embeddings = table.column("embedding").combine_chunks()
    if embeddings.values.null_count:
        raise RuntimeError("E5 embedding element null")
    text_ids = table.column("movie_id").to_numpy(zero_copy_only=False).astype(np.int64)
    if len(text_ids) != len(set(text_ids.tolist())):
        raise RuntimeError("E5 duplicate movie_id")
    vectors = embeddings.values.to_numpy(zero_copy_only=False).astype(np.float32).reshape(len(text_ids), dimension)
    norms = np.linalg.norm(vectors.astype(np.float64), axis=1)
    text_ok = (
        table.column("feature_eligible").to_numpy(zero_copy_only=False).astype(bool)
        & np.isfinite(vectors).all(axis=1) & (np.abs(norms - 1.0) <= 0.0001)
        & np.asarray([value == contract["common_support"]["e5_model_id"] for value in table.column("model_id").to_pylist()], dtype=bool)
        & np.asarray([value == contract["common_support"]["e5_revision"] for value in table.column("model_revision").to_pylist()], dtype=bool)
    )
    supported = {int(movie) for movie in text_ids[text_ok].tolist()}
    frame = structured_frame.loc[structured_frame["movie_id"].isin(supported)].sort_values("movie_id", kind="stable", ignore_index=True)
    if len(frame) != int(contract["common_support"]["expected_items"]):
        raise RuntimeError("common support count drift")
    projection = read_json(resolve(str(contract["allowed_input_artifacts"]["korean_projection"]["path"])))
    if projection.get("artifact_id") != "KOREAN_ORIGIN_MOVIELENS_MOVIE_ID_PROJECTION_V1":
        raise RuntimeError("Korean projection drift")
    korean_ids = {int(value) for value in projection["movie_ids"]}
    factor_ids = set(pd.read_parquet(resolve(str(contract["allowed_input_artifacts"]["candidate_core"]["path"])), columns=["movie_id"])["movie_id"].astype(int))
    frame["is_korean"] = frame["movie_id"].isin(korean_ids)
    frame["has_teacher_factor"] = frame["movie_id"].isin(factor_ids)
    return frame


def movie_id_only_rows(archive: Path, member: str, maximum_user: int) -> Iterable[tuple[int, int]]:
    with zipfile.ZipFile(archive) as source:
        with source.open(member) as handle:
            header = handle.readline()
            if not header.startswith(b"userId,movieId,"):
                raise RuntimeError("MovieLens header drift")
            for line in handle:
                first = line.find(b",")
                if first <= 0:
                    raise RuntimeError("MovieLens user field drift")
                raw_user = int(line[:first])
                if raw_user < 0 or raw_user > maximum_user or not current_user_allowed(raw_user):
                    continue
                second = line.find(b",", first + 1)
                if second <= first + 1:
                    raise RuntimeError("MovieLens movie field drift")
                yield raw_user, int(line[first + 1:second])


def role_order(user_key022: str, movies: Sequence[int], salt: str, role: str) -> list[int]:
    return sorted((int(movie) for movie in movies), key=lambda movie: (hashlib.sha256(f"{salt}|{user_key022}|{role}|{movie}".encode("utf-8")).digest(), movie))


def select_membership(contract: Mapping[str, Any], support: pd.DataFrame, registry: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    max_movie = int(support["movie_id"].max())
    present = np.zeros(max_movie + 1, dtype=bool)
    korean = np.zeros(max_movie + 1, dtype=bool)
    factor = np.zeros(max_movie + 1, dtype=bool)
    years = np.full(max_movie + 1, -1, dtype=np.int16)
    ids = support["movie_id"].to_numpy(dtype=np.int64)
    present[ids] = True
    korean[ids] = support["is_korean"].to_numpy(dtype=bool)
    factor[ids] = support["has_teacher_factor"].to_numpy(dtype=bool)
    years[ids] = support["release_year"].to_numpy(dtype=np.int16)
    exposed019: dict[str, set[int]] = {}
    exposed022: dict[str, set[int]] = {}
    for namespace, anonymous, movie in registry.itertuples(index=False, name=None):
        (exposed019 if namespace == "019" else exposed022).setdefault(str(anonymous), set()).add(int(movie))
    pools: dict[str, dict[int, dict[str, list[int]]]] = {key: {} for key in contract["experiments"]}
    archive_spec = contract["allowed_input_artifacts"]["movielens_archive"]
    parsed = 0
    keys: dict[int, tuple[str, str]] = {}
    for raw_user, movie in movie_id_only_rows(resolve(str(archive_spec["path"])), str(archive_spec["member"]), int(contract["source_users"]["maximum_user_id"])):
        parsed += 1
        if movie < 0 or movie >= len(present) or not present[movie]:
            continue
        k19, k22 = keys.setdefault(raw_user, (key019(raw_user), user_key(raw_user)))
        if movie in exposed019.get(k19, set()) or movie in exposed022.get(k22, set()):
            continue
        if not korean[movie]:
            row = pools["REC-EV-026A"].setdefault(raw_user, {"profile": [], "source": [], "target": []})
            row["source"].append(movie)
            if factor[movie]:
                row["profile"].append(movie)
        else:
            pools["REC-EV-026A"].setdefault(raw_user, {"profile": [], "source": [], "target": []})["target"].append(movie)
        year = int(years[movie])
        if year < 2020:
            row = pools["REC-EV-026B"].setdefault(raw_user, {"profile": [], "source": [], "target": []})
            row["source"].append(movie)
            if factor[movie]:
                row["profile"].append(movie)
        elif year <= 2023:
            pools["REC-EV-026B"].setdefault(raw_user, {"profile": [], "source": [], "target": []})["target"].append(movie)
    rows: list[dict[str, Any]] = []
    summaries: dict[str, Any] = {}
    for evidence_id, spec in contract["experiments"].items():
        target_union: set[int] = set()
        control_union: set[int] = set()
        eligible_keys: list[str] = []
        for raw_user in sorted(pools[evidence_id]):
            item_pools = pools[evidence_id][raw_user]
            k22 = keys[raw_user][1]
            profile = role_order(k22, item_pools["profile"], str(spec["profile_salt"]), ROLE_LITERALS["profile"])[:14]
            if len(profile) < 14:
                continue
            controls = [movie for movie in item_pools["source"] if movie not in set(profile)]
            panels: list[tuple[int, list[int], list[int]]] = []
            for panel in range(4):
                targets = role_order(k22, item_pools["target"], str(spec["target_salts"][panel]), ROLE_LITERALS["target"])[:4]
                selected_controls = role_order(k22, controls, str(spec["control_salts"][panel]), ROLE_LITERALS["control"])[:4]
                if len(targets) < 4 or len(selected_controls) < 4 or len(set(targets)) != 4 or len(set(selected_controls)) != 4:
                    panels = []
                    break
                panels.append((panel, targets, selected_controls))
            if len(panels) != 4:
                continue
            eligible_keys.append(k22)
            rows.extend({"evidence_id": evidence_id, "user_key022": k22, "panel": -1, "role": "PROFILE", "position": position, "movie_id": int(movie)} for position, movie in enumerate(profile))
            for panel, targets, selected_controls in panels:
                target_union.update(targets)
                control_union.update(selected_controls)
                rows.extend({"evidence_id": evidence_id, "user_key022": k22, "panel": panel, "role": "TARGET", "position": position, "movie_id": int(movie)} for position, movie in enumerate(targets))
                rows.extend({"evidence_id": evidence_id, "user_key022": k22, "panel": panel, "role": "CONTROL", "position": position, "movie_id": int(movie)} for position, movie in enumerate(selected_controls))
        feasible = len(eligible_keys) >= int(spec["minimum_users"]) and len(target_union) >= int(spec["minimum_unique_targets"]) and len(control_union) >= int(spec["minimum_unique_controls"])
        summaries[evidence_id] = {
            "eligible_users": len(eligible_keys),
            "eligible_user_key_set_sha256": hashlib.sha256(canonical_json_bytes(sorted(eligible_keys))).hexdigest(),
            "unique_targets": len(target_union),
            "unique_controls": len(control_union),
            "floors": {"minimum_users": int(spec["minimum_users"]), "minimum_unique_targets": int(spec["minimum_unique_targets"]), "minimum_unique_controls": int(spec["minimum_unique_controls"])},
            "status": "FEASIBLE_PRELABEL" if feasible else "INFEASIBLE_PRELABEL",
        }
    membership = pd.DataFrame(rows, columns=["evidence_id", "user_key022", "panel", "role", "position", "movie_id"])
    if not membership.empty:
        membership = membership.sort_values(["evidence_id", "user_key022", "role", "panel", "position"], kind="stable", ignore_index=True)
    return membership, {"movie_id_rows_parsed": parsed, "experiments": summaries}


def _write_parquet_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def _integrity(path: Path) -> dict[str, Any]:
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def compute_payload(contract: Mapping[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    registry = build_exposure_registry(contract)
    support = build_common_support(contract)
    membership, selected = select_membership(contract, support, registry)
    result = {
        "schema_version": 1,
        "evidence_id": "REC-EV-026-PREFLIGHT",
        "status": "PREFLIGHT_COMPLETE",
        "run_signature": run_signature(contract),
        "common_support": {"items": len(support), "korean_origin_items": int(support["is_korean"].sum()), "recent_2020_2023_items": int(((support["release_year"] >= 2020) & (support["release_year"] <= 2023)).sum()), "teacher_factor_items": int(support["has_teacher_factor"].sum())},
        "exposure_registry": {"rows": len(registry), "namespaces": registry.groupby("namespace").size().to_dict()},
        "reader": {"movie_id_rows_parsed": selected["movie_id_rows_parsed"], "rating_value_bytes_parsed": 0, "timestamp_bytes_parsed": 0, "raw_user_ids_written": False},
        "experiments": selected["experiments"],
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }
    return registry, membership, result


def compute_and_write(contract: Mapping[str, Any]) -> dict[str, Any]:
    registry, membership, result = compute_payload(contract)
    root = output_root(contract)
    registry_path = output_path(contract, "exposure_registry")
    membership_path = root / MEMBERSHIP_PATH
    _write_parquet_atomic(registry_path, registry)
    _write_parquet_atomic(membership_path, membership)
    registry_integrity = {"schema_version": 1, "artifact": _integrity(registry_path), "rows": len(registry), "rating_value_bytes_parsed": 0, "timestamp_bytes_parsed": 0}
    membership_integrity = {"schema_version": 1, "artifact": _integrity(membership_path), "rows": len(membership), "raw_user_ids_written": False}
    atomic_write_json(root / REGISTRY_INTEGRITY_PATH, registry_integrity)
    atomic_write_json(root / MEMBERSHIP_INTEGRITY_PATH, membership_integrity)
    progress = {"schema_version": 1, "phase": "PREFLIGHT_COMPLETE", "run_signature": result["run_signature"], "experiments": result["experiments"]}
    atomic_write_json(output_path(contract, "preflight"), result)
    atomic_write_json(output_path(contract, "progress"), progress)
    integrity = {
        "schema_version": 1,
        "run_signature": result["run_signature"],
        "artifacts": {"exposure_registry": _integrity(registry_path), "exposure_registry_integrity": _integrity(root / REGISTRY_INTEGRITY_PATH), "membership": _integrity(membership_path), "membership_integrity": _integrity(root / MEMBERSHIP_INTEGRITY_PATH), "preflight": _integrity(output_path(contract, "preflight")), "progress": _integrity(output_path(contract, "progress"))},
        "rating_value_bytes_parsed": 0,
        "timestamp_bytes_parsed": 0,
    }
    atomic_write_json(output_path(contract, "preflight_integrity"), integrity)
    return result


def verify_existing_result(contract: Mapping[str, Any]) -> dict[str, Any]:
    root = output_root(contract)
    required = [output_path(contract, "preflight"), output_path(contract, "progress"), output_path(contract, "preflight_integrity"), output_path(contract, "exposure_registry"), root / REGISTRY_INTEGRITY_PATH, root / MEMBERSHIP_PATH, root / MEMBERSHIP_INTEGRITY_PATH]
    if not all(path.is_file() for path in required):
        raise ResumeError("partial preflight result")
    integrity = read_json(output_path(contract, "preflight_integrity"))
    expected_artifact_paths = {
        "exposure_registry": output_path(contract, "exposure_registry"),
        "exposure_registry_integrity": root / REGISTRY_INTEGRITY_PATH,
        "membership": root / MEMBERSHIP_PATH,
        "membership_integrity": root / MEMBERSHIP_INTEGRITY_PATH,
        "preflight": output_path(contract, "preflight"),
        "progress": output_path(contract, "progress"),
    }
    if set(integrity) != {"schema_version", "run_signature", "artifacts", "rating_value_bytes_parsed", "timestamp_bytes_parsed"}:
        raise ResumeError("preflight integrity schema drift")
    if integrity["schema_version"] != 1 or integrity["run_signature"] != run_signature(contract) or integrity["rating_value_bytes_parsed"] != 0 or integrity["timestamp_bytes_parsed"] != 0:
        raise ResumeError("preflight integrity metadata drift")
    if set(integrity["artifacts"]) != set(expected_artifact_paths):
        raise ResumeError("preflight integrity artifact inventory drift")
    for key, spec in integrity["artifacts"].items():
        if set(spec) != {"path", "bytes", "sha256"} or resolve(spec["path"]).resolve() != expected_artifact_paths[key].resolve():
            raise ResumeError("preflight integrity path binding drift")
        path = resolve(spec["path"])
        if not path.is_file() or path.stat().st_size != spec["bytes"] or sha256_file(path) != spec["sha256"]:
            raise ResumeError("preflight artifact drift")
    registry_integrity = read_json(root / REGISTRY_INTEGRITY_PATH)
    membership_integrity = read_json(root / MEMBERSHIP_INTEGRITY_PATH)
    if set(registry_integrity) != {"schema_version", "artifact", "rows", "rating_value_bytes_parsed", "timestamp_bytes_parsed"} or registry_integrity["schema_version"] != 1 or registry_integrity["rating_value_bytes_parsed"] != 0 or registry_integrity["timestamp_bytes_parsed"] != 0:
        raise ResumeError("registry integrity metadata drift")
    if set(membership_integrity) != {"schema_version", "artifact", "rows", "raw_user_ids_written"} or membership_integrity["schema_version"] != 1 or membership_integrity["raw_user_ids_written"] is not False:
        raise ResumeError("membership integrity metadata drift")
    for nested, path in ((registry_integrity, output_path(contract, "exposure_registry")), (membership_integrity, root / MEMBERSHIP_PATH)):
        if set(nested["artifact"]) != {"path", "bytes", "sha256"} or resolve(nested["artifact"]["path"]).resolve() != path.resolve() or nested["artifact"] != _integrity(path):
            raise ResumeError("nested integrity path/hash drift")
    result = read_json(output_path(contract, "preflight"))
    if set(result) != {"schema_version", "evidence_id", "status", "run_signature", "common_support", "exposure_registry", "reader", "experiments", "locked_test_opened", "final_reserve_opened", "product_policy_updated", "champion"}:
        raise ResumeError("preflight result schema drift")
    if result["schema_version"] != 1 or result["evidence_id"] != "REC-EV-026-PREFLIGHT" or result["status"] != "PREFLIGHT_COMPLETE" or result["run_signature"] != run_signature(contract):
        raise ResumeError("preflight result identity drift")
    if result["reader"].get("rating_value_bytes_parsed") != 0 or result["reader"].get("timestamp_bytes_parsed") != 0 or result["reader"].get("raw_user_ids_written") is not False or result["locked_test_opened"] is not False or result["final_reserve_opened"] is not False or result["product_policy_updated"] is not False or result["champion"] is not None:
        raise ResumeError("preflight result firewall drift")
    if set(result["experiments"]) != {"REC-EV-026A", "REC-EV-026B"}:
        raise ResumeError("preflight experiment inventory drift")
    progress = read_json(output_path(contract, "progress"))
    expected_progress = {"schema_version": 1, "phase": "PREFLIGHT_COMPLETE", "run_signature": run_signature(contract), "experiments": result["experiments"]}
    if progress != expected_progress:
        raise ResumeError("preflight signature drift")
    stored_registry = pd.read_parquet(output_path(contract, "exposure_registry"))
    stored_membership = pd.read_parquet(root / MEMBERSHIP_PATH)
    if list(stored_registry.columns) != ["namespace", "user_key", "movie_id"] or list(stored_membership.columns) != ["evidence_id", "user_key022", "panel", "role", "position", "movie_id"]:
        raise ResumeError("preflight parquet schema drift")
    if registry_integrity["rows"] != len(stored_registry) or membership_integrity["rows"] != len(stored_membership):
        raise ResumeError("preflight parquet row count drift")
    expected_registry, expected_membership, expected_result = compute_payload(contract)
    try:
        pd.testing.assert_frame_equal(stored_registry, expected_registry, check_dtype=True, check_like=False)
        pd.testing.assert_frame_equal(stored_membership, expected_membership, check_dtype=True, check_like=False)
    except AssertionError as error:
        raise ResumeError("preflight deterministic parquet drift") from error
    if result != expected_result:
        raise ResumeError("preflight deterministic result drift")
    return result


def preflight(contract: Mapping[str, Any], *, resume: bool) -> dict[str, Any]:
    if not resume:
        raise ResumeError("post-lock preflight requires --resume")
    create_or_verify_lock(contract, resume=True)
    root = output_root(contract)
    result_paths = [output_path(contract, "preflight"), output_path(contract, "progress"), output_path(contract, "preflight_integrity"), output_path(contract, "exposure_registry"), root / REGISTRY_INTEGRITY_PATH, root / MEMBERSHIP_PATH, root / MEMBERSHIP_INTEGRITY_PATH]
    if any(path.exists() for path in result_paths):
        if not all(path.exists() for path in result_paths):
            raise ResumeError("partial preflight result")
        result = verify_existing_result(contract)
        return {"status": "REUSED_EXACT_PREFLIGHT", "experiments": result["experiments"]}
    result = compute_and_write(contract)
    return {"status": "WROTE_PREFLIGHT", "experiments": result["experiments"]}


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    validate(contract)
    return contract


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    parser.add_argument("--phase", choices=("lock", "preflight", "run"), required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.contract.resolve() != DEFAULT.resolve():
        raise RuntimeError("only exact default design accepted")
    contract = load_contract(args.contract)
    if args.phase == "lock":
        if args.resume:
            raise ResumeError("lock creation does not accept --resume")
        create_or_verify_lock(contract, resume=False)
        print("REC_EV_026_PREFLIGHT_LOCKED")
        return 0
    result = preflight(contract, resume=args.resume)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
