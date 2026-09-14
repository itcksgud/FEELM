"""Read-only replay audit for completed REC044 artifacts; no model refit."""

# ruff: noqa: E402
import os

for variable in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ[variable] = "1"

import json

import numpy as np

import feelm_preference_structure as pins
import rec_ev_044_residual_taste as runner


def main():
    config = runner.adapter.read_json(runner.DOC / "config.json")
    out = runner.ROOT / config["output_root"]
    runner.verify_sources(config)
    runner.verify_prediction(out)
    completion = runner.adapter.read_json(out / "completion-seal.json")
    for name, digest in completion["outputs"].items():
        pins.require(pins.pin(out / name) == digest, "completion artifact: " + name)
    pins.require(completion["fingerprint"] == runner.fingerprint(), "completion code")
    data = pins.load_npz(out / "input.npz")
    pred = pins.load_npz(out / "predictions.npz")
    labels = pins.load_npz(out / "labels.npz")
    saved = pins.load_npz(out / "evaluation.npz")
    summary = runner.adapter.read_json(out / "summary.json")
    for key in ("user_keys", "e_index", "e_offsets"):
        np.testing.assert_array_equal(data[key], labels[key])
    start = data["e_offsets"][:-1]
    count = np.diff(data["e_offsets"])
    estimates = np.column_stack((pred["baseline"], pred["own"]))
    error = estimates - labels["rating_raw"][:, None]
    mse = np.add.reduceat(error**2, start, axis=0) / count[:, None]
    mae = np.add.reduceat(np.abs(error), start, axis=0) / count[:, None]
    np.testing.assert_allclose(mse, saved["mse"], rtol=0, atol=1e-12)
    np.testing.assert_allclose(mae, saved["mae"], rtol=0, atol=1e-12)
    # Explicit independent contrast matrix: baseline minus five probes,
    # genres/keywords minus combined, rule/km minus genres.
    pairs = [(0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (3, 5), (4, 5), (1, 3), (2, 3)]
    delta = np.array([mse[:, left] - mse[:, right] for left, right in pairs]).T
    delta[np.abs(delta) <= 1e-12] = 0
    np.testing.assert_allclose(delta, saved["delta"], rtol=0, atol=1e-12)
    # Resampling frequency weights rather than averaging an indexed delta tensor.
    rng = np.random.Generator(np.random.PCG64(20260910))
    draws = np.empty((20000, 9))
    for first in range(0, len(draws), 128):
        size = min(128, len(draws) - first)
        ids = rng.integers(len(count), size=(size, len(count)), dtype=np.int64)
        frequency = np.stack([np.bincount(row, minlength=len(count)) for row in ids])
        draws[first : first + size] = frequency @ delta / len(count)
    np.testing.assert_allclose(draws, saved["bootstrap_means"], rtol=0, atol=1e-12)
    intervals = np.quantile(
        draws, [0.05 / 18, 1 - 0.05 / 18], axis=0, method="linear"
    ).T
    for j, report in enumerate(summary["comparisons"]):
        np.testing.assert_allclose(intervals[j], report["interval"], rtol=0, atol=1e-12)
        direction = (
            "IMPROVED"
            if intervals[j, 0] > 0
            else "WORSENED"
            if intervals[j, 1] < 0
            else "UNDECIDED"
        )
        pins.require(direction == report["direction"], "comparison direction")
    for state, report in enumerate(summary["keyword_strata_descriptive"]):
        mask = pred["keyword_state"] == state
        user_n = np.add.reduceat(mask.astype(np.int64), start)
        eligible = user_n > 0
        sums = np.add.reduceat(error[:, [0, 4, 5]] ** 2 * mask[:, None], start, axis=0)
        aggregate = (sums[eligible] / user_n[eligible, None]).mean(axis=0)
        pins.require(
            report["pairs"] == int(mask.sum())
            and report["users"] == int(eligible.sum()),
            "keyword strata counts",
        )
        np.testing.assert_allclose(
            aggregate, report["mse_BASE_KEYWORDS_COMBINED"], rtol=0, atol=1e-12
        )
    no_signal = pred["keyword_state"] != 2
    np.testing.assert_array_equal(
        pred["own"][no_signal, 3], pred["baseline"][no_signal]
    )
    print(
        json.dumps(
            {
                "status": "PASS_READ_ONLY_REPLAY",
                "users": len(count),
                "E_pairs": len(error),
                "max_mse_error": float(np.abs(mse - saved["mse"]).max()),
                "max_bootstrap_error": float(
                    np.abs(draws - saved["bootstrap_means"]).max()
                ),
                "summary_sha256": pins.pin(out / "summary.json")["sha256"],
                "model_refit": False,
                "files_written": False,
            }
        )
    )


if __name__ == "__main__":
    main()
