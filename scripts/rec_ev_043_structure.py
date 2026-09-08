"""Research adapter for REC043. E rating decoder is gated by a prediction seal."""

# ruff: noqa: E402 -- constrain numeric runtime before imports.
from __future__ import annotations

import os

for variable in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ[variable] = "1"

import hashlib
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psutil

import feelm_preference_structure as core

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/recommendation/experiments/rec-ev-043"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fingerprint():
    paths = [
        "scripts/feelm_preference_structure.py",
        "scripts/rec_ev_043_structure.py",
        "scripts/test_feelm_preference_structure.py",
        "scripts/test_rec_ev_043_adapter.py",
    ]
    paths += [
        f"docs/recommendation/experiments/rec-ev-043/{name}"
        for name in ("README.md", "config.json", "data-readiness.json")
    ]
    return {name: core.pin(ROOT / name) for name in paths}


class Budget:
    def __init__(self, out, seconds=900, memory=4294967296):
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


def input_path(config, name):
    return ROOT / config["inputs"][name]["path"]


def verify_inputs(config):
    for name, record in config["inputs"].items():
        core.require(
            core.pin(input_path(config, name))
            == {key: record[key] for key in ("bytes", "sha256")},
            "stale source: " + name,
        )
    prepare = read_json(input_path(config, "prepare_seal"))
    for name, filename in (
        ("prepared", "prepared.npz"),
        ("profiles", "profiles.parquet"),
    ):
        core.require(
            prepare["outputs"][filename] == core.pin(input_path(config, name)),
            "prepare lineage",
        )
    core.require(
        prepare["overlap"] == 0 and prepare["evaluation_users"] == config["users"],
        "split lineage",
    )
    completion = read_json(input_path(config, "conditional_completion"))
    for name, filename in (
        ("targets", "targets.parquet"),
        ("labels", "evaluation-labels.parquet"),
    ):
        core.require(
            completion["outputs"][filename] == core.pin(input_path(config, name)),
            "E lineage",
        )


def build_input(config):
    prepared = core.load_npz(input_path(config, "prepared"))
    ids = prepared["item_ids"]
    metadata = pd.read_parquet(
        input_path(config, "metadata"), columns=["movie_id", "genre_ids"]
    ).set_index("movie_id")
    core.require(
        metadata.index.is_unique and set(metadata.index) == set(ids), "metadata axis"
    )
    metadata = metadata.reindex(ids)
    genres = sorted(set(g for values in metadata.genre_ids for g in values) - {10770})
    core.require(len(genres) == 18, "content vocabulary")
    membership = np.zeros((len(ids), 34), dtype=np.uint8)
    for name, method, start in (
        ("assignments_rule", "RULE_GENRE_SET", 0),
        ("assignments_km", "KM_GENRE", 8),
    ):
        frame = pd.read_parquet(
            input_path(config, name),
            columns=["movie_id", "method", "native_label", "supported", "format_only"],
        )
        frame = frame[frame.method == method].set_index("movie_id")
        core.require(
            frame.index.is_unique and set(frame.index) == set(ids), "assignment axis"
        )
        frame = frame.reindex(ids)
        valid = frame.supported.to_numpy().astype(bool)
        labels = frame.native_label.to_numpy()
        core.require(((labels[valid] >= 0) & (labels[valid] < 8)).all(), "group labels")
        membership[np.flatnonzero(valid), start + labels[valid].astype(np.int64)] = 1
    lookup = {g: 16 + i for i, g in enumerate(genres)}
    for i, values in enumerate(metadata.genre_ids):
        for g in set(values) - {10770}:
            membership[i, lookup[g]] = 1
    common = (
        (membership[:, :8].sum(axis=1) > 0)
        & (membership[:, 8:16].sum(axis=1) > 0)
        & (membership[:, 16:].sum(axis=1) > 0)
    )
    profiles = pd.read_parquet(input_path(config, "profiles")).set_index("user_key")
    targets = pd.read_parquet(input_path(config, "targets")).set_index("user_key")
    core.require(
        profiles.index.is_unique
        and targets.index.is_unique
        and set(profiles.index) == set(targets.index),
        "user axes",
    )
    keys = np.array(sorted(profiles.index))
    core.require(len(keys) == config["users"], "user count")
    train_keys = {
        hashlib.sha256(f"rec-ev-022a-user-key-v1|{int(uid)}".encode()).hexdigest()
        for uid in prepared["training_user_ids"]
    }
    core.require(not train_keys.intersection(keys), "train/evaluation overlap")
    o_index = np.stack(
        [np.searchsorted(ids, profiles.at[key, "profile_movie_ids"]) for key in keys]
    )
    for u, key in enumerate(keys):
        core.require(
            np.array_equal(ids[o_index[u]], profiles.at[key, "profile_movie_ids"]),
            "O item axis",
        )
    raw = np.stack([profiles.at[key, "profile_rating_indices"] for key in keys])
    core.require(
        raw.shape == (len(keys), 30)
        and np.isin(raw, np.arange(10)).all()
        and common[o_index].all(),
        "O profile",
    )
    e_index, offsets, original = [], [0], 0
    for key in keys:
        eids = np.asarray(targets.at[key, "movie_ids"])
        ix = np.searchsorted(ids, eids)
        core.require(np.array_equal(ids[ix], eids), "E item axis")
        original += len(ix)
        ix = ix[common[ix]]
        e_index.extend(ix.tolist())
        offsets.append(len(e_index))
    core.require(
        original == config["E_original"] and len(e_index) == config["E_common"],
        "E common count",
    )
    zero = prepared["counts"] == 0
    core.require(
        zero.any() and np.ptp(prepared["bayes"][zero]) == 0,
        "global fallback inconsistency",
    )
    return dict(
        user_keys=keys,
        movie_ids=ids,
        membership=membership,
        bayes=prepared["bayes"],
        training_counts=prepared["counts"],
        o_index=o_index.astype(np.int64),
        o_ratings=(raw + 1) / 2,
        e_index=np.asarray(e_index, dtype=np.int64),
        e_offsets=np.asarray(offsets, dtype=np.int64),
        group_codes=np.array(
            [f"GENRE_SET_{i + 1:02d}" for i in range(8)]
            + [f"GENRE_KM_{i + 1:02d}" for i in range(8)]
            + [f"TMDB_{g}" for g in genres]
        ),
    )


