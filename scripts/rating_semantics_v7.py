"""One rating/event contract shared by v7 GBT and FM."""
from __future__ import annotations

import numpy as np


def utility(stars):
    values = np.asarray(stars, dtype=np.float32)
    return np.select(
        [values >= 5.0, values >= 4.5, values >= 4.0, values >= 3.5, values >= 3.0, values >= 2.5],
        [1.0, 0.8, 0.6, 0.35, 0.15, -0.9],
        default=-1.1,
    ).astype(np.float32)


def positive_weight(stars):
    return np.clip(utility(stars), 0.0, None)


def negative_weight(stars):
    return np.clip(-utility(stars), 0.0, None)


def relevance(stars):
    values = np.asarray(stars, dtype=np.float32)
    return np.select(
        [values >= 5.0, values >= 4.5, values >= 4.0, values >= 3.5, values >= 3.0],
        [5, 4, 3, 2, 1],
        default=0,
    ).astype(np.int8)


def pair_weight(positive_stars, negative_stars):
    positive = np.maximum(positive_weight(positive_stars), 0.15)
    negative = np.maximum(negative_weight(negative_stars), 0.9)
    return (positive + negative).astype(np.float32)
