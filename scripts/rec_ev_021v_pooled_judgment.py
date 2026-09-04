from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = REPO_ROOT / "docs/recommendation/contracts/rec-ev-021v-kr-recent-niche-pooled-judgment.json"
SYSTEMS = ("B0", "B7", "B8", "B9")
STRATA = (
    "RECENT_KR_LOW_POP",
    "OLDER_KR_LOW_POP",
    "RECENT_NON_KR_CONTROL",
    "POPULAR_CONTROL",
)
UNINTERESTED_REASONS = {
    "NONE",
    "ALREADY_SEEN",
    "GENRE_MISMATCH",
    "STORY_NOT_APPEALING",
    "CAST_OR_CREW",
    "TOO_UNKNOWN",
    "NOT_IN_MOOD",
    "OTHER_NON_IDENTIFYING",
}
FORBIDDEN_PATH_FRAGMENTS = (
    "locked-test",
    "locked_test",
    "/test.parquet",
    "\\test.parquet",
    "product-policy",
    "product_policy",
    "product-decisions",
    "model-registry",
    "serving-contract",
)
EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?82[- ]?)?0?1[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)")


class PreflightError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PreflightError(message)


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hex(*parts: Any, length: int = 16) -> str:
    material = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:length]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    safe_input_path(path)
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    safe_input_path(path)
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line.strip():
                row = json.loads(line)
                require(isinstance(row, dict), f"JSONL row must be an object: {path}:{line_number}")
                rows.append(row)
    return rows


def safe_input_path(path: Path) -> Path:
    resolved = path.resolve()
    normalized = str(resolved).lower()
    require(not any(fragment in normalized for fragment in FORBIDDEN_PATH_FRAGMENTS), f"protected path is forbidden: {resolved}")
    return resolved