def labels_after_seal(config, data, out):
    core.verify_prediction_seal(out / "input.npz", out / "run")
    # First E rating value decoder. Q/H columns are never requested.
    frame = pd.read_parquet(
        input_path(config, "labels"), columns=["user_key", "movie_id", "rating_raw"]
    )
    core.require(
        len(frame) == config["E_original"]
        and not frame.duplicated(["user_key", "movie_id"]).any(),
        "E labels duplicate/count",
    )
    frame = frame.set_index(["user_key", "movie_id"])
    axis = pd.MultiIndex.from_arrays(
        [
            np.repeat(data["user_keys"], np.diff(data["e_offsets"])),
            data["movie_ids"][data["e_index"]],
        ],
        names=["user_key", "movie_id"],
    )
    core.require(
        axis.difference(frame.index).empty and len(frame.index.difference(axis)) == 1,
        "E common label axis",
    )
    excluded = frame.index.difference(axis)[0]
    position = np.searchsorted(data["movie_ids"], excluded[1])
    core.require(
        data["movie_ids"][position] == excluded[1]
        and data["membership"][position, 16:].sum() == 0,
        "excluded E is not format only",
    )
    labels = {key: data[key] for key in ("user_keys", "e_index", "e_offsets")}
    labels["rating_raw"] = frame.reindex(axis).rating_raw.to_numpy()
    return labels


def main():
    config = read_json(DOC / "config.json")
    review = read_json(DOC / "execution-review.json")
    core.require(
        review["status"] == "PASS_EXECUTION" and review["fingerprint"] == fingerprint(),
        "STALE_REVIEW",
    )
    out = ROOT / config["output_root"]
    core.require(not out.exists(), "output exists; preserve previous run")
    out.mkdir(parents=True)
    budget = Budget(out, config["max_seconds"], config["max_process_tree_bytes"])
    budget.thread.start()
    try:
        verify_inputs(config)
        core.write_json(
            out / "request-seal.json",
            {
                "fingerprint": fingerprint(),
                "review": core.pin(DOC / "execution-review.json"),
                "inputs": config["inputs"],
                "E_values_opened": False,
            },
        )
        data = build_input(config)
        np.savez_compressed(out / "input.npz", **data)
        core.sealed_fit(out / "input.npz", out / "run")
        print("O-only preference probes sealed; opening E ratings.", flush=True)
        verify_inputs(config)
        labels = labels_after_seal(config, data, out)
        np.savez_compressed(out / "labels.npz", **labels)
        summary = core.sealed_evaluate(
            out / "input.npz", out / "labels.npz", out / "run", budget.check
        )
        budget.check()
        core.write_json(
            out / "completion-seal.json",
            {
                "status": "COMPLETE_AWAITING_AUDIT",
                "request": core.pin(out / "request-seal.json"),
                "input": core.pin(out / "input.npz"),
                "labels": core.pin(out / "labels.npz"),
                "prediction": core.pin(out / "run/prediction-seal.json"),
                "evaluation": core.pin(out / "run/evaluation-seal.json"),
                "seconds": time.monotonic() - budget.start,
                "peak_bytes": budget.peak,
                "python": sys.version,
                "numpy": np.__version__,
                "pandas": pd.__version__,
            },
        )
        print(
            json.dumps(
                {
                    "status": "COMPLETE_AWAITING_AUDIT",
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
