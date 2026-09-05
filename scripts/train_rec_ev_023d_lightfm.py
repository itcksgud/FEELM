#!/usr/bin/env python3
"""Fit one REC-EV-023D feature-only LightFM seed in the pinned container."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
from lightfm import LightFM
from scipy import sparse


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--interactions", type=Path, required=True)
    parser.add_argument("--item-features", type=Path, required=True)
    args = parser.parse_args()

    config = json.loads((args.job / "config.json").read_text(encoding="utf-8"))
    interactions = sparse.load_npz(args.interactions).tocoo().astype(np.float32)
    item_features = sparse.load_npz(args.item_features).tocsr().astype(np.float32)
    if interactions.nnz == 0 or set(interactions.data.tolist()) != {-1.0, 1.0}:
        raise RuntimeError("023D accepts only nonempty observed signed labels")
    if item_features.shape[0] != interactions.shape[1] or item_features.shape[1] <= 0:
        raise RuntimeError("023D item-feature alignment drift")
    if not np.isfinite(item_features.data).all():
        raise RuntimeError("023D item features contain nonfinite values")
    confidence = interactions.copy()
    confidence.data = np.ones_like(confidence.data, dtype=np.float32)

    model = LightFM(
        no_components=int(config["dimension"]),
        loss=str(config["loss"]),
        learning_schedule=str(config["learning_schedule"]),
        learning_rate=float(config["learning_rate"]),
        item_alpha=float(config["item_alpha"]),
        user_alpha=float(config["user_alpha"]),
        random_state=int(config["seed"]),
    )
    model.fit(
        interactions,
        item_features=item_features,
        sample_weight=confidence,
        epochs=int(config["epochs"]),
        num_threads=int(config["threads"]),
        verbose=True,
    )
    item_biases, item_factors = model.get_item_representations(item_features)
    norms = np.linalg.norm(item_factors.astype(np.float64), axis=1)
    if not np.isfinite(item_biases).all() or not np.isfinite(item_factors).all() or bool((norms <= 0).any()):
        raise RuntimeError("023D produced invalid item feature representations")
    destination = args.job / "result.npz"
    temporary = args.job / f".result.{os.getpid()}.tmp"
    with temporary.open("wb") as handle:
        np.savez_compressed(
            handle,
            item_biases=item_biases.astype(np.float32),
            item_factors=item_factors.astype(np.float32),
        )
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
