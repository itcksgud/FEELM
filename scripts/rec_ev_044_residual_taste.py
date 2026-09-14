"""Local REC044 runner. Independent review and prediction seals gate E decoding."""

# ruff: noqa: E402
from __future__ import annotations

import os

for variable in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ[variable] = "1"

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import scipy
from scipy import sparse

import feelm_preference_structure as prior
import feelm_residual_taste as core
import rec_ev_043_structure as adapter

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/recommendation/experiments/rec-ev-044"
FILES = [
    "scripts/feelm_residual_taste.py",
    "scripts/rec_ev_044_residual_taste.py",
    "scripts/test_feelm_residual_taste.py",
    "scripts/feelm_preference_structure.py",
    "scripts/rec_ev_043_structure.py",
] + [
    f"docs/recommendation/experiments/rec-ev-044/{name}"
    for name in ("README.md", "config.json", "data-readiness.json")
]


def fingerprint():
    return {name: prior.pin(ROOT / name) for name in FILES}


def verify_sources(config):
    for name, record in config["sources"].items():
        prior.require(
            prior.pin(ROOT / record["path"])
            == {k: record[k] for k in ("bytes", "sha256")},
            "stale source: " + name,
        )
    old = adapter.read_json(ROOT / config["sources"]["prior_config"]["path"])
    adapter.verify_inputs(old)
    prior.require(old["users"] == 2180 and old["E_common"] == 191932, "fixed cohort")
    return old


def verify_prediction(out):
    seal = adapter.read_json(out / "prediction-seal.json")
    prior.require(
        seal["fingerprint"] == fingerprint() and seal["E_values_opened"] is False,
        "prediction code seal",
    )
    for name, pin in seal["outputs"].items():
        prior.require(prior.pin(out / name) == pin, "prediction output seal: " + name)
    return seal


def labels_after_seal(out, old, data):
    verify_prediction(out)
    frame = pd.read_parquet(
        adapter.input_path(old, "labels"),
        columns=["user_key", "movie_id", "rating_raw"],
    )
    prior.require(
        len(frame) == old["E_original"]
        and not frame.duplicated(["user_key", "movie_id"]).any(),
        "E source axis",
    )
    frame = frame.set_index(["user_key", "movie_id"])
    axis = pd.MultiIndex.from_arrays(
        [
            np.repeat(data["user_keys"], np.diff(data["e_offsets"])),
            data["movie_ids"][data["e_index"]],
        ],
        names=["user_key", "movie_id"],
    )
    excluded = frame.index.difference(axis)
    prior.require(
        axis.difference(frame.index).empty and len(excluded) == 1, "E common axis"
    )
    index = np.searchsorted(data["movie_ids"], excluded[0][1])
    prior.require(
        data["movie_ids"][index] == excluded[0][1]
        and data["membership"][index, 16:].sum() == 0,
        "E exclusion",
    )
    return {
        **{k: data[k] for k in ("user_keys", "e_offsets", "e_index")},
        "rating_raw": frame.reindex(axis).rating_raw.to_numpy(),
    }


def main():
    config = adapter.read_json(DOC / "config.json")
    review = adapter.read_json(DOC / "execution-review.json")
    prior.require(
        review["status"] == "PASS_EXECUTION" and review["fingerprint"] == fingerprint(),
        "STALE_REVIEW",
    )
    out = ROOT / config["output_root"]
    prior.require(not out.exists(), "preserve previous output")
    out.mkdir(parents=True)
    budget = adapter.Budget(
        out, config["max_seconds"], config["max_process_tree_bytes"]
    )
    budget.thread.start()
    try:
        old = verify_sources(config)
        prior.write_json(
            out / "request-seal.json",
            dict(
                fingerprint=fingerprint(),
                review=prior.pin(DOC / "execution-review.json"),
                E_values_opened=False,
                sources=config["sources"],
            ),
        )
        # Rebuild from known O, train-only means, fixed metadata and label-free target IDs.
        data = adapter.build_input(old)
        existing = prior.load_npz(ROOT / config["sources"]["prior_input"]["path"])
        prior.require(
            data.keys() == existing.keys()
            and all(np.array_equal(data[k], existing[k]) for k in data),
            "REC043 replay",
        )
        np.savez_compressed(out / "input.npz", **data)
        metadata = pd.read_parquet(
            adapter.input_path(old, "metadata"), columns=["movie_id", "keyword_ids"]
        )
        metadata = metadata.set_index("movie_id").reindex(data["movie_ids"])
        matrices, vocabulary = core.features(
            data, metadata.keyword_ids, config["min_keyword_df"]
        )
        prior.require(
            len(vocabulary["keyword_ids"]) == 10818
            and vocabulary["keyword_nnz"] == 337368,
            "keyword readiness mismatch",
        )
        prior.write_json(out / "vocabulary.json", vocabulary)
        for m, x in zip(core.METHODS, matrices, strict=True):
            sparse.save_npz(out / f"features-{m}.npz", x)
        fitted = core.fit(data, matrices, config["ridge_strength"], budget.check)
        np.savez_compressed(out / "predictions.npz", **fitted)
        files = ["input.npz", "vocabulary.json", "predictions.npz"] + [
            f"features-{m}.npz" for m in core.METHODS
        ]
        prior.write_json(
            out / "prediction-seal.json",
            dict(
                fingerprint=fingerprint(),
                E_values_opened=False,
                outputs={name: prior.pin(out / name) for name in files},
            ),
        )
        print("O-only predictions sealed; opening E star values.", flush=True)
        verify_sources(config)
        labels = labels_after_seal(out, old, data)
        np.savez_compressed(out / "labels.npz", **labels)
        summary, details = core.evaluate(data, fitted, labels, budget.check)
        prior.write_json(out / "summary.json", summary)
        np.savez_compressed(out / "evaluation.npz", **details)
        budget.check()
        prior.write_json(
            out / "completion-seal.json",
            dict(
                status="COMPLETE_AWAITING_AUDIT",
                fingerprint=fingerprint(),
                outputs={
                    name: prior.pin(out / name)
                    for name in (
                        "request-seal.json",
                        "prediction-seal.json",
                        "summary.json",
                        "labels.npz",
                        "evaluation.npz",
                    )
                },
                seconds=time.monotonic() - budget.start,
                peak_bytes=budget.peak,
                python=sys.version,
                numpy=np.__version__,
                scipy=scipy.__version__,
                pandas=pd.__version__,
            ),
        )
        print(
            json.dumps(
                {
                    "status": "COMPLETE_AWAITING_AUDIT",
                    "seconds": time.monotonic() - budget.start,
                }
            )
        )
    except Exception as error:
        prior.write_json(
            out / "failure.json",
            dict(
                status="FAILED_PARTIAL_PRESERVED",
                type=type(error).__name__,
                reason=str(error),
            ),
        )
        raise
    finally:
        budget.stop.set()
        budget.thread.join(timeout=1)


if __name__ == "__main__":
    main()
