"""Synthetic boundary checks; no research payload is read."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import feelm_classified_evaluation as core
import numpy as np


def fixture():
    n, width = 3, 6
    taste = np.tile(
        np.array([[1, 0], [1, 1], [0, 1], [1, 0], [0, 0], [0, 0]], dtype=bool), (n, 1)
    )
    discovery = np.tile(
        np.array([[0, 1], [0, 0], [1, 0], [0, 0], [0, 0], [0, 0]], dtype=bool), (n, 1)
    )
    c = dict(
        user_keys=np.array(["a", "b", "c"]),
        offsets=np.arange(n + 1) * width,
        movie_ids=np.tile(np.arange(1, width + 1), n),
        global_ranks=np.tile(np.arange(1, width + 1), n),
        scores=np.tile(np.arange(width, 0, -1, dtype=float), n),
        factor_supported=np.ones(n * width, bool),
        format_only=np.zeros(n * width, bool),
        taste=taste,
        discovery=discovery,
        profile_movie_ids=np.array([[100], [101], [102]]),
    )
    # Half-star bins 1..10 occur twice each in each user's full history.
    raw = np.tile(np.array([0.5, 1.0, 2.0, 3.0, 4.0, 5.0]), n)
    labels = {k: c[k].copy() for k in ("user_keys", "offsets", "movie_ids")}
    labels.update(
        rating_raw=raw,
        histograms=np.full((n, 10), 2, dtype=np.int64),
        source_q=(2 * raw - 0.5) / 10,
    )
    return c, labels


class SelectionTests(unittest.TestCase):
    def test_roles_and_exclusion(self):
        c, _ = fixture()
        s = core.select(c)
        self.assertEqual(
            s["selected_indices"][0].tolist(), [[0, 1, 2], [0, 1, 2], [1, 2, 0]]
        )
        self.assertEqual(s["types"][0].tolist(), [[1, 1, 1], [2, 2, 3], [2, 2, 3]])
        c["format_only"][0] = True
        s = core.select(c)
        self.assertEqual(s["selected_indices"][0, 0].tolist(), [1, 2, 3])
        self.assertEqual(s["selected_indices"][0, 1].tolist(), [1, 3, 2])

    def test_no_d_fallback_and_t_shortage(self):
        c, _ = fixture()
        c["discovery"][:6] = False
        s = core.select(c)
        self.assertEqual(
            s["statuses"][0, 1:].tolist(), ["NO_DISCOVERY_T3", "NO_DISCOVERY_NO_T3"]
        )
        self.assertEqual(s["counts"][0].tolist(), [3, 3, 2])
        c["taste"][:6, 1] = False
        c["taste"][2, 1] = True
        c["discovery"][0, 1] = True
        s = core.select(c)
        self.assertEqual(s["counts"][0, 2], 1)
        self.assertEqual(s["types"][0, 2].tolist(), [2, 0, 0])

    def test_rank_tie_and_invalid_order(self):
        c, _ = fixture()
        c["scores"][0] = c["scores"][1]
        core.select(c)
        c["global_ranks"][:2] = [2, 1]
        with self.assertRaisesRegex(ValueError, "rank/score"):
            core.select(c)

    def test_unsupported_movie_and_overlap(self):
        c, _ = fixture()
        c["factor_supported"][0] = False
        c["global_ranks"][0] = 0
        c["scores"][0] = -np.inf
        self.assertNotIn(0, core.select(c)["selected_indices"][0].ravel())
        c["profile_movie_ids"][0, 0] = 1
        with self.assertRaisesRegex(ValueError, "O/E"):
            core.select(c)

    def test_bad_axis_and_role_overlap(self):
        c, _ = fixture()
        c["offsets"][-1] -= 1
        with self.assertRaisesRegex(ValueError, "offsets"):
            core.select(c)
        c, _ = fixture()
        c["discovery"][0, 0] = True
        with self.assertRaisesRegex(ValueError, "role masks"):
            core.select(c)


class ScoringTests(unittest.TestCase):
    def test_half_stars_and_source_q(self):
        c, labels = fixture()
        num, den = core.validate_labels(c, labels)
        np.testing.assert_array_equal(num[:6], [2, 6, 14, 22, 30, 38])
        np.testing.assert_array_equal(den, [40, 40, 40])
        labels["rating_raw"][0] = 0.75
        with self.assertRaisesRegex(ValueError, "half-star"):
            core.validate_labels(c, labels)
        c, labels = fixture()
        labels["source_q"][0] += 0.01
        with self.assertRaisesRegex(ValueError, "source Q differs"):
            core.validate_labels(c, labels)

    def test_histogram_and_axis_rejected(self):
        c, labels = fixture()
        labels["movie_ids"][0] = 999
        with self.assertRaisesRegex(ValueError, "label axis"):
            core.validate_labels(c, labels)
        c, labels = fixture()
        labels["histograms"][0, 0] = 0
        with self.assertRaisesRegex(ValueError, "E exceeds"):
            core.validate_labels(c, labels)

    def test_exact_threshold_integer_ties_and_common_users(self):
        c, _ = fixture()
        s = core.select(c)
        num = np.tile(np.array([2, 6, 10, 14, 18, 22]), 3)
        den = np.array([50, 50, 50])
        values, scale, valid = core.panel_numerators(s, num, den, 2)
        self.assertEqual(values[0, 2, 2], 1)  # q=0.2 included.
        delta, common = core.paired_deltas(values, scale, valid)
        np.testing.assert_array_equal(delta[:, :3], 0)
        self.assertTrue(common.all())
        # High histogram counts: integer sum ties stay exactly zero before division.
        values[0, 0, 0] = values[0, 1, 0] = 19999999999
        scale[0, 0] = 40000000000
        self.assertEqual(core.paired_deltas(values, scale, valid)[0][0, 0], 0)
        valid[0, 2] = False
        self.assertEqual(core.paired_deltas(values, scale, valid)[0].shape, (2, 9))
        valid[1, 2] = False
        with self.assertRaisesRegex(ValueError, "common users < 2"):
            core.paired_deltas(values, scale, valid)

    def test_bootstrap_shared_draws_and_quantile(self):
        d = np.arange(27, dtype=float).reshape(3, 9) / 27
        draws, ci = core.bootstrap(d, repeats=257)
        rng = np.random.Generator(np.random.PCG64(20260908))
        expected = d[rng.integers(0, 3, size=(257, 3), dtype=np.int64)].mean(axis=1)
        np.testing.assert_array_equal(draws, expected)
        np.testing.assert_array_equal(
            ci, np.quantile(expected, [1 / 360, 359 / 360], axis=0, method="linear").T
        )
        with self.assertRaisesRegex(RuntimeError, "budget"):
            core.bootstrap(
                d,
                repeats=257,
                guard=lambda: (_ for _ in ()).throw(RuntimeError("budget")),
            )

    def test_missing_d_null_and_zero_interval_undecided(self):
        c, labels = fixture()
        c["discovery"][:] = False
        c["taste"][:] = True
        summary, _ = core.evaluate(c, core.select(c), labels)
        self.assertEqual(summary["methods"][1]["discovery"]["provided"], 0)
        self.assertIsNone(summary["methods"][1]["discovery"]["mean_q"])
        self.assertEqual(summary["secondary_descriptive"][0]["users"], 0)
        self.assertTrue(
            all(
                x["direction"] == "UNDECIDED" and x["interval"] == [0, 0]
                for x in summary["primary"]
            )
        )


class SealTests(unittest.TestCase):
    def test_tampered_selection_cannot_open_labels(self):
        c, labels = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            np.savez(root / "c.npz", **c)
            np.savez(root / "l.npz", **labels)
            core.sealed_select(root / "c.npz", root / "run")
            with (root / "run/selection.npz").open("ab") as stream:
                stream.write(b"tamper")
            with patch.object(
                core.np, "load", side_effect=AssertionError("payload opened")
            ):
                with self.assertRaisesRegex(ValueError, "selection seal"):
                    core.sealed_evaluate(root / "c.npz", root / "l.npz", root / "run")

    def test_complete_replay_and_no_overwrite(self):
        c, labels = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            np.savez(root / "c.npz", **c)
            np.savez(root / "l.npz", **labels)
            core.sealed_select(root / "c.npz", root / "run")
            with self.assertRaisesRegex(ValueError, "output must be empty"):
                core.sealed_select(root / "c.npz", root / "run")
            summary = core.sealed_evaluate(root / "c.npz", root / "l.npz", root / "run")
            self.assertEqual(summary["users"], 3)
            self.assertTrue((root / "run/evaluation-seal.json").exists())


if __name__ == "__main__":
    unittest.main()
