"""Sequential isolated Spark fits, validation-only selection, then prediction seal."""

# ruff: noqa: E402 -- fix BLAS threads before importing NumPy.
from __future__ import annotations
import os

for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[name] = "1"
import argparse
import json
import subprocess
import time

import numpy as np
import pandas as pd

from rec046_common import (
    ROOT,
    DOC,
    OUT,
    require,
    pin,
    write_json,
    reviewed,
    fingerprint,
    als_predict,
    verify_prepared,
)
from rec046_prepare import raw_rows

METHODS = ["ALS", "FM", "ALS_FM", "RIDGE", "GBT"]


def model_predictions(folder, method, index, contexts, ids):
    target = folder / "models" / f"{method}-{index}"
    n = max(c["stop"] for c in contexts)
    if method != "ALS":
        table = pd.read_parquet(target / "predictions").sort_values("row_id")
        require(
            np.array_equal(table.row_id.to_numpy(), np.arange(n)),
            "prediction IDs missing/duplicate",
        )
        values = table.prediction.to_numpy(dtype=float)
        require(np.isfinite(values).all(), "nonfinite model prediction")
        return values, None
    info = json.loads((folder / "feature-info.json").read_text())
    cfg = json.loads((DOC / "config.json").read_text())
    table = pd.read_parquet(target / "item-factors")
    require(table.id.is_unique, "duplicate ALS factors")
    rank = cfg["models"]["ALS"][index]["rank"]
    counts = np.load(folder / "train-counts.npy", allow_pickle=False)
    require(
        np.array_equal(np.sort(table.id.to_numpy()), ids[counts > 0]),
        "ALS factor IDs must equal observed training movies",
    )
    exported = np.vstack(table.features)
    require(
        exported.shape == (len(table), rank) and np.isfinite(exported).all(),
        "invalid exported ALS factor values or shape",
    )
    factors = np.full((len(ids), rank), np.nan)
    idx = np.searchsorted(ids, table.id.to_numpy())
    require(np.array_equal(ids[idx], table.id.to_numpy()), "factor catalog axis")
    factors[idx] = exported
    values = np.zeros(n)
    support = np.zeros(n, dtype=bool)
    for c in contexts:
        p, s, _ = als_predict(
            factors,
            ids,
            c["oi"],
            c["stars"],
            c["ei"],
            cfg["models"]["ALS"][index]["regParam"],
            info["global_mean"],
        )
        values[c["start"] : c["stop"]] = p
        support[c["start"] : c["stop"]] = s
    return values, support


def load_labels(role, all_contexts, ids):
    cfg = json.loads((DOC / "config.json").read_text())
    pairs = {
        (c["uid"], int(ids[i]))
        for contexts in all_contexts
        for c in contexts
        if c["role"] == role
        for i in c["ei"]
    }
    users = {u for u, _ in pairs}
    lookup = set(map(int, ids))
    ratings = {}
    for u, mid, stamp, rb in raw_rows(users, lookup):
        if stamp >= cfg["cutoff"] and (u, mid) in pairs:
            v = float(rb)
            require(v in np.arange(1, 11) / 2, "target half stars")
            ratings[(u, mid)] = v
    require(len(ratings) == len(pairs), "target lookup missing")
    pd.DataFrame(
        [(u, m, v) for (u, m), v in sorted(ratings.items())],
        columns=["uid", "movie_id", "rating"],
    ).to_parquet(OUT / f"{role}-labels.parquet", index=False)
    return ratings


def validation_loss(p, contexts, labels, ids):
    losses = []
    for c in contexts:
        if c["role"] != "validation":
            continue
        y = np.array([labels[(c["uid"], int(ids[i]))] for i in c["ei"]])
        losses.append(np.mean((np.clip(p[c["start"] : c["stop"]], 0.5, 5) - y) ** 2))
    require(bool(losses), "empty validation")
    return float(np.mean(losses))


