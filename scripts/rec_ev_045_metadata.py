"""REC045 metadata preparation only; no rating decoder, API calls, or model fit."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from feelm_preference_structure import pin, require, write_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/recommendation-evidence/rec-ev-045-metadata"


def positive_number(body, name, allow_zero=False):
    value = body.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not np.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        return None
    return float(value)


def main():
    require(not OUT.exists(), "preserve existing metadata preparation")
    OUT.mkdir(parents=True)
    started = time.monotonic()
    paths = {
        "metadata": ROOT / "outputs/recommendation-evidence/rec-ev-033/metadata.parquet",
        "ledger": ROOT / "outputs/recommendation-evidence/rec-ev-033/cache-ledger.parquet",
        "seal": ROOT / "outputs/recommendation-evidence/rec-ev-033/prepare-seal.json",
    }
    seal = json.loads(paths["seal"].read_text(encoding="utf-8"))
    for key, name in (("metadata", "metadata.parquet"), ("ledger", "cache-ledger.parquet")):
        require(pin(paths[key]) == seal["outputs"][name], "source seal: " + key)
    metadata = pd.read_parquet(paths["metadata"])
    ledger = pd.read_parquet(paths["ledger"]).set_index("path")
    require(ledger.index.is_unique and metadata.movie_id.is_unique, "source axes")
    allowed = [ROOT / f"outputs/recommendation-evidence/{name}/tmdb-cache"
               for name in ("rec-ev-019b", "rec-ev-027-catalog")]
    rows = []
    try:
        for i, row in enumerate(metadata.itertuples(index=False)):
            path = (ROOT / row.cache_path).resolve()
            require(any(path.is_relative_to(p.resolve()) for p in allowed), "cache scope")
            raw = path.read_bytes()
            expected = ledger.loc[row.cache_path]
            require(len(raw) == expected.bytes and hashlib.sha256(raw).hexdigest() == expected.sha256,
                    "cache pin")
            cached = json.loads(raw)
            body = cached["body"]
            require(body["id"] == row.tmdb_id and cached["body_sha256"] == row.response_sha256,
                    "cache identity")
            collection = body.get("belongs_to_collection")
            companies = sorted(set(int(x["id"]) for x in (body.get("production_companies") or []) if x.get("id")))
            rows.append({
                "movie_id": int(row.movie_id), "tmdb_id": int(row.tmdb_id),
                "production_company_ids": companies,
                "collection_ids": [int(collection["id"])] if isinstance(collection, dict) and collection.get("id") else [],
                "spoken_language_codes": sorted(set(x["iso_639_1"] for x in (body.get("spoken_languages") or []) if x.get("iso_639_1"))),
                "origin_country_codes": sorted(set(body.get("origin_country") or [])),
                "tmdb_vote_average": positive_number(body, "vote_average"),
                "tmdb_vote_count": positive_number(body, "vote_count", allow_zero=True),
                "tmdb_popularity": positive_number(body, "popularity", allow_zero=True),
                "budget_positive": positive_number(body, "budget"),
                "revenue_positive": positive_number(body, "revenue"),
                "overview_characters": len(body.get("overview") or ""),
                "fetched_at": str(cached.get("fetched_at") or ""),
            })
            if i % 10000 == 0:
                print(f"metadata {i}/{len(metadata)}; no ratings read", flush=True)
            require(time.monotonic() - started < 900, "metadata time budget")
        frame = pd.DataFrame(rows)
        frame.to_parquet(OUT / "extended-metadata.parquet", index=False)
        list_fields = ["production_company_ids", "collection_ids", "spoken_language_codes", "origin_country_codes"]
        summary = {
            "status": "METADATA_ONLY", "movies": len(frame), "rating_values_read": False,
            "network_requests": 0, "seconds": time.monotonic() - started,
            "list_fields": {name: {"nonempty_movies": int(frame[name].map(len).gt(0).sum()),
                                   "unique_values": len(set(v for values in frame[name] for v in values))}
                            for name in list_fields},
            "numeric_fields": {name: {"nonnull_movies": int(frame[name].notna().sum()),
                                      "quantiles": frame[name].quantile([0, .25, .5, .75, 1]).to_dict()}
                               for name in ("tmdb_vote_average", "tmdb_vote_count", "tmdb_popularity", "budget_positive", "revenue_positive")},
            "fetched_min": frame.fetched_at.min(), "fetched_max": frame.fetched_at.max(),
            "snapshot_aggregates_not_historical_forecast_inputs": True,
        }
        write_json(OUT / "summary.json", summary)
        write_json(OUT / "seal.json", {"status": "COMPLETE_METADATA_ONLY", "code": pin(__file__),
            "sources": {k: {"path": p.relative_to(ROOT).as_posix(), **pin(p)} for k, p in paths.items()},
            "outputs": {name: pin(OUT / name) for name in ("extended-metadata.parquet", "summary.json")}})
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    except Exception as error:
        write_json(OUT / "failure.json", {"type": type(error).__name__, "reason": str(error)})
        raise


if __name__ == "__main__":
    main()
