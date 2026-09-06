#!/usr/bin/env python3
"""Fit one feature-only LightFM seed for a REC-EV-027 outer split."""

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
    interactions = sparse.load_npz(args.interactions).tocsr().astype(np.float32)
    interactions.sort_indices()
    coo = interactions.tocoo(copy=True)
    order = np.lexsort((coo.col, coo.row))
    if not np.array_equal(order, np.arange(coo.nnz)):
        raise RuntimeError("REC-EV-027 LightFM COO order drift")
    if interactions.nnz == 0 or not set(interactions.data.tolist()) <= {-1.0, 1.0}:
        raise RuntimeError("REC-EV-027 LightFM requires signed observed interactions")
    item_features = sparse.load_npz(args.item_features).tocsr().astype(np.float32)
    item_features.sort_indices()
    if item_features.shape[0] != interactions.shape[1] or item_features.shape[1] <= 0:
        raise RuntimeError("REC-EV-027 LightFM item feature alignment drift")
    if not np.isfinite(item_features.data).all():
        raise RuntimeError("REC-EV-027 LightFM nonfinite item feature")

    seed = int(config["seed"])
    model = LightFM(
        no_components=int(config["dimension"]),
        loss="logistic",
        learning_schedule="adagrad",
        learning_rate=float(config["learning_rate"]),
        item_alpha=float(config["item_alpha"]),
        user_alpha=float(config["user_alpha"]),
        random_state=np.random.RandomState(seed),
    )
    model.fit(
        interactions,
        item_features=item_features,
        user_features=None,
        sample_weight=None,
        epochs=int(config["epochs"]),
        num_threads=1,
        verbose=True,
    )
    item_biases, item_factors = model.get_item_representations(item_features)
    if (
        item_biases.shape != (item_features.shape[0],)
        or item_factors.shape != (item_features.shape[0], int(config["dimension"]))
        or not np.isfinite(item_biases).all()
        or not np.isfinite(item_factors).all()
    ):
        raise RuntimeError("REC-EV-027 LightFM result drift")
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
