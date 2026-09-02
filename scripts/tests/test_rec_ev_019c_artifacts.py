from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from rec_ev_019c_artifacts import PredictionParquetSink, write_trial_metrics


class RecEv019CArtifactsTest(unittest.TestCase):
    def test_prediction_sink_commits_typed_parquet_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "predictions.parquet"
            sink = PredictionParquetSink(path, flush_rows=1)
            sink("B0", 0, "T1", "u", [{
                "rank": 1, "movie_id": 7, "effective_score": 0.9,
                "fallback_used": False, "fallback_reason": None,
            }])
            sink.close()
            frame = pd.read_parquet(path)
        self.assertEqual(1, len(frame))
        self.assertEqual(7, int(frame.iloc[0]["movie_id"]))

    def test_trial_metrics_use_contract_column_order(self) -> None:
        row = {
            "user_key": "u", "k": 5, "model_id": "B0", "trial_id": "T1", "seed": None,
            "evaluation_phase": "GRID", "ndcg_at_10": 0.1, "recall_at_10": 0.2,
            "mrr_at_10": 0.3, "positive_mean_rank_percentile": 0.4,
            "candidate_recall_at_500": 1.0, "fallback_user": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.parquet"
            write_trial_metrics(path, [row])
            columns = pd.read_parquet(path).columns.tolist()
        self.assertEqual(list(row), columns)


if __name__ == "__main__":
    unittest.main()
