from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from .bias import _integer_ids, _ratings
from .errors import ArtifactCompatibilityError, ArtifactValidationError
from .metadata import ArtifactKind, ArtifactMetadata


@dataclass(frozen=True, slots=True)
class ItemFactorModel:
    item_ids: npt.NDArray[np.int64]
    factors: npt.NDArray[np.float64]
    reg_param: float

    def __post_init__(self) -> None:
        if self.item_ids.ndim != 1 or self.factors.ndim != 2:
            raise ArtifactValidationError("item IDs must be 1D and factors must be 2D")
        if len(self.item_ids) == 0 or len(self.item_ids) != len(self.factors):
            raise ArtifactValidationError("item IDs and factors must be non-empty and aligned")
        if bool((self.item_ids < 0).any()) or not np.isfinite(self.factors).all():
            raise ArtifactValidationError("item factor payload contains invalid values")
        if len(np.unique(self.item_ids)) != len(self.item_ids):
            raise ArtifactValidationError("item factor IDs must be unique")
        if self.factors.shape[1] <= 0 or self.reg_param < 0:
            raise ArtifactValidationError("factor rank must be positive and reg_param non-negative")
        order = np.argsort(self.item_ids, kind="stable")
        if not np.array_equal(order, np.arange(len(order))):
            object.__setattr__(self, "item_ids", self.item_ids[order])
            object.__setattr__(self, "factors", self.factors[order])

    @property
    def rank(self) -> int:
        return int(self.factors.shape[1])

    @classmethod
    def load_npz(
        cls,
        payload_path: str | Path,
        metadata: ArtifactMetadata,
    ) -> "ItemFactorModel":
        metadata.require_kind(ArtifactKind.ALS_ITEM_FACTORS)
        metadata.verify_payload(payload_path)
        try:
            reg_param = float(metadata.parameters["reg_param"])
        except (KeyError, TypeError, ValueError) as error:
            raise ArtifactValidationError("factor metadata reg_param is invalid") from error
        if reg_param < 0:
            raise ArtifactValidationError("reg_param must be non-negative")
        try:
            with np.load(payload_path, allow_pickle=False) as payload:
                names = set(payload.files)
                if {"item_ids", "item_factors"} <= names:
                    id_name, factor_name = "item_ids", "item_factors"
                elif {"movie_ids", "movie_factors"} <= names:
                    id_name, factor_name = "movie_ids", "movie_factors"
                else:
                    raise ArtifactValidationError(
                        "factor payload must contain item_ids/item_factors or movie_ids/movie_factors"
                    )
                result = cls(
                    item_ids=np.asarray(payload[id_name], dtype=np.int64),
                    factors=np.asarray(payload[factor_name], dtype=np.float64),
                    reg_param=reg_param,
                )
        except (OSError, ValueError) as error:
            if isinstance(error, ArtifactValidationError):
                raise
            raise ArtifactValidationError(f"cannot load item factors: {error}") from error
        if metadata.factor_rank is None:
            raise ArtifactValidationError("item-factor metadata must declare factor_rank")
        if result.rank != metadata.factor_rank:
            raise ArtifactCompatibilityError(
                f"factor rank mismatch: metadata {metadata.factor_rank}, payload {result.rank}"
            )
        return result

    def lookup(self, item_ids: npt.ArrayLike):
        items = _integer_ids(item_ids, "item_ids")
        positions = np.searchsorted(self.item_ids, items)
        in_range = positions < len(self.item_ids)
        known = np.zeros(len(items), dtype=bool)
        known[in_range] = self.item_ids[positions[in_range]] == items[in_range]
        values = np.full((len(items), self.rank), np.nan, dtype=np.float64)
        values[known] = self.factors[positions[known]]
        return values, known

    def fold_in(self, item_ids: npt.ArrayLike, ratings: npt.ArrayLike) -> "FoldInResult":
        items, stars = _integer_ids(item_ids, "item_ids"), _ratings(ratings)
        if len(items) == 0 or len(items) != len(stars):
            raise ValueError("non-empty aligned item IDs and ratings are required")
        item_factors, known = self.lookup(items)
        available = item_factors[known]
        known_ratings = stars[known]
        count = len(known_ratings)
        if count == 0:
            return FoldInResult(None, provided_count=len(stars), factor_count=0)
        normal = available.T @ available
        # Matches Spark explicit ALS-WR: lambda is scaled by this user's known rating count.
        normal.flat[:: self.rank + 1] += self.reg_param * count
        right = available.T @ known_ratings
        try:
            factor = np.linalg.solve(normal, right)
        except np.linalg.LinAlgError:
            factor = np.linalg.lstsq(normal, right, rcond=None)[0]
        return FoldInResult(factor, provided_count=len(stars), factor_count=count)

    def score(self, user_factor: npt.ArrayLike, item_ids: npt.ArrayLike):
        factor = np.asarray(user_factor, dtype=np.float64)
        if factor.shape != (self.rank,) or not np.isfinite(factor).all():
            raise ValueError(f"user factor must be a finite vector with rank {self.rank}")
        values, known = self.lookup(item_ids)
        result = np.full(len(values), np.nan, dtype=np.float64)
        result[known] = values[known] @ factor
        return result, known


@dataclass(frozen=True, slots=True)
class FoldInResult:
    factor: npt.NDArray[np.float64] | None
    provided_count: int
    factor_count: int

    @property
    def available(self) -> bool:
        return self.factor is not None
