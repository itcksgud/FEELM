"""Verify the completed REC-EV-027 rating-independent catalog artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_rec_ev_027_catalog_features import derive_rating_independent_catalog  # noqa: E402
from build_rec_ev_019b_features import sha256_file  # noqa: E402
from build_rec_ev_027_catalog_features import DEFAULT_BUILD, validate_build_contract  # noqa: E402
from rec_ev_022a_core import build_structured_full  # noqa: E402
from validate_rec_ev_027_contract import DEFAULT, ROOT, validate  # noqa: E402


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    validate(contract, verify_files=True)
    build_contract_path = ROOT / contract["allowed_input_artifacts"]["catalog_build_contract"]["path"]
    build_contract = json.loads(build_contract_path.read_text(encoding="utf-8"))
    validate_build_contract(build_contract)
    outputs = {key: ROOT / value for key, value in contract["catalog_content_build"]["outputs"].items()}
    for path in outputs.values():
        require(path.is_file(), f"missing catalog artifact: {path}")

    manifest = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
    summary = json.loads(outputs["summary"].read_text(encoding="utf-8"))
    require(manifest["status"] == "PASS_RATING_INDEPENDENT_FULL_CATALOG_BUILD", "manifest status drift")
    require(
        manifest["catalog_build_contract_sha256"] == sha256_file(build_contract_path),
        "catalog-build contract hash drift",
    )
    require(
        manifest["implementation_sha256"] == build_contract["implementation"]["sha256"],
        "catalog implementation hash drift",
    )
    expected_implementations = [
        {key: build_contract["implementation"][key] for key in ("path", "bytes", "sha256")},
        *build_contract["implementation"]["transitive_helpers"],
    ]
    require(manifest["implementation_artifacts"] == expected_implementations, "catalog helper provenance drift")
    require(summary["status"] == manifest["status"], "summary status drift")
    require(manifest["ratings_member_opened"] is False, "manifest says ratings opened")
    require(summary["archive_members_opened"] == ["ml-32m/movies.csv", "ml-32m/links.csv"], "archive member audit drift")
    require(summary["ratings_member_opened"] is False and summary["rating_values_opened"] is False, "catalog build label leak")
    pins = {row["path"]: row for row in manifest["artifacts"]}
    for key in ("identity", "structured", "embeddings", "domain_projection", "summary"):
        path = outputs[key]
        relative = path.relative_to(ROOT).as_posix()
        require(relative in pins, f"manifest missing {key}")
        require(path.stat().st_size == int(pins[relative]["bytes"]), f"{key} byte drift")
        require(sha256_file(path) == pins[relative]["sha256"], f"{key} hash drift")

    archive = Path(contract["allowed_input_artifacts"]["movielens_archive"]["path"])
    catalog, opened = derive_rating_independent_catalog(archive)
    require(opened == ["ml-32m/movies.csv", "ml-32m/links.csv"], "verifier archive member drift")
    identity = pd.read_parquet(outputs["identity"])
    structured = pd.read_parquet(outputs["structured"])
    embeddings = pd.read_parquet(outputs["embeddings"])
    domains = pd.read_parquet(outputs["domain_projection"])
    expected_ids = catalog["movie_id"].astype(int).tolist()
    require(int(catalog["link_row_present"].fillna(False).sum()) == 87585, "links.csv row coverage drift")
    require(identity["movie_id"].astype(int).tolist() == expected_ids, "identity does not exactly cover movies.csv")
    for name, frame in (("identity", identity), ("structured", structured), ("embeddings", embeddings), ("domains", domains)):
        require(not frame["movie_id"].duplicated().any(), f"{name} duplicate movie_id")
        require(frame["movie_id"].is_monotonic_increasing, f"{name} not movie_id sorted")
    feature_ids = structured["movie_id"].astype(int).tolist()
    require(embeddings["movie_id"].astype(int).tolist() == feature_ids, "structured/E5 identity mismatch")
    require(domains["movie_id"].astype(int).tolist() == feature_ids, "structured/domain identity mismatch")
    require(set(feature_ids) <= set(expected_ids), "feature item outside catalog")
    require(embeddings["model_id"].eq(contract["item_universe"]["e5_model_id"]).all(), "E5 model drift")
    require(embeddings["model_revision"].eq(contract["item_universe"]["e5_revision"]).all(), "E5 revision drift")
    vectors = np.stack(embeddings["embedding"].map(lambda value: np.asarray(value, dtype=np.float32)))
    require(vectors.shape == (len(embeddings), 384), "E5 shape drift")
    require(bool(np.isfinite(vectors).all()), "nonfinite E5 vector")
    norms = np.linalg.norm(vectors, axis=1)
    require(bool((np.abs(norms - 1.0) <= 0.0001).all()), "E5 norm drift")
    require(domains["tmdb_korean_origin_proxy"].dtype == bool, "Korean proxy dtype drift")
    derived_kr = domains["production_country_codes"].map(lambda values: "KR" in list(values))
    require(domains["tmdb_korean_origin_proxy"].equals(derived_kr), "Korean proxy flag/content mismatch")
    require(summary["catalog_movies"] == len(expected_ids) == identity.shape[0], "catalog count mismatch")
    require(summary["attempted_movies"] == len(expected_ids), "not every catalog movie attempted/reused")
    require(summary["nonterminal_http_failures"] == 0, "nonterminal HTTP failure was frozen")
    require(summary["identity_eligible"] == len(structured), "eligible count mismatch")
    structured_matrix = build_structured_full(structured, structured["movie_id"].astype(int).to_numpy())
    structured_norms = np.sqrt(np.asarray(structured_matrix.multiply(structured_matrix).sum(axis=1)).ravel())
    final_structured_ids = set(structured.loc[structured_norms > 0, "movie_id"].astype(int))
    text_ids = set(embeddings.loc[embeddings["feature_eligible"], "movie_id"].astype(int))
    result = {
        "status": "PASS_REC_EV_027_RATING_INDEPENDENT_CATALOG",
        "catalog_movies": len(expected_ids),
        "identity_eligible": len(structured),
        "structured_eligible": int(structured["feature_eligible"].sum()),
        "text_eligible": int(embeddings["feature_eligible"].sum()),
        "final_structured_positive_norm": len(final_structured_ids),
        "common_feature_eligible": len(final_structured_ids & text_ids),
        "tmdb_korean_origin_proxy": int(domains["tmdb_korean_origin_proxy"].sum()),
        "ratings_member_opened": False,
        "locked_test_opened": False,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