def load_contract(path: Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    require(path.resolve() == DEFAULT_CONTRACT.resolve(), "unexpected REC-EV-021V contract path")
    contract = read_json(path)
    validate_contract(contract)
    return contract


def validate_contract(contract: dict[str, Any]) -> None:
    require(contract.get("contract_id") == "REC-EV-021V-KR-RECENT-NICHE-POOLED-JUDGMENT-V1", "contract identity drift")
    require(contract.get("status") == "APPROVED_FOR_RECRUITMENT_PREFLIGHT_ONLY", "contract status drift")
    require(contract.get("task_id") == "TASK-REC-EV-021V", "task identity drift")
    boundary = contract["evidence_boundary"]
    require(boundary["separate_from_movielens_behavioral_validation"] is True, "MovieLens separation missing")
    require(boundary["current_target_evidence_status"] == "NO_ACTUAL_TARGET_DOMAIN_EVIDENCE", "target evidence boundary drift")
    authorization = contract["current_authorization"]
    for key in (
        "public_data_download",
        "human_recruitment",
        "consent_collection",
        "incentive_or_payment",
        "pii_collection_or_storage",
        "locked_test_access",
        "champion_selection",
        "product_policy_change",
    ):
        require(authorization[key] is False, f"forbidden authorization enabled: {key}")
    require(contract["invariants"] == {
        "execution_role": "TARGET_DOMAIN_POOLED_JUDGMENT_PREFLIGHT",
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
        "pii_stored": False,
    }, "invariant drift")
    cohort = contract["cohort"]
    require(cohort["residency"] == "KR" and cohort["target_valid_users"] == 100, "cohort target drift")
    onboarding = contract["onboarding"]
    require((onboarding["items_per_user"], onboarding["minimum_mapped_positive"], onboarding["minimum_mapped_negative"]) == (10, 2, 2), "K10 mapping gate drift")
    catalog = contract["catalog"]
    require((catalog["recent_release_date_min"], catalog["recent_release_date_max"]) == ("2024-01-01", "2026-09-05"), "recent window drift")
    require(catalog["target_origin_country_code"] == "KR", "target origin drift")
    require(tuple(catalog["strata"]) == STRATA, "catalog strata drift")
    systems = contract["frozen_systems"]
    require(tuple(systems) == SYSTEMS, "frozen system inventory drift")
    require(systems["B0"]["trial_id"] == "B0_MOVIELENS_BAYESIAN_RATING-T003", "B0 freeze drift")
    require(systems["B7"]["embedding_revision"] == "614241f622f53c4eeff9890bdc4f31cfecc418b3", "E5 revision drift")
    require((systems["B8"]["trial_id"], systems["B8"]["seed"], systems["B8"]["fit_or_refit_allowed"]) == ("B8_LIGHTFM-T003", 17, False), "B8 freeze drift")
    require(systems["B9"]["trial_id"] == "B9_RRF-T003" and systems["B9"]["raw_score_fusion_allowed"] is False, "B9 freeze drift")
    pool = contract["blind_pool"]
    require(pool["max_items_per_user"] == 48 and tuple(pool["systems"]) == SYSTEMS, "pool size/system drift")
    require(pool["items_per_stratum"] == {stratum: 12 for stratum in STRATA}, "pool strata quota drift")
    judgments = contract["judgments"]
    require(judgments["viewing_intent_scale"] == [0, 4] and judgments["expected_satisfaction_scale"] == [0, 4], "judgment scale drift")
    require(judgments["actual_watch_14_day"]["role"] == "SECONDARY_ONLY" and judgments["actual_watch_14_day"]["excluded_from_primary_and_success_gate"] is True, "14-day role drift")
    primary = contract["primary_endpoint"]
    require(primary["metric"] == "PAIRED_LINEAR_NDCG_AT_10" and primary["actual_watch_14_day_used"] is False, "primary endpoint drift")
    require(primary["positive_threshold"] == 0.75, "positive threshold drift")
    gates = contract["completion_gates"]
    require((gates["valid_users_min"], gates["accepted_unique_judgments_min"], gates["unseen_recent_kr_low_pop_positives_min"], gates["mapping_and_dedup_rate_min"]) == (100, 4000, 300, 0.95), "completion gate drift")
    success = contract["success_rule"]["per_candidate"]
    require(success["paired_ndcg_two_sided_95_lower_strictly_greater_than"] == 0.0, "paired CI gate drift")
    require(success["relative_ndcg_improvement_min"] == 0.05, "relative effect gate drift")
    require(success["top2_harm_delta_one_sided_95_upper_max"] == 0.02, "Top-2 harm gate drift")
    require(contract["budget"]["recruitment_authorized"] is False and contract["budget"]["payment_authorized"] is False, "budget authorization drift")


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise PreflightError(f"invalid ISO date: {value}") from error


def validate_source_manifest(manifest: dict[str, Any], contract: dict[str, Any], *, fixture_mode: bool) -> Path:
    required = {
        "schema_version", "source_id", "snapshot_version", "retrieved_at_utc", "catalog_as_of_date",
        "license", "local_artifact", "popularity_rule", "synthetic_fixture",
    }
    require(set(manifest) == required, "catalog source manifest fields differ")
    require(manifest["schema_version"] == 1, "catalog source manifest version drift")
    datetime.fromisoformat(str(manifest["retrieved_at_utc"]).replace("Z", "+00:00"))
    require(_parse_date(manifest["catalog_as_of_date"]) <= _parse_date(contract["catalog"]["catalog_as_of_date"]), "catalog snapshot is after frozen as-of date")
    require(bool(manifest["synthetic_fixture"]) is fixture_mode, "fixture/source mode mismatch")
    license_info = manifest["license"]
    for field in ("license_id", "license_url", "research_use_status", "redistribution_status", "attribution"):
        require(bool(license_info.get(field)), f"license field missing: {field}")
    if not fixture_mode:
        require(license_info["research_use_status"] == "APPROVED", "external source research use is not approved")
    popularity = manifest["popularity_rule"]
    require(popularity["frozen_before_judgments"] is True, "popularity rule must be frozen")
    require(float(popularity["low_pop_max_inclusive"]) < float(popularity["popular_min_inclusive"]), "popularity thresholds overlap")
    artifact = manifest["local_artifact"]
    require(artifact.get("path") is not None, "local catalog source is absent; downloader is intentionally unavailable")
    artifact_path = safe_input_path(Path(str(artifact["path"])))
    require(artifact_path.is_file(), f"local catalog source is absent: {artifact_path}")
    require(artifact.get("bytes") == artifact_path.stat().st_size, "catalog source byte size drift")
    require(artifact.get("sha256") == sha256_file(artifact_path), "catalog source checksum drift")
    return artifact_path


def expected_frozen_system_provenance(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    systems = contract["frozen_systems"]
    return {
        "B0": {
            "model_id": systems["B0"]["model_id"],
            "trial_id": systems["B0"]["trial_id"],
            "model_revision": None,
            "seed": None,
        },
        "B7": {
            "model_id": systems["B7"]["model_id"],
            "trial_id": systems["B7"]["trial_id"],
            "model_revision": systems["B7"]["embedding_revision"],
            "seed": None,
        },
        "B8": {
            "model_id": systems["B8"]["model_id"],
            "trial_id": systems["B8"]["trial_id"],
            "model_revision": None,
            "seed": systems["B8"]["seed"],
        },
        "B9": {
            "model_id": systems["B9"]["model_id"],
            "trial_id": systems["B9"]["trial_id"],
            "model_revision": None,
            "seed": None,
        },
    }


def validate_frozen_ranking_manifest(
    manifest: dict[str, Any],
    source_manifest: dict[str, Any],
    contract: dict[str, Any],
    *,
    fixture_mode: bool,
) -> Path:
    required = {
        "schema_version", "created_at_utc", "catalog_source_sha256", "rankings_artifact", "systems",
        "selected_before_judgments", "fit_or_refit_performed", "synthetic_fixture",
    }
    require(set(manifest) == required, "frozen ranking manifest fields differ")
    require(manifest["schema_version"] == 1, "frozen ranking manifest version drift")
    datetime.fromisoformat(str(manifest["created_at_utc"]).replace("Z", "+00:00"))
    require(bool(manifest["synthetic_fixture"]) is fixture_mode, "ranking fixture/source mode mismatch")
    require(manifest["selected_before_judgments"] is True, "rankings were not frozen before judgments")
    require(manifest["fit_or_refit_performed"] is False, "ranking manifest records fit/refit")
    require(manifest["catalog_source_sha256"] == source_manifest["local_artifact"]["sha256"], "ranking/catalog snapshot checksum mismatch")
    require(manifest["systems"] == expected_frozen_system_provenance(contract), "frozen system provenance drift")
    artifact = manifest["rankings_artifact"]
    ranking_path = safe_input_path(Path(str(artifact["path"])))
    require(ranking_path.is_file(), f"frozen ranking artifact is absent: {ranking_path}")
    require(artifact["bytes"] == ranking_path.stat().st_size, "frozen ranking artifact byte size drift")
    require(artifact["sha256"] == sha256_file(ranking_path), "frozen ranking artifact checksum drift")
    return ranking_path


def load_catalog_csv(path: Path) -> list[dict[str, Any]]:
    safe_input_path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _origin_codes(value: Any) -> list[str]:
    if isinstance(value, list):
        codes = value
    else:
        codes = re.split(r"[|,; ]+", str(value))
    return sorted({str(code).strip().upper() for code in codes if str(code).strip()})


def classify_catalog_row(row: dict[str, Any], manifest: dict[str, Any], contract: dict[str, Any]) -> str | None:
    if row.get("mapping_status") not in contract["catalog"]["mapping_status_allowlist"]:
        return None
    release = _parse_date(str(row["release_date"]))
    as_of = _parse_date(manifest["catalog_as_of_date"])
    if release > as_of:
        return None
    recent_min = _parse_date(contract["catalog"]["recent_release_date_min"])
    recent_max = _parse_date(contract["catalog"]["recent_release_date_max"])
    origins = _origin_codes(row["origin_country_codes"])
    popularity = float(row[manifest["popularity_rule"]["field"]])
    low = popularity <= float(manifest["popularity_rule"]["low_pop_max_inclusive"])
    popular = popularity >= float(manifest["popularity_rule"]["popular_min_inclusive"])
    recent = recent_min <= release <= recent_max
    if popular:
        return "POPULAR_CONTROL"
    if not low:
        return None
    if "KR" in origins and recent:
        return "RECENT_KR_LOW_POP"
    if "KR" in origins and release < recent_min:
        return "OLDER_KR_LOW_POP"
    if "KR" not in origins and recent:
        return "RECENT_NON_KR_CONTROL"
    return None


def build_catalog(rows: Sequence[dict[str, Any]], manifest: dict[str, Any], contract: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    mapped_rows = 0
    duplicate_rows = 0
    rejected_rows = 0
    for source_row in rows:
        if source_row.get("mapping_status") in contract["catalog"]["mapping_status_allowlist"]:
            mapped_rows += 1
        stratum = classify_catalog_row(source_row, manifest, contract)
        if stratum is None:
            rejected_rows += 1
            continue
        movie_key = str(source_row.get("movie_key", "")).strip()
        require(movie_key, "catalog movie_key is empty")
        normalized = {
            "movie_key": movie_key,
            "display_title": str(source_row["display_title"]).strip(),
            "release_date": str(source_row["release_date"]),
            "origin_country_codes": _origin_codes(source_row["origin_country_codes"]),
            "popularity_value": float(source_row[manifest["popularity_rule"]["field"]]),
            "mapping_status": str(source_row["mapping_status"]),
            "stratum": stratum,
            "source_id": manifest["source_id"],
        }
        if movie_key in by_key:
            duplicate_rows += 1
            require(by_key[movie_key] == normalized, f"conflicting catalog duplicate: {movie_key}")
            continue
        by_key[movie_key] = normalized
    catalog = sorted(by_key.values(), key=lambda row: (row["stratum"], row["movie_key"]))
    denominator = max(1, len(rows))
    mapping_dedup_rate = (mapped_rows - duplicate_rows) / denominator
    counts = Counter(row["stratum"] for row in catalog)
    audit = {
        "source_rows": len(rows),
        "mapped_rows": mapped_rows,
        "duplicate_rows": duplicate_rows,
        "rejected_rows": rejected_rows,
        "unique_eligible_items": len(catalog),
        "mapping_and_dedup_rate": mapping_dedup_rate,
        "stratum_counts": {stratum: counts[stratum] for stratum in STRATA},
    }
    require(mapping_dedup_rate >= float(contract["catalog"]["minimum_mapping_and_dedup_rate"]), "catalog mapping/dedup gate failed")
    for stratum in STRATA:
        require(counts[stratum] >= int(contract["blind_pool"]["items_per_stratum"][stratum]), f"catalog stratum is undersized: {stratum}")
    return catalog, audit


def participant_id(index: int) -> str:
    return f"p_{index:016x}"


def synthetic_fixture(contract: dict[str, Any], participant_count: int = 4) -> dict[str, Any]:
    catalog_rows: list[dict[str, Any]] = []
    specs = {
        "RECENT_KR_LOW_POP": ("KR", "2025-03-01", 10.0),
        "OLDER_KR_LOW_POP": ("KR", "2018-03-01", 12.0),
        "RECENT_NON_KR_CONTROL": ("US", "2025-03-01", 14.0),
        "POPULAR_CONTROL": ("US", "2023-03-01", 90.0),
    }
    prefixes = {
        "RECENT_KR_LOW_POP": "rkr",
        "OLDER_KR_LOW_POP": "okr",
        "RECENT_NON_KR_CONTROL": "rnk",
        "POPULAR_CONTROL": "pop",
    }
    for stratum, (origin, release_date, popularity) in specs.items():
        for index in range(1, 21):
            catalog_rows.append({
                "movie_key": f"{prefixes[stratum]}_{index:03d}",
                "display_title": f"Synthetic {stratum} {index:03d}",
                "release_date": release_date,
                "origin_country_codes": origin,
                "popularity_value": popularity + (index / 100.0),
                "mapping_status": "VERIFIED_CANONICAL_ID",
            })
    participants = []
    onboarding = []
    for index in range(1, participant_count + 1):
        key = participant_id(index)
        participants.append({
            "participant_id": key,
            "residency": "KR",
            "consent_version": "synthetic-v1",
            "consented_at_utc": "2026-09-05T00:00:00+00:00",
            "consent_active": True,
            "collection_wave": "SYNTHETIC_DRY_RUN",
        })
        onboarding.append({
            "participant_id": key,
            "items": [
                {
                    "movie_key": f"seed_{item:03d}",
                    "mapped_label": "POSITIVE" if item % 2 else "NEGATIVE",
                    "mapping_status": "VERIFIED_CANONICAL_ID",
                }
                for item in range(1, 11)
            ],
        })
    catalog_by_key = {row["movie_key"]: row for row in catalog_rows}
    rankings: list[dict[str, Any]] = []
    for participant in participants:
        p_key = participant["participant_id"]
        p_index = int(p_key.split("_")[1], 16)
        for system_index, system in enumerate(SYSTEMS):
            scored: list[tuple[float, str]] = []
            for movie_key, row in catalog_by_key.items():
                item_index = int(movie_key.rsplit("_", 1)[1])
                compatibility_signal = 4 - ((item_index + p_index) % 5)
                noise = int(stable_hex("fixture-rank", p_key, system, movie_key, length=8), 16) / 0xFFFFFFFF
                if system == "B0":
                    score = float(row["popularity_value"]) + noise
                else:
                    strength = {"B7": 1.0, "B8": 1.2, "B9": 1.1}[system]
                    score = strength * compatibility_signal + noise + system_index / 100.0
                scored.append((score, movie_key))
            scored.sort(key=lambda item: (-item[0], item[1]))
            for rank, (score, movie_key) in enumerate(scored, start=1):
                rankings.append({
                    "participant_id": p_key,
                    "model_id": system,
                    "movie_key": movie_key,
                    "rank": rank,
                    "effective_score": score,
                })
    return {"catalog_rows": catalog_rows, "participants": participants, "onboarding": onboarding, "rankings": rankings}


def budget_guard(contract: dict[str, Any], *, fixture_mode: bool, participant_count: int, approved_budget_krw: int | None = None, incentive_per_user_krw: int | None = None) -> dict[str, Any]:
    require(participant_count <= int(contract["cohort"]["hard_recruited_participant_cap"]), "participant hard cap exceeded")
    if fixture_mode:
        require(approved_budget_krw in (None, 0) and incentive_per_user_krw in (None, 0), "fixture must have zero cost")
        return {"mode": "SYNTHETIC_FIXTURE", "planned_cost_krw": 0, "approved_cap_krw": 0, "status": "PASS_ZERO_COST"}
    require(approved_budget_krw is not None and incentive_per_user_krw is not None, "external mode requires explicit approved budget and incentive")
    require(approved_budget_krw >= 0 and incentive_per_user_krw >= 0, "budget values must be nonnegative")
    planned = participant_count * incentive_per_user_krw
    require(planned <= approved_budget_krw, "planned recruitment cost exceeds approved budget")
    return {"mode": "EXTERNAL_PREFLIGHT", "planned_cost_krw": planned, "approved_cap_krw": approved_budget_krw, "status": "PASS_WITHIN_APPROVED_CAP"}


def _validate_onboarding(row: dict[str, Any], contract: dict[str, Any]) -> None:
    require(set(row) == {"participant_id", "items"}, "onboarding fields differ")
    require(re.fullmatch(contract["firewalls"]["participant_id_pattern"], str(row["participant_id"])) is not None, "participant ID format invalid")
    items = row["items"]
    require(isinstance(items, list) and len(items) == 10, "K10 must contain exactly 10 inputs")
    movie_keys = []
    labels = Counter()
    for item in items:
        require(set(item) == {"movie_key", "mapped_label", "mapping_status"}, "onboarding item fields differ")
        require(item["mapping_status"] == "VERIFIED_CANONICAL_ID", "K10 contains an unmapped input")
        require(item["mapped_label"] in {"POSITIVE", "NEGATIVE"}, "K10 label invalid")
        movie_keys.append(item["movie_key"])
        labels[item["mapped_label"]] += 1
    require(len(set(movie_keys)) == len(movie_keys), "K10 contains duplicate movie inputs")
    require(labels["POSITIVE"] >= 2 and labels["NEGATIVE"] >= 2, "K10 lacks minimum mapped positive/negative anchors")


def _ranking_lookup(rankings: Sequence[dict[str, Any]], catalog_by_key: dict[str, dict[str, Any]], participant_keys: set[str], contract: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    unique: set[tuple[str, str, str]] = set()
    counts: Counter[str] = Counter()
    for row in rankings:
        participant = str(row["participant_id"])
        model = str(row["model_id"])
        movie_key = str(row["movie_key"])
        require(participant in participant_keys, "ranking references unknown participant")
        require(model in SYSTEMS, "ranking references unknown model")
        require(movie_key in catalog_by_key, "ranking references unknown catalog item")
        key = (participant, model, movie_key)
        require(key not in unique, "duplicate participant/model/movie ranking")
        unique.add(key)
        counts[participant] += 1
        grouped[(participant, model)].append({**row, "rank": int(row["rank"])})
    for participant in participant_keys:
        require(counts[participant] <= int(contract["resources"]["max_rank_rows_per_participant"]), "ranking row budget exceeded")
        for model in SYSTEMS:
            rows = sorted(grouped[(participant, model)], key=lambda row: (row["rank"], row["movie_key"]))
            require(rows, f"missing ranking: {participant}/{model}")
            require(len({row["rank"] for row in rows}) == len(rows), f"duplicate rank: {participant}/{model}")
            grouped[(participant, model)] = rows
    return grouped


def build_blind_pool(
    catalog: Sequence[dict[str, Any]],
    onboarding: Sequence[dict[str, Any]],
    rankings: Sequence[dict[str, Any]],
    contract: dict[str, Any],
    *,
    checkpoint_root: Path | None = None,
    resume: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    catalog_by_key = {row["movie_key"]: row for row in catalog}
    require(len(catalog_by_key) == len(catalog), "normalized catalog contains duplicates")
    onboarding_by_participant = {row["participant_id"]: row for row in onboarding}
    require(len(onboarding_by_participant) == len(onboarding), "duplicate participant onboarding")
    for row in onboarding:
        _validate_onboarding(row, contract)
    participant_keys = set(onboarding_by_participant)
    ranking_groups = _ranking_lookup(rankings, catalog_by_key, participant_keys, contract)
    if checkpoint_root is not None:
        checkpoint_root.mkdir(parents=True, exist_ok=True)
    all_pool: list[dict[str, Any]] = []
    all_sealed: list[dict[str, Any]] = []
    resumed = 0
    for participant in sorted(participant_keys):
        checkpoint = checkpoint_root / f"{participant}.json" if checkpoint_root is not None else None
        if resume and checkpoint is not None and checkpoint.is_file():
            payload = read_json(checkpoint)
            require(payload.get("participant_id") == participant, "pool checkpoint participant mismatch")
            pool_rows = payload["pool_rows"]
            sealed_rows = payload["sealed_rows"]
            resumed += 1
        else:
            excluded = {item["movie_key"] for item in onboarding_by_participant[participant]["items"]}
            selected: list[dict[str, Any]] = []
            sealed_rows = []
            for stratum in STRATA:
                source_offset = int(stable_hex("source-offset", participant, stratum, contract["blind_pool"]["randomization_seed"], length=8), 16) % len(SYSTEMS)
                source_order = SYSTEMS[source_offset:] + SYSTEMS[:source_offset]
                used: set[str] = set()
                cursors = {model: 0 for model in SYSTEMS}
                model_rows = {
                    model: [row for row in ranking_groups[(participant, model)] if catalog_by_key[row["movie_key"]]["stratum"] == stratum and row["movie_key"] not in excluded]
                    for model in SYSTEMS
                }
                rank_maps = {model: {row["movie_key"]: row["rank"] for row in ranking_groups[(participant, model)]} for model in SYSTEMS}
                quota = int(contract["blind_pool"]["items_per_stratum"][stratum])
                for slot in range(quota):
                    source = source_order[slot % len(source_order)]
                    candidates = model_rows[source]
                    while cursors[source] < len(candidates) and candidates[cursors[source]]["movie_key"] in used:
                        cursors[source] += 1
                    require(cursors[source] < len(candidates), f"insufficient unique pool candidates: {participant}/{stratum}/{source}")
                    source_row = candidates[cursors[source]]
                    cursors[source] += 1
                    movie_key = source_row["movie_key"]
                    used.add(movie_key)
                    blind_token = "i_" + stable_hex("blind-item", participant, movie_key, contract["blind_pool"]["randomization_seed"], length=20)
                    catalog_row = catalog_by_key[movie_key]
                    selected.append({
                        "participant_id": participant,
                        "blind_item_token": blind_token,
                        "movie_key": movie_key,
                        "display_title": catalog_row["display_title"],
                        "release_date": catalog_row["release_date"],
                        "stratum": stratum,
                    })
                    sealed_rows.append({
                        "participant_id": participant,
                        "blind_item_token": blind_token,
                        "movie_key": movie_key,
                        "stratum": stratum,
                        "selection_source_model": source,
                        "selection_source_rank": int(source_row["rank"]),
                        "model_ranks": {model: int(rank_maps[model][movie_key]) for model in SYSTEMS},
                    })
            selected.sort(key=lambda row: stable_hex("presentation", participant, row["movie_key"], contract["blind_pool"]["randomization_seed"], length=32))
            pool_rows = [{**row, "presentation_order": order} for order, row in enumerate(selected, start=1)]
            require(len(pool_rows) == 48, "pool must contain exactly 48 items")
            source_counts = Counter(row["selection_source_model"] for row in sealed_rows)
            require(source_counts == Counter({model: 12 for model in SYSTEMS}), "pool source balance drift")
            if checkpoint is not None:
                write_json(checkpoint, {"participant_id": participant, "pool_rows": pool_rows, "sealed_rows": sealed_rows})
        all_pool.extend(pool_rows)
        all_sealed.extend(sealed_rows)
    participant_visible_forbidden = {"selection_source_model", "selection_source_rank", "model_ranks", "model_id", "rank", "effective_score"}
    require(not any(participant_visible_forbidden.intersection(row) for row in all_pool), "participant-visible pool leaks model provenance")
    audit = {
        "participants": len(participant_keys),
        "pool_rows": len(all_pool),
        "sealed_rows": len(all_sealed),
        "resumed_participants": resumed,
        "max_items_per_user": max(Counter(row["participant_id"] for row in all_pool).values(), default=0),
        "stratum_counts": dict(sorted(Counter(row["stratum"] for row in all_pool).items())),
        "selection_source_counts": dict(sorted(Counter(row["selection_source_model"] for row in all_sealed).items())),
        "participant_visible_model_fields": False,
    }
    return sorted(all_pool, key=lambda row: (row["participant_id"], row["presentation_order"])), sorted(all_sealed, key=lambda row: (row["participant_id"], row["stratum"], row["movie_key"])), audit


def synthetic_judgments(pool: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in pool:
        participant_index = int(str(row["participant_id"]).split("_")[1], 16)
        item_index = int(str(row["movie_key"]).rsplit("_", 1)[1])
        value = int((item_index + participant_index) % 5)
        seen = "SEEN" if (item_index + participant_index) % 9 == 0 else "UNSEEN"
        intent = value
        satisfaction = min(4, value + (1 if item_index % 3 == 0 else 0))
        reason = "TOO_UNKNOWN" if intent <= 1 else "NONE"
        rows.append({
            "participant_id": row["participant_id"],
            "blind_item_token": row["blind_item_token"],
            "seen_status": seen,
            "viewing_intent": intent,
            "expected_satisfaction": satisfaction,
            "uninterested_reason": reason,
            "actual_watch_14d": None,
            "submitted_at_utc": "2026-09-05T00:00:00+00:00",
        })
    return rows


def _pii_findings(value: Any, forbidden_fields: set[str], path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in forbidden_fields:
                findings.append(f"{path}.{key}:forbidden_field")
            findings.extend(_pii_findings(child, forbidden_fields, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_pii_findings(child, forbidden_fields, f"{path}[{index}]"))
    elif isinstance(value, str):
        if EMAIL_RE.search(value):
            findings.append(f"{path}:email_like_value")
        if PHONE_RE.search(value):
            findings.append(f"{path}:phone_like_value")
    return findings


def _validate_participant(row: dict[str, Any], contract: dict[str, Any]) -> None:
    allowed = {"participant_id", "residency", "consent_version", "consented_at_utc", "consent_active", "collection_wave"}
    required = {"participant_id", "residency", "consent_version", "consented_at_utc", "consent_active"}
    require(required.issubset(row) and set(row).issubset(allowed), "participant fields differ")
    require(re.fullmatch(contract["firewalls"]["participant_id_pattern"], str(row["participant_id"])) is not None, "participant ID format invalid")
    require(row["residency"] == "KR", "participant is outside the KR target cohort")
    require(row["consent_active"] is True and bool(row["consent_version"]), "active versioned consent is required")
    datetime.fromisoformat(str(row["consented_at_utc"]).replace("Z", "+00:00"))


def _validate_judgment(row: dict[str, Any], contract: dict[str, Any]) -> None:
    allowed = {"participant_id", "blind_item_token", "seen_status", "viewing_intent", "expected_satisfaction", "uninterested_reason", "actual_watch_14d", "submitted_at_utc"}
    required = {"participant_id", "blind_item_token", "seen_status", "viewing_intent", "expected_satisfaction", "uninterested_reason", "actual_watch_14d"}
    require(required.issubset(row) and set(row).issubset(allowed), "judgment fields differ")
    require(re.fullmatch(contract["firewalls"]["participant_id_pattern"], str(row["participant_id"])) is not None, "judgment participant ID invalid")
    require(re.fullmatch(r"^i_[0-9a-f]{20}$", str(row["blind_item_token"])) is not None, "blind item token invalid")
    require(row["seen_status"] in {"SEEN", "UNSEEN"}, "seen status invalid")
    require(type(row["viewing_intent"]) is int and 0 <= row["viewing_intent"] <= 4, "viewing intent out of range")
    require(type(row["expected_satisfaction"]) is int and 0 <= row["expected_satisfaction"] <= 4, "expected satisfaction out of range")
    require(row["uninterested_reason"] in UNINTERESTED_REASONS, "uninterested reason invalid")
    require(row["actual_watch_14d"] in {"YES", "NO", "UNKNOWN", None}, "14-day actual-watch value invalid")
    if row["viewing_intent"] <= int(contract["judgments"]["uninterested_reason_required_when_viewing_intent_lte"]):
        require(row["uninterested_reason"] != "NONE", "low-intent judgment requires an uninterested reason")
    else:
        require(row["uninterested_reason"] == "NONE", "high-intent judgment must use NONE reason")


def validate_participants_and_onboarding(
    participants: Sequence[dict[str, Any]],
    onboarding: Sequence[dict[str, Any]],
    contract: dict[str, Any],
) -> dict[str, Any]:
    forbidden = {field.lower() for field in contract["firewalls"]["forbidden_pii_fields"]}
    findings = _pii_findings([participants, onboarding], forbidden)
    if findings:
        raise PreflightError(f"PII firewall rejected input: {findings[0]}")
    participant_ids: set[str] = set()
    onboarding_ids: set[str] = set()
    for row in participants:
        _validate_participant(row, contract)
        require(row["participant_id"] not in participant_ids, "duplicate participant record")
        participant_ids.add(row["participant_id"])
    for row in onboarding:
        _validate_onboarding(row, contract)
        require(row["participant_id"] not in onboarding_ids, "duplicate onboarding record")
        onboarding_ids.add(row["participant_id"])
    require(participant_ids == onboarding_ids, "participant and K10 input cohorts differ")
    return {
        "participants": len(participant_ids),
        "all_kr_resident": True,
        "all_active_versioned_consent": True,
        "all_k10_anchor_gates_pass": True,
        "pii_stored": False,
    }


def import_judgments(
    participants: Sequence[dict[str, Any]],
    onboarding: Sequence[dict[str, Any]],
    judgments: Sequence[dict[str, Any]],
    pool: Sequence[dict[str, Any]],
    contract: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    forbidden = {field.lower() for field in contract["firewalls"]["forbidden_pii_fields"]}
    findings = _pii_findings([participants, onboarding, judgments], forbidden)
    if findings:
        raise PreflightError(f"PII firewall rejected input: {findings[0]}")
    participant_map: dict[str, dict[str, Any]] = {}
    input_map: dict[str, dict[str, Any]] = {}
    invalid_users: dict[str, list[str]] = defaultdict(list)
    for row in participants:
        try:
            _validate_participant(row, contract)
            require(row["participant_id"] not in participant_map, "duplicate participant record")
            participant_map[row["participant_id"]] = row
        except (PreflightError, ValueError) as error:
            invalid_users[str(row.get("participant_id", "UNKNOWN"))].append(str(error))
    for row in onboarding:
        try:
            _validate_onboarding(row, contract)
            require(row["participant_id"] not in input_map, "duplicate onboarding record")
            input_map[row["participant_id"]] = row
        except PreflightError as error:
            invalid_users[str(row.get("participant_id", "UNKNOWN"))].append(str(error))
    pool_by_user: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in pool:
        token = str(row["blind_item_token"])
        require(token not in pool_by_user[str(row["participant_id"])], "duplicate blind token in pool")
        pool_by_user[str(row["participant_id"])][token] = row
    judgment_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    raw_valid_shape_rows = 0
    for row in judgments:
        participant = str(row.get("participant_id", "UNKNOWN"))
        try:
            _validate_judgment(row, contract)
            raw_valid_shape_rows += 1
            judgment_by_user[participant].append(row)
        except PreflightError as error:
            invalid_users[participant].append(str(error))
    normalized: list[dict[str, Any]] = []
    valid_users: list[str] = []
    for participant in sorted(set(participant_map) | set(input_map) | set(judgment_by_user) | set(pool_by_user)):
        if participant not in participant_map:
            invalid_users[participant].append("participant record missing")
        if participant not in input_map:
            invalid_users[participant].append("K10 input missing")
        expected_pool = pool_by_user.get(participant, {})
        rows = judgment_by_user.get(participant, [])
        if len(expected_pool) != 48:
            invalid_users[participant].append("pool does not contain 48 items")
        tokens = [str(row["blind_item_token"]) for row in rows]
        if len(tokens) != len(set(tokens)):
            invalid_users[participant].append("duplicate judgment token")
        if set(tokens) != set(expected_pool):
            invalid_users[participant].append("judgments do not exactly cover the blind pool")
        if invalid_users.get(participant):
            continue
        valid_users.append(participant)
        for row in rows:
            pool_row = expected_pool[str(row["blind_item_token"])]
            compatibility = (int(row["viewing_intent"]) + int(row["expected_satisfaction"])) / 8.0
            normalized.append({
                "participant_id": participant,
                "blind_item_token": row["blind_item_token"],
                "movie_key": pool_row["movie_key"],
                "stratum": pool_row["stratum"],
                "seen_status": row["seen_status"],
                "viewing_intent": int(row["viewing_intent"]),
                "expected_satisfaction": int(row["expected_satisfaction"]),
                "compatibility": compatibility,
                "uninterested_reason": row["uninterested_reason"],
                "actual_watch_14d": row["actual_watch_14d"],
            })
    accepted = len(normalized)
    target_positives = sum(
        row["stratum"] == "RECENT_KR_LOW_POP" and row["seen_status"] == "UNSEEN" and row["compatibility"] >= 0.75
        for row in normalized
    )
    mapping_rate = accepted / max(1, len(judgments))
    summary = {
        "submitted_participant_rows": len(participants),
        "submitted_judgment_rows": len(judgments),
        "schema_valid_judgment_rows": raw_valid_shape_rows,
        "valid_users": len(valid_users),
        "accepted_unique_judgments": accepted,
        "unseen_recent_kr_low_pop_positives": target_positives,
        "mapping_and_dedup_rate": mapping_rate,
        "invalid_users": len(invalid_users),
        "invalid_reason_counts": dict(sorted(Counter(reason for reasons in invalid_users.values() for reason in reasons).items())),
        "pii_stored": False,
        "free_text_stored": False,
        "actual_watch_14d_role": "SECONDARY_ONLY",
    }
    return sorted(normalized, key=lambda row: (row["participant_id"], row["blind_item_token"])), summary


def linear_ndcg(ordered_relevance: Sequence[float], k: int = 10) -> float:
    observed = list(ordered_relevance[:k])
    if not observed:
        return 0.0
    dcg = sum(float(gain) / math.log2(index + 2) for index, gain in enumerate(observed))
    ideal = sorted((float(value) for value in ordered_relevance), reverse=True)[:k]
    idcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(ideal))
    return dcg / idcg if idcg > 0.0 else 0.0


def percentile(values: Sequence[float], q: float) -> float:
    require(bool(values), "percentile requires values")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def bootstrap_mean(values: Sequence[float], *, iterations: int, seed: int) -> dict[str, float | list[float]]:
    require(bool(values), "bootstrap requires paired users")
    rng = random.Random(seed)
    n = len(values)
    means = [sum(float(values[rng.randrange(n)]) for _ in range(n)) / n for _ in range(iterations)]
    mean = sum(float(value) for value in values) / n
    return {
        "mean": mean,
        "two_sided_95": [percentile(means, 0.025), percentile(means, 0.975)],
        "one_sided_95_upper": percentile(means, 0.95),
    }


def _per_user_metrics(
    participant: str,
    judgments: Sequence[dict[str, Any]],
    sealed_by_movie: dict[str, dict[str, Any]],
    *,
    excluded_selection_source: str | None = None,
) -> dict[str, dict[str, float]] | None:
    target = [
        row for row in judgments
        if row["participant_id"] == participant
        and row["stratum"] == "RECENT_KR_LOW_POP"
        and row["seen_status"] == "UNSEEN"
        and (excluded_selection_source is None or sealed_by_movie[row["movie_key"]]["selection_source_model"] != excluded_selection_source)
    ]
    minimum = 5 if excluded_selection_source is None else 3
    if len(target) < minimum or not any(float(row["compatibility"]) >= 0.75 for row in target):
        return None
    metrics: dict[str, dict[str, float]] = {}
    for model in SYSTEMS:
        ordered = sorted(target, key=lambda row: (sealed_by_movie[row["movie_key"]]["model_ranks"][model], row["movie_key"]))
        relevances = [float(row["compatibility"]) for row in ordered]
        metrics[model] = {
            "ndcg_at_10": linear_ndcg(relevances, 10),
            "top2_no_positive": float(not any(value >= 0.75 for value in relevances[:2])),
        }
    return metrics


def analyze_judgments(
    normalized: Sequence[dict[str, Any]],
    sealed_pool: Sequence[dict[str, Any]],
    import_summary: dict[str, Any],
    contract: dict[str, Any],
    *,
    evidence_mode: str,
) -> dict[str, Any]:
    sealed_by_user: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in sealed_pool:
        sealed_by_user[row["participant_id"]][row["movie_key"]] = row
    judgments_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in normalized:
        judgments_by_user[row["participant_id"]].append(row)
    user_metrics: dict[str, dict[str, dict[str, float]]] = {}
    for participant, rows in judgments_by_user.items():
        metrics = _per_user_metrics(participant, rows, sealed_by_user[participant])
        if metrics is not None:
            user_metrics[participant] = metrics
    iterations = int(contract["statistics"]["iterations"])
    seed = int(contract["statistics"]["seed"])
    candidate_results: dict[str, Any] = {}
    for candidate_index, candidate in enumerate(("B7", "B8", "B9"), start=1):
        participants = sorted(user_metrics)
        ndcg_deltas = [user_metrics[p][candidate]["ndcg_at_10"] - user_metrics[p]["B0"]["ndcg_at_10"] for p in participants]
        harm_deltas = [user_metrics[p][candidate]["top2_no_positive"] - user_metrics[p]["B0"]["top2_no_positive"] for p in participants]
        if ndcg_deltas:
            ndcg_bootstrap = bootstrap_mean(ndcg_deltas, iterations=iterations, seed=seed + candidate_index)
            harm_bootstrap = bootstrap_mean(harm_deltas, iterations=iterations, seed=seed + 100 + candidate_index)
            baseline_mean = sum(user_metrics[p]["B0"]["ndcg_at_10"] for p in participants) / len(participants)
            relative = float(ndcg_bootstrap["mean"]) / baseline_mean if baseline_mean > 0.0 else None
        else:
            ndcg_bootstrap = {"mean": None, "two_sided_95": [None, None], "one_sided_95_upper": None}
            harm_bootstrap = {"mean": None, "two_sided_95": [None, None], "one_sided_95_upper": None}
            baseline_mean = None
            relative = None
        robustness: dict[str, Any] = {}
        robustness_pass = True
        for source in SYSTEMS:
            deltas = []
            baselines = []
            for participant, rows in judgments_by_user.items():
                metrics = _per_user_metrics(participant, rows, sealed_by_user[participant], excluded_selection_source=source)
                if metrics is None:
                    continue
                deltas.append(metrics[candidate]["ndcg_at_10"] - metrics["B0"]["ndcg_at_10"])
                baselines.append(metrics["B0"]["ndcg_at_10"])
            mean_delta = sum(deltas) / len(deltas) if deltas else None
            mean_baseline = sum(baselines) / len(baselines) if baselines else None
            relative_delta = mean_delta / mean_baseline if mean_delta is not None and mean_baseline and mean_baseline > 0.0 else None
            passed = mean_delta is not None and mean_delta > 0.0 and relative_delta is not None and relative_delta >= 0.05
            robustness_pass = robustness_pass and passed
            robustness[source] = {"paired_users": len(deltas), "mean_delta_ndcg_at_10": mean_delta, "relative_improvement": relative_delta, "pass": passed}
        gate_pass = bool(
            ndcg_deltas
            and ndcg_bootstrap["two_sided_95"][0] is not None
            and float(ndcg_bootstrap["two_sided_95"][0]) > 0.0
            and relative is not None
            and relative >= 0.05
            and harm_bootstrap["one_sided_95_upper"] is not None
            and float(harm_bootstrap["one_sided_95_upper"]) <= 0.02
            and robustness_pass
        )
        candidate_results[candidate] = {
            "paired_users": len(ndcg_deltas),
            "baseline_mean_ndcg_at_10": baseline_mean,
            "paired_ndcg_delta": ndcg_bootstrap,
            "relative_ndcg_improvement": relative,
            "paired_top2_harm_delta": harm_bootstrap,
            "pool_source_robustness": robustness,
            "pool_source_robustness_pass": robustness_pass,
            "success_gate_pass": gate_pass,
        }
    completion = {
        "valid_users": int(import_summary["valid_users"]),
        "accepted_unique_judgments": int(import_summary["accepted_unique_judgments"]),
        "unseen_recent_kr_low_pop_positives": int(import_summary["unseen_recent_kr_low_pop_positives"]),
        "mapping_and_dedup_rate": float(import_summary["mapping_and_dedup_rate"]),
    }
    completion_pass = (
        completion["valid_users"] >= int(contract["completion_gates"]["valid_users_min"])
        and completion["accepted_unique_judgments"] >= int(contract["completion_gates"]["accepted_unique_judgments_min"])
        and completion["unseen_recent_kr_low_pop_positives"] >= int(contract["completion_gates"]["unseen_recent_kr_low_pop_positives_min"])
        and completion["mapping_and_dedup_rate"] >= float(contract["completion_gates"]["mapping_and_dedup_rate_min"])
    )
    if not completion_pass:
        status = "INSUFFICIENT_TARGET_DOMAIN_EVIDENCE"
    elif any(result["success_gate_pass"] for result in candidate_results.values()):
        status = "PASS_TARGET_DOMAIN_POOLED_JUDGMENT_REQUIRES_SEPARATE_PRODUCT_REVIEW"
    else:
        status = "FAIL_TARGET_DOMAIN_EFFECT_OR_SAFETY_GATE"
    return {
        "schema_version": 1,
        "evidence_id": "REC-EV-021V",
        "status": status,
        "evidence_mode": evidence_mode,
        "actual_target_domain_evidence": evidence_mode == "EXTERNAL" and completion_pass,
        "completion_gates_pass": completion_pass,
        "completion": completion,
        "primary_eligible_users": len(user_metrics),
        "primary_endpoint": "PAIRED_LINEAR_NDCG_AT_10_ON_UNSEEN_RECENT_KR_LOW_POP",
        "compatibility": "(viewing_intent + expected_satisfaction) / 8",
        "actual_watch_14d_used_in_primary": False,
        "candidate_results": candidate_results,
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }


def artifact_records(paths: Sequence[Path]) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(REPO_ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(paths)
    ]
