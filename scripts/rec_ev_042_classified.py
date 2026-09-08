"""Reviewed research adapter: cached scores/roles -> sealed selection -> observed labels."""
# ruff: noqa: E402 -- thread limits must precede NumPy and pandas imports.

from __future__ import annotations

import os

for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psutil

import feelm_classified_evaluation as core

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/recommendation/experiments/rec-ev-042"
CODE_FILES = (
    "scripts/feelm_classified_evaluation.py",
    "scripts/rec_ev_042_classified.py",
    "scripts/test_feelm_classified_evaluation.py",
    "scripts/test_rec_ev_042_adapter.py",
)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def expected_pin(record):
    return {key: record[key] for key in ("bytes", "sha256")}


def fingerprint():
    paths = list(CODE_FILES) + [
        f"docs/recommendation/experiments/rec-ev-042/{name}"
        for name in (
            "README.md",
            "config.json",
            "data-readiness.json",
            "implementation.json",
        )
    ]
    return {path: core.pin(ROOT / path) for path in paths}


class Budget:
    """Wall-time/process-tree RSS watchdog, including during blocking library calls."""

    def __init__(self, out, seconds, memory):
        self.out, self.seconds, self.memory = out, seconds, memory
        self.start, self.peak = time.monotonic(), 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.watch, daemon=True)

    def check(self):
        process = psutil.Process()
        rss = process.memory_info().rss
        for child in process.children(recursive=True):
            try:
                rss += child.memory_info().rss
            except psutil.NoSuchProcess:
                pass
        self.peak = max(self.peak, rss)
        core.require(
            time.monotonic() - self.start <= self.seconds and rss <= self.memory,
            "RESOURCE_BUDGET_EXCEEDED",
        )

    def watch(self):
        while not self.stop.wait(0.25):
            try:
                self.check()
            except Exception:
                core.write_json(
                    self.out / "budget-failure.json",
                    {
                        "status": "RESOURCE_BUDGET_EXCEEDED",
                        "seconds": time.monotonic() - self.start,
                        "peak_bytes": self.peak,
                    },
                )
                os._exit(2)


def verify_sources(config):
    for name, record in config["inputs"].items():
        core.require(
            core.pin(ROOT / record["path"]) == expected_pin(record),
            "stale input: " + name,
        )

    def get(key):
        return read_json(ROOT / config["inputs"][key]["path"])

    def pinned(key):
        return expected_pin(config["inputs"][key])

    score, role, train, prepare = [
        get(key)
        for key in ("als_score_seal", "roles_score_seal", "train_seal", "prepare_seal")
    ]
    core.require(
        score["outputs"]["target-order.npz"] == pinned("order")
        and score["label_payload_opened"] is False,
        "ALS order lineage",
    )
    core.require(
        role["outputs"]["eligibility.npz"] == pinned("eligibility")
        and role["E_H_decoded"] is False,
        "role lineage",
    )
    for source, destination in [
        ("profiles", "profiles"),
        ("labelsE", "labels"),
        ("histogramsE", "histograms"),
        ("train_seal", "train_seal"),
        ("prepare_seal", "prepare_seal"),
    ]:
        core.require(
            expected_pin(role["inputs"][source]) == pinned(destination),
            "role input lineage",
        )
    core.require(
        train["prepare_seal"] == pinned("prepare_seal")
        and prepare["overlap"] == 0
        and prepare["evaluation_users"] == config["users"],
        "training split lineage",
    )
    core.require(
        score["source_factors"]
        == train["outputs"]["factors.npz"]
        == expected_pin(role["inputs"]["factors"]),
        "factor checkpoint lineage",
    )
    core.require(
        prepare["outputs"]["profiles.parquet"] == pinned("profiles"), "profile lineage"
    )
    # Completion/review content is immutable through the design's fourteen input hashes.
    return {
        "evaluation_training_user_overlap": prepare["overlap"],
        "training_users": train["training_users"],
        "training_rows": train["training_rows"],
        "factors": train["outputs"]["factors.npz"],
    }