def fit_all():
    reviewed()
    cfg = json.loads((DOC / "config.json").read_text())
    verify_prepared()
    image = subprocess.check_output(
        ["docker", "image", "inspect", cfg["docker_image"], "--format", "{{.Id}}"],
        text=True,
    ).strip()
    require(image == cfg["image_id"], "runtime image changed")
    logs = OUT / "logs"
    logs.mkdir(exist_ok=True)
    for r in range(cfg["rounds"]):
        for method, variants in cfg["models"].items():
            for index in range(len(variants)):
                name = f"rec046-r{r}-{method.lower()}-{index}"
                target = OUT / f"r{r}/models/{method}-{index}"
                require(not target.exists(), "preserve existing fit " + name)
                command = [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--hostname",
                    "rec046",
                    "--add-host",
                    "rec046:127.0.0.1",
                    "-e",
                    "SPARK_LOCAL_IP=127.0.0.1",
                    "--cpus",
                    "4",
                    "--memory",
                    "10g",
                    "--memory-swap",
                    "10g",
                    "--mount",
                    f"type=bind,source={ROOT / 'scripts'},target=/scripts,readonly",
                    "--mount",
                    f"type=bind,source={OUT},target=/data",
                    "--mount",
                    f"type=bind,source={DOC},target=/config,readonly",
                    cfg["docker_image"],
                    "/opt/spark/bin/spark-submit",
                    "--master",
                    "local[4]",
                    "--driver-memory",
                    "6g",
                    "--conf",
                    "spark.sql.shuffle.partitions=8",
                    "--conf",
                    "spark.ui.enabled=false",
                    "/scripts/rec046_worker.py",
                    str(r),
                    method,
                    str(index),
                ]
                print("START " + name, flush=True)
                started = time.monotonic()
                with (logs / (name + ".log")).open("w", encoding="utf-8") as log:
                    process = subprocess.Popen(
                        command, stdout=log, stderr=subprocess.STDOUT
                    )
                    try:
                        code = process.wait(timeout=2700)
                        require(code == 0, "fit failed: " + name)
                    except BaseException:
                        subprocess.run(
                            ["docker", "stop", "--time", "2", name],
                            capture_output=True,
                            timeout=20,
                        )
                        if process.poll() is None:
                            process.kill()
                        write_json(
                            OUT / "failure.json",
                            {
                                "model": name,
                                "seconds": time.monotonic() - started,
                                "fingerprint": fingerprint(),
                            },
                        )
                        raise
                metrics = json.loads((target / "metrics.json").read_text())
                print(f"DONE {name}: {metrics['fit_seconds']:.2f}s fit", flush=True)
    # ALS workers export item factors; fold-in must also precede validation labels.
    ids = np.load(OUT / "catalog.npz", allow_pickle=False)["movie_ids"]
    for r in range(cfg["rounds"]):
        folder = OUT / f"r{r}"
        contexts = json.loads((folder / "contexts.json").read_text())
        values, names, support = [], [], []
        for method, variants in cfg["models"].items():
            for index in range(len(variants)):
                prediction, direct = model_predictions(
                    folder, method, index, contexts, ids
                )
                values.append(prediction)
                names.append(f"{method}-{index}")
                if method == "ALS":
                    support.append(direct)
        np.savez_compressed(
            folder / "models/all-predictions.npz",
            predictions=np.column_stack(values),
            methods=np.array(names),
            als_support=np.column_stack(support),
        )
    write_json(
        OUT / "all-fit-seal.json",
        {
            "fingerprint": fingerprint(),
            "prepared_seal": pin(OUT / "prepared-seal.json"),
            "files": {
                p.relative_to(OUT).as_posix(): pin(p)
                for p in OUT.glob("r*/models/**/*")
                if p.is_file()
            },
            "validation_target_stars_decoded": 0,
            "evaluation_target_stars_decoded": 0,
        },
    )


def select():
    reviewed()
    verify_prepared()
    cfg = json.loads((DOC / "config.json").read_text())
    seal = json.loads((OUT / "all-fit-seal.json").read_text())
    require(seal["fingerprint"] == fingerprint(), "fit seal version")
    require(
        seal["prepared_seal"] == pin(OUT / "prepared-seal.json"),
        "fit prepared seal changed",
    )
    for name, expected in seal["files"].items():
        require(pin(OUT / name) == expected, "model output changed " + name)
    ids = np.load(OUT / "catalog.npz", allow_pickle=False)["movie_ids"]
    all_contexts = [
        json.loads((OUT / f"r{r}/contexts.json").read_text())
        for r in range(cfg["rounds"])
    ]
    labels = load_labels("validation", all_contexts, ids)
    selections = []
    for r, contexts in enumerate(all_contexts):
        folder = OUT / f"r{r}"
        cache = np.load(folder / "models/all-predictions.npz", allow_pickle=False)
        names = list(cache["methods"])
        chosen = {}
        losses = {}
        pred = {}
        direct = None
        for method, variants in cfg["models"].items():
            candidates = [
                (
                    cache["predictions"][:, names.index(f"{method}-{i}")],
                    cache["als_support"][:, i] if method == "ALS" else None,
                )
                for i in range(len(variants))
            ]
            loss = [validation_loss(p, contexts, labels, ids) for p, _ in candidates]
            best = int(np.argmin(loss))
            chosen[method] = best
            losses[method] = loss
            pred[method] = candidates[best][0]
            if method == "ALS":
                direct = candidates[best][1]
        alphas = [0, 0.25, 0.5, 0.75, 1]
        fusion = [
            np.where(direct, (1 - a) * pred["FM"] + a * pred["ALS"], pred["FM"])
            for a in alphas
        ]
        loss = [validation_loss(p, contexts, labels, ids) for p in fusion]
        best = int(np.argmin(loss))
        pred["ALS_FM"] = fusion[best]
        np.savez_compressed(
            folder / "selected.npz",
            predictions=np.column_stack([pred[m] for m in METHODS]),
            direct_als=direct,
            methods=np.array(METHODS),
        )
        selections.append(
            {
                "round": r,
                "chosen": chosen,
                "validation_mse": losses,
                "hybrid_alpha": alphas[best],
                "hybrid_validation_mse": loss,
            }
        )
    write_json(OUT / "selection.json", selections)
    write_json(
        OUT / "prediction-seal.json",
        {
            "fingerprint": fingerprint(),
            "prepared_seal": pin(OUT / "prepared-seal.json"),
            "all_fit_seal": pin(OUT / "all-fit-seal.json"),
            "evaluation_target_stars_decoded": 0,
            "files": {
                p.relative_to(OUT).as_posix(): pin(p)
                for p in [
                    OUT / "selection.json",
                    OUT / "validation-labels.parquet",
                    *(OUT / f"r{r}/selected.npz" for r in range(cfg["rounds"])),
                    *(OUT / f"r{r}/contexts.json" for r in range(cfg["rounds"])),
                ]
            },
        },
    )
    print(
        "Selection fixed using validation only; evaluation target stars still closed",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["fit", "select"])
    args = parser.parse_args()
    (fit_all if args.stage == "fit" else select)()
