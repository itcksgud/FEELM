#!/usr/bin/env python3
"""Fit one contracted B8 base model inside the pinned Linux container."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import scipy.sparse as sparse
from lightfm import LightFM


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args()
    job = args.job.resolve()
    config = json.loads((job / "config.json").read_text(encoding="utf-8"))
    interactions = sparse.load_npz(job / "interactions.npz").tocoo().astype(np.float32)
    item_features = sparse.load_npz(job / "item-features.npz").tocsr().astype(np.float32)
    if set(interactions.data.tolist()) != {-1.0, 1.0}:
        raise RuntimeError("B8 accepts only observed signed labels")
    if item_features.shape[0] != interactions.shape[1]:
        raise RuntimeError("B8 item-feature rows differ from candidate items")
    confidence = interactions.copy()
    confidence.data = np.ones_like(confidence.data, dtype=np.float32)
    model = LightFM(
        no_components=int(config["dimension"]),
        loss="logistic",
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
        num_threads=1,
        verbose=True,
    )
    item_biases, item_factors = model.get_item_representations(item_features)
    if not np.isfinite(item_biases).all() or not np.isfinite(item_factors).all():
        raise RuntimeError("B8 produced non-finite item representations")
    np.savez_compressed(
        job / "result.npz",
        item_biases=item_biases.astype(np.float32),
        item_factors=item_factors.astype(np.float32),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