def load_candidates(config, guard):
    def path(key):
        return ROOT / config["inputs"][key]["path"]

    with np.load(path("order"), allow_pickle=False) as source:
        order = {k: source[k] for k in source.files}
    with np.load(path("eligibility"), allow_pickle=False) as source:
        role = {k: source[k] for k in source.files}
    keys, offsets, ids = order["user_keys"], order["offsets"], order["movie_ids"]
    core.require(
        len(keys) == config["users"] and len(ids) == config["observed_pairs"],
        "source counts",
    )
    core.require(
        np.array_equal(keys, role["user_keys"])
        and len(role["movie_ids"]) == config["catalog_items"],
        "role axes",
    )
    core.require(
        role["methods"].tolist() == list(core.METHODS[1:])
        and role["roles"].tolist() == ["T", "D"],
        "role semantics",
    )
    catalog = role["movie_ids"]
    core.require((np.diff(catalog) > 0).all(), "catalog order")
    pos = np.searchsorted(catalog, ids)
    core.require(
        (pos < len(catalog)).all() and np.array_equal(catalog[pos], ids), "catalog IDs"
    )
    core.require(
        np.array_equal(role["factor_support"][pos], order["factor_supported"]),
        "factor axes",
    )
    profiles = pd.read_parquet(
        path("profiles"), columns=["user_key", "profile_movie_ids"]
    ).set_index("user_key")
    targets = pd.read_parquet(
        path("targets"), columns=["user_key", "movie_ids"]
    ).set_index("user_key")
    for frame in (profiles, targets):
        core.require(
            frame.index.is_unique and set(frame.index) == set(keys), "source user axes"
        )
    profile_ids = np.stack(
        [profiles.at[key, "profile_movie_ids"] for key in keys]
    ).astype(np.int64)
    core.require(profile_ids.shape == (len(keys), config["n"]), "O30 shape")
    taste, discovery = np.zeros((len(ids), 2), bool), np.zeros((len(ids), 2), bool)
    for u, key in enumerate(keys):
        part = slice(offsets[u], offsets[u + 1])
        core.require(
            np.array_equal(ids[part], targets.at[key, "movie_ids"]), "E membership"
        )
        for method in range(2):
            bits = np.unpackbits(
                role["packed"][u, method], axis=1, count=len(catalog), bitorder="big"
            )
            taste[part, method], discovery[part, method] = (
                bits[0, pos[part]],
                bits[1, pos[part]],
            )
        if u % 100 == 0:
            guard()
    return dict(
        user_keys=keys,
        offsets=offsets,
        movie_ids=ids,
        global_ranks=order["global_ranks"][:, 3],
        scores=order["global_scores"][:, 3],
        factor_supported=order["factor_supported"],
        format_only=role["format_only"][pos].astype(bool),
        taste=taste,
        discovery=discovery,
        profile_movie_ids=profile_ids,
    )


def load_labels_after_seal(config, candidates, out):
    seal = read_json(out / "run/selection-seal.json")
    core.require(
        seal["status"] == "SELECTED_BEFORE_LABELS"
        and seal["label_payload_opened"] is False
        and seal["selection"] == core.pin(out / "run/selection.npz")
        and seal["candidate"] == core.pin(out / "candidates.npz")
        and seal["code"] == core.pin(core.__file__),
        "UNSEALED_RATING_ACCESS",
    )
    # No E/H decoder is called above this line.
    labels = pd.read_parquet(ROOT / config["inputs"]["labels"]["path"])
    core.require(
        not labels.duplicated(["user_key", "movie_id"]).any(), "duplicate E labels"
    )
    with np.load(
        ROOT / config["inputs"]["histograms"]["path"], allow_pickle=False
    ) as source:
        core.require(
            np.array_equal(source["user_keys"], candidates["user_keys"]), "H user axis"
        )
        histograms = source["histograms"]
    axis = pd.MultiIndex.from_arrays(
        [
            np.repeat(candidates["user_keys"], np.diff(candidates["offsets"])),
            candidates["movie_ids"],
        ],
        names=["user_key", "movie_id"],
    )
    labels = labels.set_index(["user_key", "movie_id"])
    core.require(
        len(labels) == len(axis)
        and labels.index.difference(axis).empty
        and axis.difference(labels.index).empty,
        "E label membership",
    )
    labels = labels.reindex(axis)
    bundle = {name: candidates[name] for name in ("user_keys", "offsets", "movie_ids")}
    bundle.update(
        rating_raw=labels["rating_raw"].to_numpy(),
        source_q=labels["q"].to_numpy(),
        histograms=histograms,
    )
    return bundle


