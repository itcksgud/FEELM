"""Metadata-only support audit; never opens O/E star values or fits a model."""

import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from feelm_preference_structure import pin

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "axis": "outputs/recommendation-evidence/rec-ev-044/input.npz",
    "structured": "outputs/recommendation-evidence/rec-ev-027-catalog/structured-features.parquet",
    "countries": "outputs/recommendation-evidence/rec-ev-027-catalog/domain-projection.parquet",
}


def main():
    with np.load(ROOT / SOURCES["axis"], allow_pickle=False) as source:
        # Read IDs/positions only. o_ratings and all E rating sources are unused.
        ids, oi, ei, offsets = [
            source[k] for k in ("movie_ids", "o_index", "e_index", "e_offsets")
        ]
    frame = pd.read_parquet(ROOT / SOURCES["structured"]).set_index("movie_id")
    domain = pd.read_parquet(ROOT / SOURCES["countries"]).set_index("movie_id")
    assert frame.index.is_unique and domain.index.is_unique
    assert set(ids).issubset(frame.index) and set(ids).issubset(domain.index)
    frame, domain = frame.reindex(ids), domain.reindex(ids)
    result = {
        "status": "METADATA_SUPPORT_ONLY_NOT_PREFERENCE_EVIDENCE",
        "catalog_movies": len(ids),
        "users": len(oi),
        "O_pairs": int(oi.size),
        "E_pairs": len(ei),
        "rating_values_read": False,
        "model_fit": False,
        "sources": {
            name: {"path": path, **pin(ROOT / path)} for name, path in SOURCES.items()
        },
        "audit_code": pin(__file__),
        "fields": {},
    }
    for name, values in (
        ("director_ids", frame.director_ids),
        ("top5_cast_ids", frame.top5_cast_ids),
        ("production_country_codes", domain.production_country_codes),
    ):
        sets = [set(row) for row in values]
        missing = np.array([not row for row in sets])
        overlap, repeated = 0, 0
        for u in range(len(oi)):
            assert len(set(oi[u])) == 30
            support = Counter(value for index in oi[u] for value in sets[index])
            for index in ei[offsets[u] : offsets[u + 1]]:
                n = max((support[v] for v in sets[index]), default=0)
                overlap += n >= 1
                repeated += n >= 2
        result["fields"][name] = {
            "catalog_missing": int(missing.sum()),
            "O_missing": int(missing[oi].sum()),
            "E_missing": int(missing[ei].sum()),
            "E_with_O_shared_value": int(overlap),
            "E_with_value_in_two_O_movies": int(repeated),
            "overlap_pair_fraction": overlap / len(ei),
            "repeated_pair_fraction": repeated / len(ei),
        }
    for name in ("original_language", "release_year", "runtime_minutes"):
        missing = frame[name].isna().to_numpy()
        if name == "original_language":
            missing |= frame[name].fillna("").eq("").to_numpy()
        else:
            missing |= frame[name].fillna(0).le(0).to_numpy()
        result["fields"][name] = {
            "catalog_missing": int(missing.sum()),
            "O_missing": int(missing[oi].sum()),
            "E_missing": int(missing[ei].sum()),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
