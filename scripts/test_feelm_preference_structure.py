"""Synthetic mathematical and label-boundary tests; no research files are read."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import feelm_preference_structure as core
import numpy as np


def fixture():
    size, n = 36, 3
    ids = np.arange(1, size + 1)
    membership = np.zeros((size, 34), dtype=np.uint8)
    membership[np.arange(size), ids % 2] = 1
    membership[np.arange(size), 8 + ids % 2] = 1
    membership[np.arange(size), 16 + ids % 2] = 1
    membership[:, 18] = 1
    stars = np.where(ids % 2, 4.5, 1.5)
    d = dict(
        user_keys=np.array(["a", "b", "c"]),
        movie_ids=ids,
        membership=membership,
        bayes=np.full(size, 3.0),
        training_counts=np.full(size, 1000),
        o_index=np.tile(np.arange(30), (n, 1)),
        o_ratings=np.tile(stars[:30], (n, 1)),
        e_index=np.tile(np.arange(30, size), n),
        e_offsets=np.arange(n + 1) * 6,
    )
    labels = {key: d[key].copy() for key in ("user_keys", "e_index", "e_offsets")}
    labels["rating_raw"] = np.tile(stars[30:], n)
    return d, labels


class ProbeTests(unittest.TestCase):
    def test_nonrepresentable_mean_null_is_not_evidence(self):
        d, labels = fixture()
        d["membership"][:] = 0
        d["membership"][:, [0, 8, 16]] = 1
        d["o_ratings"][:, :8] = 0.5
        d["o_ratings"][:, 8:] = 3.5
        labels["rating_raw"][:] = 0.5
        fitted = core.fit(d)
        np.testing.assert_array_equal(fitted["effect"], 0)
        summary, detail = core.evaluate(d, fitted, labels)
        np.testing.assert_array_equal(detail["primary_delta"], 0)
        self.assertTrue(all(m["direction"] == "UNDECIDED" for m in summary["methods"]))

    def test_fixed_shrinkage_and_fractional_movie_mass(self):
        d, _ = fixture()
        f = core.fit(d)
        np.testing.assert_allclose(f["effect"][0, 1, :2], [-1.125, 1.125], atol=1e-14)
        np.testing.assert_allclose(f["effect"][0, 1, 16:19], [-0.9, 0.9, 0], atol=1e-14)
        for part in core.SLICES:
            np.testing.assert_allclose(
                core.weights(d["membership"], d["o_index"][0], part).sum(axis=1), 1
            )
        np.testing.assert_allclose(f["own"][:6, 1, 0], [4.125, 1.875] * 3)

    def test_unseen_category_has_zero_effect(self):
        d, _ = fixture()
        d["membership"][30, :8] = 0
        d["membership"][30, 2] = 1
        f = core.fit(d)
        self.assertEqual(f["effect"][0, 1, 2], 0)
        self.assertEqual(f["own"][0, 1, 0], f["baseline"][0, 1])

    def test_movie_bias_and_user_shift_removed(self):
        d, _ = fixture()
        d["bayes"] = np.where(d["movie_ids"] % 2, 4.0, 1.0)
        f = core.fit(d)
        np.testing.assert_allclose(f["effect"][:, 1], 0, atol=1e-14)
        np.testing.assert_allclose(f["centers"][:, 1], 0.5)

    def test_invalid_O_and_overlap_fail(self):
        d, _ = fixture()
        d["o_ratings"][0, 0] = 0.7
        with self.assertRaisesRegex(ValueError, "O30"):
            core.fit(d)
        d, _ = fixture()
        d["e_index"][0] = 0
        with self.assertRaisesRegex(ValueError, "overlap"):
            core.fit(d)

    def test_donor_is_deterministic_derangement(self):
        keys = np.array(["a", "b", "c"])
        donor = core.donor_mapping(keys)
        self.assertEqual(sorted(donor.tolist()), [0, 1, 2])
        self.assertTrue((donor != np.arange(3)).all())
        np.testing.assert_array_equal(donor, core.donor_mapping(keys))

    def test_positive_negative_and_zero_gain(self):
        d, labels = fixture()
        f = core.fit(d)
        s, v = core.evaluate(d, f, labels)
        np.testing.assert_allclose(v["primary_delta"][:, 0], 2.109375)
        self.assertTrue(all(m["direction"] == "IMPROVED" for m in s["methods"]))
        labels["rating_raw"] = 6 - labels["rating_raw"]
        s, _ = core.evaluate(d, f, labels)
        self.assertTrue(all(m["direction"] == "WORSENED" for m in s["methods"]))
        d, labels = fixture()
        d["o_ratings"][:] = 3
        labels["rating_raw"][:] = 3
        s, _ = core.evaluate(d, core.fit(d), labels)
        self.assertTrue(all(m["direction"] == "UNDECIDED" for m in s["methods"]))

    def test_label_mismatch_and_bad_star(self):
        d, labels = fixture()
        f = core.fit(d)
        labels["user_keys"][0] = "z"
        with self.assertRaisesRegex(ValueError, "label axis"):
            core.evaluate(d, f, labels)
        d, labels = fixture()
        labels["rating_raw"][0] = 3.2
        with self.assertRaisesRegex(ValueError, "half-star"):
            core.evaluate(d, f, labels)

    def test_all_partial_unseen_strata(self):
        d, labels = fixture()
        d["membership"][30, 16:] = 0
        d["membership"][30, 19] = 1
        d["membership"][31, 19] = 1
        s, _ = core.evaluate(d, core.fit(d), labels)
        layers = s["methods"][2]["seen_conditioned"]
        self.assertEqual([x["pairs"] for x in layers], [3, 3, 12])
        self.assertEqual(layers[0]["gain"], 0)
        self.assertEqual(s["descriptive"][2]["E_no_group_seen_in_O"], 3)

    def test_descriptive_mass_mix_and_ties(self):
        d, labels = fixture()
        s = core.describe(d, labels["rating_raw"])
        for m in s:
            self.assertAlmostEqual(
                sum(sum(g["OE_rating_mass"]) for g in m["groups"]), 108.0
            )
            self.assertEqual(sum(m["O_represented_group_users"]), 3)
            self.assertEqual(
                m["most_evaluated_vs_highest_rated"]["nonunique_maximum_users"],
                3 if m["method"] != "TMDB_GENRES" else 0,
            )
        # Common genre contains both low and high ratings for all three people.
        self.assertEqual(s[2]["groups"][2]["mixed_users"], 3)

    def test_shared_bootstrap_stream_and_budget(self):
        d = np.arange(9, dtype=float).reshape(3, 3)
        draws, ci = core.bootstrap(d)
        rng = np.random.Generator(np.random.PCG64(20260909))
        expected = d[rng.integers(3, size=(20000, 3), dtype=np.int64)].mean(axis=1)
        np.testing.assert_array_equal(draws, expected)
        np.testing.assert_array_equal(
            ci, np.quantile(expected, [1 / 120, 119 / 120], axis=0, method="linear").T
        )
        with self.assertRaisesRegex(RuntimeError, "budget"):
            core.bootstrap(
                d, guard=lambda: (_ for _ in ()).throw(RuntimeError("budget"))
            )

    def test_seal_tamper_blocks_decoder_and_partial_preserved(self):
        d, labels = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            np.savez(root / "input.npz", **d)
            np.savez(root / "labels.npz", **labels)
            core.sealed_fit(root / "input.npz", root / "run")
            with self.assertRaisesRegex(ValueError, "empty"):
                core.sealed_fit(root / "input.npz", root / "run")
            with (root / "run/predictions.npz").open("ab") as stream:
                stream.write(b"tamper")
            with patch.object(core.np, "load", side_effect=AssertionError("decoded")):
                with self.assertRaisesRegex(ValueError, "seal mismatch"):
                    core.sealed_evaluate(
                        root / "input.npz", root / "labels.npz", root / "run"
                    )


if __name__ == "__main__":
    unittest.main()
