"""Bounded two-stage REC-EV-019C experiment engine.

This module contains no file access. The CLI must pass checksum-verified Validation
inputs after its authorization and role-firewall checks have succeeded.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from rec_ev_019c_bounded_core import BudgetLedger, select_tuning_panel
from rec_ev_019c_data import PreparedInputs
from rec_ev_019c_evaluation import (
    ScoreResult,
    ValidationContext,
    aggregate_user_metrics,
    build_validation_contexts,
    evaluate_contexts,
    metrics_from_top_ranking,
    select_trial,
)
from rec_ev_019c_lightfm import LightfmRepresentations, build_lightfm_item_features
from rec_ev_019c_models import (
    BprFactors,
    bayesian_rating_scores,
    build_item_neighbor_columns,
    epoch_pair_arrays,
    fold_in_bpr_user,
    fold_in_logistic_user,
    item_knn_scores,
    percentile_scores,
    signed_dense_profile_scores,
    signed_sparse_profile_scores,
    train_bpr_minibatch,
)
from run_rec_ev_019c_validation import expand_trials, reciprocal_rank_fusion


LightfmFit = Callable[
    [Mapping[str, Any], Any, Any, Mapping[str, Any], int, Path],
    LightfmRepresentations,
]
PredictionSink = Callable[[str, int, str, str, Sequence[Mapping[str, Any]]], None]
ProgressSink = Callable[[Mapping[str, Any]], None]


@dataclass
class TuningResult:
    trial_user_metrics: list[dict[str, Any]]
    all_trial_metrics: list[dict[str, Any]]
    per_model_per_k: dict[str, dict[str, dict[str, Any]]]
    stability_panel: dict[str, Any]
    tuning_panel: dict[str, list[str]]
    panel_rankings: dict[tuple[str, int, str], dict[str, np.ndarray]]


class ExperimentEngine:
    def __init__(
        self,
        contract: Mapping[str, Any],
        inputs: PreparedInputs,
        ledger: BudgetLedger,
        *,
        lightfm_fit: LightfmFit | None = None,
        cache_root: Path | None = None,
        progress: ProgressSink | None = None,
    ) -> None:
        self.contract = contract
        self.inputs = inputs
        self.ledger = ledger
        self.lightfm_fit = lightfm_fit
        self.cache_root = cache_root or Path("outputs/recommendation-evidence/rec-ev-019c/cache")
        self.progress = progress or (lambda _: None)
        self.contexts = build_validation_contexts(
            inputs.validation_prefixes,
            inputs.validation_windows,
            inputs.movie_position,
        )
        expected = {
            int(k): int(value)
            for k, value in contract["source_preconditions"]["validation_strict_users_by_k"].items()
        }
        actual = {k: len(self.contexts[k]) for k in (0, 5, 10)}
        if actual != expected:
            raise RuntimeError(f"Validation user count drift: {actual}")
        users_by_k = {k: [context.user_key for context in values] for k, values in self.contexts.items()}
        panel_sizes = {
            int(k): int(value)
            for k, value in contract["resource_execution_plan"]["tuning_panel"]["users_per_k"].items()
        }
        self.tuning_panel = select_tuning_panel(users_by_k, panel_sizes)
        self.panel_contexts = {
            k: [context for context in self.contexts[k] if context.user_key in set(self.tuning_panel[k])]
            for k in (0, 5, 10)
        }
        self.fit_cache: dict[tuple[str, str, int], Any] = {}
        self.neighbor_columns: dict[int, Any] = {}
        self._b0_raw: dict[int, np.ndarray] = {}
        self._b0_percentile_by_k: dict[int, np.ndarray] = {}
        self._selected: dict[str, dict[int, dict[str, Any]]] = {}
        self._candidate_set = set(map(int, self.inputs.candidate_ids.tolist()))
        self._b0_order_by_k: dict[int, np.ndarray] = {}
        self._b0_rank_by_k: dict[int, np.ndarray] = {}

    @property
    def candidate_ids(self) -> np.ndarray:
        return self.inputs.candidate_ids

    def selected_b0_score(self, k: int) -> np.ndarray:
        selected = self._selected["B0_MOVIELENS_BAYESIAN_RATING"][int(k)]
        trial = next(
            row for row in expand_trials(self.contract)["B0_MOVIELENS_BAYESIAN_RATING"]
            if row["trial_id"] == selected["trial_id"]
        )
        return self._b0_scores(int(trial["parameters"]["prior_strength"]))

    def _emit(self, **values: Any) -> None:
        self.progress(values)

    def _charge_scores(self, contexts: Sequence[ValidationContext]) -> None:
        self.ledger.charge("full_catalog_user_item_scores", len(contexts) * len(self.candidate_ids))
        self.ledger.check_wall_clock()

    def _b0_scores(self, prior_strength: int) -> np.ndarray:
        if int(prior_strength) not in self._b0_raw:
            count = self.inputs.b0_rating_count
            mean = self.inputs.b0_rating_mean
            global_mean = float(np.sum(count * mean) / np.sum(count))
            self._b0_raw[int(prior_strength)] = bayesian_rating_scores(
                count, mean, global_mean=global_mean, prior_strength=float(prior_strength)
            )
        return self._b0_raw[int(prior_strength)]

    def _ensure_neighbors(self, contexts: Sequence[ValidationContext]) -> None:
        requested = sorted({int(position) for context in contexts for position in context.anchor_positions})
        missing = [position for position in requested if position not in self.neighbor_columns]
        if not missing:
            return
        model = self.contract["models"]["B2_ITEM_KNN"]
        built = build_item_neighbor_columns(
            self.inputs.base_binary,
            missing,
            maximum_neighbors=max(map(int, model["search_space"]["neighbors"])),
            shrink_values=list(map(float, model["search_space"]["shrink"])),
        )
        self.neighbor_columns.update(built)

    def _b4_pair_updates(self) -> int:
        matrix = self.inputs.base_binary
        maximum = int(self.contract["resource_execution_plan"]["b4_pair_sampling"]["maximum_pairs_per_user_per_epoch"])
        pairs = 0
        for user in range(matrix.shape[0]):
            start, stop = matrix.indptr[user : user + 2]
            values = matrix.data[start:stop]
            pairs += min(int(np.count_nonzero(values == 1)) * int(np.count_nonzero(values == -1)), maximum)
        return pairs * int(self.contract["models"]["B4_BPR_MF"]["fixed_parameters"]["epochs"])

    def _b4_epoch_pairs(self, seed: int, epoch: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        directory = self.cache_root / "b4-observed-pairs-v3"
        path = directory / f"S{seed}-E{epoch:02d}.npz"
        if path.is_file():
            cached = np.load(path, allow_pickle=False)
            return cached["users"], cached["likes"], cached["dislikes"]
        arrays = epoch_pair_arrays(
            self.inputs.base_binary,
            self.inputs.base_user_keys,
            seed=int(seed),
            epoch=int(epoch),
            maximum_pairs=int(
                self.contract["resource_execution_plan"]["b4_pair_sampling"]["maximum_pairs_per_user_per_epoch"]
            ),
        )
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f".S{seed}-E{epoch:02d}.{os.getpid()}.tmp"
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, users=arrays[0], likes=arrays[1], dislikes=arrays[2])
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return arrays

    def _fit(self, model_id: str, trial: Mapping[str, Any], seed: int) -> Any:
        key = (model_id, str(trial["trial_id"]), int(seed))
        if key in self.fit_cache:
            return self.fit_cache[key]
        parameters = trial["parameters"]
        if model_id == "B4_BPR_MF":
            fixed = self.contract["models"][model_id]["fixed_parameters"]
            fit_directory = self.cache_root / f"{trial['trial_id']}-S{seed}"
            fit_path = fit_directory / "bpr-result.npz"
            if fit_path.is_file():
                cached = np.load(fit_path, allow_pickle=False)
                fitted = BprFactors(np.empty((0, 0), dtype=np.float32), cached["item_factors"])
                expected = int(parameters["factors"])
                if fitted.item_factors.shape != (self.inputs.base_binary.shape[1], expected):
                    raise RuntimeError("B4 cached item-factor shape drift")
            else:
                self.ledger.charge("b4_pair_updates", self._b4_pair_updates())
                fitted = train_bpr_minibatch(
                    self.inputs.base_binary,
                    self.inputs.base_user_keys,
                    factors=int(parameters["factors"]),
                    regularization=float(parameters["regularization"]),
                    epochs=int(fixed["epochs"]),
                    learning_rate=float(fixed["learning_rate"]),
                    seed=int(seed),
                    maximum_pairs_per_user_epoch=int(
                        self.contract["resource_execution_plan"]["b4_pair_sampling"]["maximum_pairs_per_user_per_epoch"]
                    ),
                    batch_size=int(fixed["batch_size"]),
                    pair_provider=lambda epoch: self._b4_epoch_pairs(int(seed), epoch),
                )
                fitted = BprFactors(np.empty((0, 0), dtype=np.float32), fitted.item_factors)
                fit_directory.mkdir(parents=True, exist_ok=True)
                temporary = fit_directory / f".bpr-result.{os.getpid()}.tmp"
                with temporary.open("wb") as handle:
                    np.savez_compressed(
                        handle,
                        item_factors=fitted.item_factors,
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, fit_path)
        elif model_id == "B8_LIGHTFM":
            if self.lightfm_fit is None:
                raise RuntimeError("B8 pinned Linux fit backend is unavailable")
            fixed = self.contract["models"][model_id]["fixed_parameters"]
            self.ledger.charge("b8_base_updates", self.inputs.base_binary.nnz * int(fixed["epochs"]))
            item_features = build_lightfm_item_features(self.inputs.structured_by_variant["FULL"])
            fitted = self.lightfm_fit(
                self.contract,
                self.inputs.base_binary,
                item_features,
                parameters,
                int(seed),
                self.cache_root / f"{trial['trial_id']}-S{seed}",
            )
        else:
            fitted = None
        self.fit_cache[key] = fitted
        return fitted

    def _provider(
        self,
        model_id: str,
        trial: Mapping[str, Any],
        *,
        seed: int | None,
    ) -> Callable[[ValidationContext], ScoreResult]:
        parameters = trial["parameters"]
        candidate_count = len(self.candidate_ids)
        if model_id == "B0_MOVIELENS_BAYESIAN_RATING":
            raw = self._b0_scores(int(parameters["prior_strength"]))
            available = np.ones(candidate_count, dtype=bool)
            return lambda _: ScoreResult(raw, available, False)
        if model_id == "B2_ITEM_KNN":
            return lambda context: self._knn_score(context, parameters)
        if model_id == "B6_TMDB_STRUCTURED_CONTENT":
            matrix = self.inputs.structured_by_variant[str(parameters["variant"])]

            def structured(context: ValidationContext) -> ScoreResult:
                raw, available, fallback = signed_sparse_profile_scores(
                    matrix, context.anchor_positions, context.labels
                )
                return ScoreResult(
                    raw,
                    np.zeros(candidate_count, dtype=bool) if fallback else available,
                    fallback,
                    "UNDEFINED_TARGET_PROFILE" if fallback else "MISSING_STRUCTURED_FEATURE",
                )

            return structured
        if model_id == "B7_TMDB_TEXT_CONTENT":
            def text(context: ValidationContext) -> ScoreResult:
                raw, available, fallback = signed_dense_profile_scores(
                    self.inputs.text_embeddings,
                    self.inputs.text_available,
                    context.anchor_positions,
                    context.labels,
                )
                return ScoreResult(
                    raw,
                    np.zeros(candidate_count, dtype=bool) if fallback else available,
                    fallback,
                    "UNDEFINED_TARGET_PROFILE" if fallback else "MISSING_TEXT_FEATURE",
                )

            return text
        if model_id == "B4_BPR_MF":
            fitted: BprFactors = self._fit(model_id, trial, int(seed))

            def bpr(context: ValidationContext) -> ScoreResult:
                likes = context.anchor_positions[context.labels == 1]
                dislikes = context.anchor_positions[context.labels == -1]
                user, fallback = fold_in_bpr_user(
                    fitted.item_factors,
                    likes,
                    dislikes,
                    regularization=float(parameters["regularization"]),
                    learning_rate=float(self.contract["models"][model_id]["fixed_parameters"]["learning_rate"]),
                )
                available = np.zeros(candidate_count, dtype=bool) if fallback else np.ones(candidate_count, dtype=bool)
                return ScoreResult(
                    fitted.item_factors @ user,
                    available,
                    fallback,
                    "PREFIX_LACKS_EITHER_BINARY_CLASS" if fallback else None,
                )

            return bpr
        if model_id == "B8_LIGHTFM":
            fitted: LightfmRepresentations = self._fit(model_id, trial, int(seed))

            def lightfm(context: ValidationContext) -> ScoreResult:
                bias, user, fallback = fold_in_logistic_user(
                    fitted.item_biases,
                    fitted.item_factors,
                    context.anchor_positions,
                    context.labels,
                    regularization=float(self.contract["models"][model_id]["fixed_parameters"]["user_alpha"]),
                    learning_rate=float(self.contract["models"][model_id]["fixed_parameters"]["learning_rate"]),
                )
                available = np.zeros(candidate_count, dtype=bool) if fallback else np.ones(candidate_count, dtype=bool)
                return ScoreResult(
                    bias + fitted.item_biases + fitted.item_factors @ user,
                    available,
                    fallback,
                    "PREFIX_LACKS_EITHER_BINARY_CLASS" if fallback else None,
                )

            return lightfm
        raise KeyError(f"unsupported single-head model: {model_id}")

    def _knn_score(self, context: ValidationContext, parameters: Mapping[str, Any]) -> ScoreResult:
        raw, available, fallback = item_knn_scores(
            self.neighbor_columns,
            context.anchor_positions,
            context.labels,
            candidate_count=len(self.candidate_ids),
            neighbors=int(parameters["neighbors"]),
            shrink=float(parameters["shrink"]),
        )
        return ScoreResult(
            raw,
            np.zeros(len(self.candidate_ids), dtype=bool) if fallback else available,
            fallback,
            "NO_NONZERO_NEIGHBOR_SUPPORT" if fallback else "NO_ITEM_NEIGHBOR_SUPPORT",
        )

    def _evaluate(
        self,
        model_id: str,
        trial: Mapping[str, Any],
        k: int,
        contexts: Sequence[ValidationContext],
        *,
        phase: str,
        seed: int | None,
        b0_percentiles: np.ndarray,
    ) -> tuple[list[dict[str, Any]], dict[str, np.ndarray]]:
        if model_id == "B2_ITEM_KNN":
            self._ensure_neighbors(contexts)
        self._charge_scores(contexts)
        metric_rows, predictions = evaluate_contexts(
            contexts,
            self._provider(model_id, trial, seed=seed),
            candidate_ids=self.candidate_ids,
            b0_percentiles=b0_percentiles,
            top_candidates=int(self.contract["candidate_and_ranking"]["top_candidates"]),
            top_k=int(self.contract["candidate_and_ranking"]["top_k"]),
        )
        annotated = [
            {
                **row,
                "model_id": model_id,
                "trial_id": trial["trial_id"],
                "seed": seed,
                "evaluation_phase": phase,
            }
            for row in metric_rows
        ]
        ranking_ids = {
            user_key: np.asarray([row["movie_id"] for row in rows], dtype=np.int32)
            for user_key, rows in predictions.items()
        }
        self._emit(phase=phase, model_id=model_id, trial_id=trial["trial_id"], seed=seed, k=k, users=len(contexts))
        return annotated, ranking_ids

    def run_tuning(self) -> TuningResult:
        trials = expand_trials(self.contract)
        all_rows: list[dict[str, Any]] = []
        all_aggregates: list[dict[str, Any]] = []
        panel_rankings: dict[tuple[str, int, str], dict[str, np.ndarray]] = {}
        selected: dict[str, dict[int, dict[str, Any]]] = {}

        for trial in trials["B0_MOVIELENS_BAYESIAN_RATING"]:
            b0 = percentile_scores(
                self._b0_scores(int(trial["parameters"]["prior_strength"])),
                np.ones(len(self.candidate_ids), dtype=bool),
            )
            for k in (0, 5, 10):
                rows, rankings = self._evaluate(
                    "B0_MOVIELENS_BAYESIAN_RATING", trial, k, self.panel_contexts[k],
                    phase="GRID", seed=None, b0_percentiles=b0,
                )
                all_rows.extend(rows)
                aggregate = {
                    "model_id": "B0_MOVIELENS_BAYESIAN_RATING", "trial_id": trial["trial_id"], "k": k,
                    **aggregate_user_metrics(rows),
                }
                all_aggregates.append(aggregate)
                panel_rankings[("B0_MOVIELENS_BAYESIAN_RATING", k, trial["trial_id"])] = rankings
        selected["B0_MOVIELENS_BAYESIAN_RATING"] = {}
        for k in (0, 5, 10):
            winner = select_trial([row for row in all_aggregates if row["model_id"] == "B0_MOVIELENS_BAYESIAN_RATING" and row["k"] == k])
            selected["B0_MOVIELENS_BAYESIAN_RATING"][k] = winner
            trial = next(row for row in trials["B0_MOVIELENS_BAYESIAN_RATING"] if row["trial_id"] == winner["trial_id"])
            self._b0_percentile_by_k[k] = percentile_scores(
                self._b0_scores(int(trial["parameters"]["prior_strength"])),
                np.ones(len(self.candidate_ids), dtype=bool),
            )
            order = np.lexsort((self.candidate_ids, -self._b0_percentile_by_k[k])).astype(np.int32)
            ranks = np.empty(len(order), dtype=np.int32)
            ranks[order] = np.arange(1, len(order) + 1, dtype=np.int32)
            self._b0_order_by_k[k] = order
            self._b0_rank_by_k[k] = ranks

        for model_id in ("B2_ITEM_KNN", "B4_BPR_MF", "B6_TMDB_STRUCTURED_CONTENT", "B7_TMDB_TEXT_CONTENT", "B8_LIGHTFM"):
            selected[model_id] = {}
            for trial in trials[model_id]:
                seed = 17 if self.contract["models"][model_id]["stochastic_seeds"] else None
                for k in (5, 10):
                    rows, rankings = self._evaluate(
                        model_id, trial, k, self.panel_contexts[k], phase="GRID", seed=seed,
                        b0_percentiles=self._b0_percentile_by_k[k],
                    )
                    all_rows.extend(rows)
                    aggregate = {"model_id": model_id, "trial_id": trial["trial_id"], "k": k, **aggregate_user_metrics(rows)}
                    all_aggregates.append(aggregate)
                    panel_rankings[(model_id, k, trial["trial_id"])] = rankings
            for k in (5, 10):
                selected[model_id][k] = select_trial(
                    [row for row in all_aggregates if row["model_id"] == model_id and row["k"] == k]
                )

        stability: dict[str, Any] = {}
        for model_id in ("B4_BPR_MF", "B8_LIGHTFM"):
            stability[model_id] = {}
            by_trial: dict[str, list[int]] = {}
            for k in (5, 10):
                by_trial.setdefault(selected[model_id][k]["trial_id"], []).append(k)
            for trial_id, k_values in by_trial.items():
                trial = next(row for row in trials[model_id] if row["trial_id"] == trial_id)
                for seed in (42, 73, 101, 211):
                    for k in k_values:
                        rows, _ = self._evaluate(
                            model_id, trial, k, self.panel_contexts[k], phase="STABILITY", seed=seed,
                            b0_percentiles=self._b0_percentile_by_k[k],
                        )
                        all_rows.extend(rows)
                        aggregate = {
                            "model_id": model_id, "trial_id": trial_id, "k": k, "seed": seed,
                            **aggregate_user_metrics(rows),
                        }
                        all_aggregates.append(aggregate)
                    self.fit_cache.pop((model_id, trial_id, int(seed)), None)
            for k in (5, 10):
                winner = selected[model_id][k]
                seed_rows = [
                    row for row in all_aggregates
                    if row["model_id"] == model_id and row["trial_id"] == winner["trial_id"] and row["k"] == k
                ]
                values = [float(row["user_macro_ndcg_at_10"]) for row in seed_rows]
                stability[model_id][str(k)] = {
                    "trial_id": winner["trial_id"],
                    "seeds": [17, 42, 73, 101, 211],
                    "ndcg_mean": float(np.mean(values)),
                    "ndcg_std": float(np.std(values, ddof=1)),
                }

        selected["B9_RRF"] = {}
        for trial in trials["B9_RRF"]:
            parameters = trial["parameters"]
            heads = self.contract["models"]["B9_RRF"]["head_sets"][parameters["head_set"]]
            for k in (5, 10):
                metric_rows: list[dict[str, Any]] = []
                rankings_by_user: dict[str, np.ndarray] = {}
                for context in self.panel_contexts[k]:
                    component = [
                        panel_rankings[(head, k, selected[head][k]["trial_id"])][context.user_key].tolist()
                        for head in heads
                    ]
                    row, predictions = self._evaluate_rrf_context(
                        context, component, c=int(parameters["c"])
                    )
                    metric_rows.append({"user_key": context.user_key, "k": k, **row})
                    rankings_by_user[context.user_key] = np.asarray(
                        [row["movie_id"] for row in predictions], dtype=np.int32
                    )
                annotated = [
                    {**row, "model_id": "B9_RRF", "trial_id": trial["trial_id"], "seed": None, "evaluation_phase": "GRID"}
                    for row in metric_rows
                ]
                all_rows.extend(annotated)
                aggregate = {"model_id": "B9_RRF", "trial_id": trial["trial_id"], "k": k, **aggregate_user_metrics(annotated)}
                all_aggregates.append(aggregate)
                panel_rankings[("B9_RRF", k, trial["trial_id"])] = rankings_by_user
        for k in (5, 10):
            selected["B9_RRF"][k] = select_trial(
                [row for row in all_aggregates if row["model_id"] == "B9_RRF" and row["k"] == k]
            )
        self._selected = selected
        return TuningResult(
            trial_user_metrics=all_rows,
            all_trial_metrics=all_aggregates,
            per_model_per_k={model: {str(k): value for k, value in values.items()} for model, values in selected.items()},
            stability_panel=stability,
            tuning_panel={str(k): values for k, values in self.tuning_panel.items()},
            panel_rankings=panel_rankings,
        )

    def _evaluate_rrf_context(
        self,
        context: ValidationContext,
        component_rankings: Sequence[Sequence[int]],
        *,
        c: int,
        fallback_user: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        self.ledger.charge("rrf_rank_contributions", sum(map(len, component_rankings)))
        fused = reciprocal_rank_fusion(component_rankings, c=int(c))
        fused_ids = [int(row["movie_id"]) for row in fused]
        fused_rank = {movie_id: rank for rank, movie_id in enumerate(fused_ids, start=1)}
        fused_set = set(fused_ids)
        seen_positions = set(map(int, context.anchor_positions.tolist()))
        removed_positions = seen_positions.union(
            self.inputs.movie_position[movie_id] for movie_id in fused_set
        )
        b0_ranks = self._b0_rank_by_k[context.k]

        def exact_rank(movie_id: int) -> int | None:
            if int(movie_id) not in self.inputs.movie_position:
                return None
            if int(movie_id) in fused_rank:
                return fused_rank[int(movie_id)]
            position = self.inputs.movie_position[int(movie_id)]
            if position in seen_positions:
                return None
            base_rank = int(b0_ranks[position])
            removed_before = sum(int(b0_ranks[item]) < base_rank for item in removed_positions)
            return len(fused_ids) + base_rank - removed_before

        top = fused[: int(self.contract["candidate_and_ranking"]["top_candidates"])]
        metrics = metrics_from_top_ranking(
            [row["movie_id"] for row in top],
            context.evaluation_rows,
            candidate_set=self._candidate_set,
            candidate_count_after_seen=len(self.candidate_ids) - len(seen_positions),
            exact_rank_provider=exact_rank,
            top_k=int(self.contract["candidate_and_ranking"]["top_k"]),
            top_candidates=int(self.contract["candidate_and_ranking"]["top_candidates"]),
            fallback_user=fallback_user,
        )
        predictions = [
            {
                "rank": rank,
                "movie_id": int(row["movie_id"]),
                "effective_score": float(row["rrf_score"]),
                "fallback_used": bool(fallback_user),
                "fallback_reason": "ALL_COMPONENT_HEADS_FELL_BACK" if fallback_user else None,
            }
            for rank, row in enumerate(top, start=1)
        ]
        return metrics, predictions

    def run_full_validation(self, prediction_sink: PredictionSink) -> list[dict[str, Any]]:
        if not self._selected:
            raise RuntimeError("tuning selection must complete before full Validation")
        trials = expand_trials(self.contract)
        metrics: list[dict[str, Any]] = []
        head_rankings: dict[tuple[int, str], dict[str, np.ndarray]] = {}
        model_ids = [
            "B0_MOVIELENS_BAYESIAN_RATING", "B2_ITEM_KNN", "B4_BPR_MF",
            "B6_TMDB_STRUCTURED_CONTENT", "B7_TMDB_TEXT_CONTENT", "B8_LIGHTFM",
        ]
        for model_id in model_ids:
            k_values = (0, 5, 10) if model_id == "B0_MOVIELENS_BAYESIAN_RATING" else (5, 10)
            for k in k_values:
                selected = self._selected[model_id][k]
                trial = next(row for row in trials[model_id] if row["trial_id"] == selected["trial_id"])
                seed = 17 if self.contract["models"][model_id]["stochastic_seeds"] else None
                if model_id == "B2_ITEM_KNN":
                    self._ensure_neighbors(self.contexts[k])
                provider = self._provider(model_id, trial, seed=seed)
                if model_id != "B0_MOVIELENS_BAYESIAN_RATING":
                    head_rankings[(k, model_id)] = {}
                batch_size = int(self.contract["candidate_and_ranking"]["user_batch_size_max"])
                for batch_start in range(0, len(self.contexts[k]), batch_size):
                    batch = self.contexts[k][batch_start : batch_start + batch_size]
                    self._charge_scores(batch)
                    metric_rows, predictions = evaluate_contexts(
                        batch, provider,
                        candidate_ids=self.candidate_ids,
                        b0_percentiles=self._b0_percentile_by_k[k],
                        top_candidates=int(self.contract["candidate_and_ranking"]["top_candidates"]),
                        top_k=int(self.contract["candidate_and_ranking"]["top_k"]),
                    )
                    for row in metric_rows:
                        metrics.append({**row, "model_id": model_id})
                    for context in batch:
                        rows = predictions[context.user_key]
                        prediction_sink(model_id, k, trial["trial_id"], context.user_key, rows)
                        if model_id != "B0_MOVIELENS_BAYESIAN_RATING":
                            head_rankings[(k, model_id)][context.user_key] = np.asarray(
                                [row["movie_id"] for row in rows], dtype=np.int32
                            )
                    self._emit(
                        phase="FULL_VALIDATION", model_id=model_id, trial_id=trial["trial_id"],
                        seed=seed, k=k, batch_start=batch_start, users=len(batch),
                    )

        for k in (5, 10):
            selected_rrf = self._selected["B9_RRF"][k]
            trial = next(row for row in trials["B9_RRF"] if row["trial_id"] == selected_rrf["trial_id"])
            parameters = trial["parameters"]
            heads = self.contract["models"]["B9_RRF"]["head_sets"][parameters["head_set"]]
            for context in self.contexts[k]:
                component = [head_rankings[(k, head)][context.user_key].tolist() for head in heads]
                row, predictions = self._evaluate_rrf_context(
                    context, component, c=int(parameters["c"])
                )
                metrics.append({"user_key": context.user_key, "k": k, **row, "model_id": "B9_RRF"})
                prediction_sink("B9_RRF", k, trial["trial_id"], context.user_key, predictions)
            self._emit(phase="FULL_VALIDATION", model_id="B9_RRF", trial_id=trial["trial_id"], seed=None, k=k, users=len(self.contexts[k]))
        return metrics