def main():
    config = read_json(DOC / "config.json")
    review = read_json(DOC / "execution-review.json")
    core.require(
        review["status"] == "PASS_EXECUTION" and review["fingerprint"] == fingerprint(),
        "STALE_INPUT_OR_REVIEW",
    )
    out = ROOT / config["output_root"]
    core.require(
        not out.exists(), "output already exists; preserve previous/partial run"
    )
    out.mkdir(parents=True)
    budget = Budget(out, config["max_seconds"], config["max_process_tree_bytes"])
    budget.thread.start()
    try:
        lineage = verify_sources(config)
        core.write_json(
            out / "request-seal.json",
            {
                "status": "READY",
                "fingerprint": fingerprint(),
                "execution_review": core.pin(DOC / "execution-review.json"),
                "inputs": config["inputs"],
                "lineage": lineage,
                "label_payload_opened": False,
            },
        )
        candidates = load_candidates(config, budget.check)
        np.savez_compressed(out / "candidates.npz", **candidates)
        _, selected = core.sealed_select(out / "candidates.npz", out / "run")
        ready = read_json(DOC / "data-readiness.json")
        core.require(
            int(selected["pool_counts"].sum()) == ready["common_candidate_pairs"]
            and int((selected["counts"] >= 3).all(axis=1).sum())
            == ready["common_full_three_users"],
            "readiness differs",
        )
        for method in (1, 2):
            core.require(
                int((selected["types"][:, method, 2] == core.DISCOVERY).sum())
                == ready["methods"][core.METHODS[method]]["discovery_users"],
                "D readiness differs",
            )
        print("Selection sealed; opening E/H for evaluation.", flush=True)
        # Revalidate every source before the one-way transition to scoring.
        verify_sources(config)
        labels = load_labels_after_seal(config, candidates, out)
        np.savez_compressed(out / "labels.npz", **labels)
        summary = core.sealed_evaluate(
            out / "candidates.npz", out / "labels.npz", out / "run", budget.check
        )
        budget.check()
        core.write_json(
            out / "completion-seal.json",
            {
                "status": "COMPLETE_AWAITING_RESULT_REVIEW",
                "request": core.pin(out / "request-seal.json"),
                "selection": core.pin(out / "run/selection-seal.json"),
                "evaluation": core.pin(out / "run/evaluation-seal.json"),
                "candidates": core.pin(out / "candidates.npz"),
                "labels": core.pin(out / "labels.npz"),
                "seconds": time.monotonic() - budget.start,
                "peak_process_tree_bytes": budget.peak,
                "python": sys.version,
                "numpy": np.__version__,
                "pandas": pd.__version__,
            },
        )
        print(
            json.dumps(
                {
                    "status": "COMPLETE_AWAITING_RESULT_REVIEW",
                    "users": summary["users"],
                    "seconds": time.monotonic() - budget.start,
                }
            ),
            flush=True,
        )
    except Exception as error:
        core.write_json(
            out / "failure.json",
            {
                "status": "FAILED_PARTIAL_PRESERVED",
                "type": type(error).__name__,
                "reason": str(error),
            },
        )
        raise
    finally:
        budget.stop.set()
        budget.thread.join(timeout=1)


if __name__ == "__main__":
    main()
