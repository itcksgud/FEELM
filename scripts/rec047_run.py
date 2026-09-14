"""Fixed recipes, shared validation-selected blend, then sealed final predictions."""

# ruff: noqa: E402
from __future__ import annotations
import os

for name in ["OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"]:
    os.environ[name] = "1"
import argparse
import json
import subprocess
import time
import numpy as np
import pandas as pd
from rec047_common import (
    ROOT,
    DOC,
    OUT,
    H,
    KS,
    LEVELS,
    METHODS,
    config,
    reviewed,
    fingerprint,
    stage_seal,
    require,
    pin,
    write_json,
    als_predict,
    check_prediction,
)
from rec047_prepare import raw_rows


def validate_seal(path, base, stage):
    seal = json.loads(path.read_text(encoding="utf-8"))
    require(seal["fingerprint"] == fingerprint(), "model seal version")
    require(
        seal["prepared_seal"] == pin(OUT / stage / "prepared-seal.json"),
        "model prepared parent",
    )
    if stage == "evaluation":
        require(
            seal["selection"] == pin(OUT / "selection.json"), "model selection parent"
        )
    for p, v in seal["files"].items():
        require(pin(base / p) == v, "model file changed " + p)
    return seal


def predictions(stage, level, method):
    folder = OUT / stage
    target = folder / "models" / f"p{level}" / method
    info = json.loads((folder / "feature-info.json").read_text())
    if method != "ALS":
        p = pd.read_parquet(target / "predictions").sort_values("row_id")
        require(
            np.array_equal(p.row_id, np.arange(info["score_rows"])),
            "prediction row IDs",
        )
        values = p.prediction.to_numpy(float)
        check_prediction(values, info["score_rows"])
        return values, None
    contexts = json.loads((folder / "contexts.json").read_text())
    cat = np.load(folder / "catalog.npz", allow_pickle=False)
    ids = cat["movie_ids"]
    counts = cat["counts"][LEVELS.index(level)]
    factors = pd.read_parquet(target / "item-factors")
    require(
        factors.id.is_unique and np.array_equal(np.sort(factors.id), ids[counts > 0]),
        "factor/support catalog identity",
    )
    rank = config()["models"]["ALS"]["rank"]
    vec = np.full((len(ids), rank), np.nan)
    values = np.vstack(factors.features)
    require(
        values.shape == (len(factors), rank) and np.isfinite(values).all(),
        "ALS factors",
    )
    vec[np.searchsorted(ids, factors.id)] = values
    pred = np.zeros(info["score_rows"])
    direct = np.zeros(info["score_rows"], bool)
    for c in contexts:
        p, d, _ = als_predict(
            vec,
            ids,
            c["oi"],
            c["stars"],
            c["ei"],
            config()["models"]["ALS"]["regParam"],
            info["levels"][str(level)]["global_mean"],
        )
        pred[c["start"] : c["stop"]] = p
        direct[c["start"] : c["stop"]] = d
    check_prediction(pred, info["score_rows"], direct)
    return pred, direct


def fit(stage):
    reviewed()
    stage_seal(stage)
    cfg = config()
    folder = OUT / stage
    if (folder / "fit-seal.json").exists():
        validate_seal(folder / "fit-seal.json", folder, stage)
        return
    if stage == "evaluation":
        validate_selection()
    require(
        subprocess.check_output(
            ["docker", "image", "inspect", cfg["docker_image"], "--format", "{{.Id}}"],
            text=True,
        ).strip()
        == cfg["image_id"],
        "runtime image identity",
    )
    logs = folder / "logs"
    logs.mkdir(exist_ok=True)
    for level in LEVELS:
        for method in cfg["models"]:
            target = folder / "models" / f"p{level}" / method
            if (target / "model-seal.json").exists():
                validate_seal(target / "model-seal.json", target, stage)
                continue
            require(not target.exists(), "unfinished fit preserved " + str(target))
            name = f"rec047-{stage}-p{level}-{method.lower()}"
            command = [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "--network",
                "none",
                "--hostname",
                "rec047",
                "--add-host",
                "rec047:127.0.0.1",
                "-e",
                "SPARK_LOCAL_IP=127.0.0.1",
                "--cpus",
                "4",
                "--memory",
                "12g",
                "--memory-swap",
                "12g",
                "--mount",
                f"type=bind,source={ROOT / 'scripts'},target=/scripts,readonly",
                "--mount",
                f"type=bind,source={folder},target=/data",
                "--mount",
                f"type=bind,source={DOC},target=/config,readonly",
                cfg["docker_image"],
                "/opt/spark/bin/spark-submit",
                "--master",
                "local[4]",
                "--driver-memory",
                "8g",
                "--conf",
                "spark.sql.shuffle.partitions=8",
                "--conf",
                "spark.ui.enabled=false",
                "/scripts/rec047_worker.py",
                str(level),
                method,
            ]
            print("START " + name, flush=True)
            t = time.monotonic()
            with (logs / (name + ".log")).open("w", encoding="utf-8") as log:
                proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                try:
                    code = proc.wait(timeout=cfg["fit_timeout_seconds"])
                    require(code == 0, "fit failed " + name)
                except BaseException:
                    subprocess.run(
                        ["docker", "stop", "--time", "2", name],
                        capture_output=True,
                        timeout=20,
                    )
                    if proc.poll() is None:
                        proc.kill()
                    write_json(
                        folder / (name + "-failure.json"),
                        {
                            "name": name,
                            "seconds": time.monotonic() - t,
                            "process_returncode": proc.poll(),
                            "fingerprint": fingerprint(),
                            "claim": "resource/runtime failure is not inferior predictive performance",
                        },
                    )
                    raise
            p, d = predictions(stage, level, method)
            np.savez_compressed(
                target / "aligned-predictions.npz",
                predictions=p,
                direct=d if d is not None else np.zeros(0, bool),
            )
            write_json(
                target / "model-seal.json",
                {
                    "fingerprint": fingerprint(),
                    "prepared_seal": pin(folder / "prepared-seal.json"),
                    "selection": pin(OUT / "selection.json")
                    if stage == "evaluation"
                    else None,
                    "files": {
                        p.relative_to(target).as_posix(): pin(p)
                        for p in target.rglob("*")
                        if p.is_file()
                    },
                    "target_stars_decoded": 0,
                },
            )
            metrics = json.loads((target / "metrics.json").read_text())
            print(
                f"DONE {name}: {metrics['training_rows']} rows, {metrics['fit_seconds']:.1f}s fit, {metrics.get('cgroup_peak_bytes', 0) / 1024**3:.2f}GiB peak",
                flush=True,
            )
    stage_seal(stage)
    for level in LEVELS:
        for method in cfg["models"]:
            target = folder / "models" / f"p{level}" / method
            validate_seal(target / "model-seal.json", target, stage)
    combined_files = {}
    if stage == "evaluation":
        selection = validate_selection()
        all_predictions, all_direct = [], []
        n = json.loads((folder / "feature-info.json").read_text())["score_rows"]
        for level in LEVELS:
            values = {
                m: np.load(
                    folder / "models" / f"p{level}" / m / "aligned-predictions.npz"
                )["predictions"]
                for m in cfg["models"]
            }
            direct = np.load(
                folder / "models" / f"p{level}" / "ALS/aligned-predictions.npz"
            )["direct"]
            w = selection["common_als_weight"]
            values["ALS_FM"] = np.where(
                direct, w * values["ALS"] + (1 - w) * values["FM"], values["FM"]
            )
            for vector in values.values():
                check_prediction(vector, n, direct)
            all_predictions.append(np.column_stack([values[m] for m in METHODS]))
            all_direct.append(direct)
        combined = folder / "final-predictions.npz"
        require(not combined.exists(), "preserve combined predictions")
        np.savez_compressed(
            combined,
            predictions=np.asarray(all_predictions),
            direct=np.asarray(all_direct),
            levels=LEVELS,
            methods=METHODS,
        )
        combined_files[combined.name] = pin(combined)
    write_json(
        folder / "fit-seal.json",
        {
            "fingerprint": fingerprint(),
            "prepared_seal": pin(folder / "prepared-seal.json"),
            "selection": pin(OUT / "selection.json") if stage == "evaluation" else None,
            "files": {
                **combined_files,
                **{
                    p.relative_to(folder).as_posix(): pin(p)
                    for p in (folder / "models").rglob("*")
                    if p.is_file()
                },
            },
            "target_stars_decoded": 0,
        },
    )


def load_labels(stage):
    folder = OUT / stage
    validate_seal(folder / "fit-seal.json", folder, stage)
    stage_seal(stage)
    require(not (folder / "labels.parquet").exists(), "preserve opened labels")
    contexts = json.loads((folder / "contexts.json").read_text())
    ids = np.load(folder / "catalog.npz")["movie_ids"]
    pairs = {(c["uid"], int(ids[i])) for c in contexts for i in c["ei"]}
    users = {u for u, m in pairs}
    origin = config()["origins"][stage]
    ratings = {}
    for u, m, t, rb in raw_rows(users, set(map(int, ids))):
        if origin <= t < origin + H and (u, m) in pairs:
            value = float(rb)
            require(value * 2 in range(1, 11), "target half stars")
            require((u, m) not in ratings, "unique target")
            ratings[(u, m)] = value
    require(len(ratings) == len(pairs), "complete target labels")
    pd.DataFrame(
        [(u, m, v) for (u, m), v in sorted(ratings.items())],
        columns=["uid", "movie_id", "rating"],
    ).to_parquet(folder / "labels.parquet", index=False)
    return ratings


def validate_selection():
    r = json.loads((OUT / "selection.json").read_text())
    require(r["fingerprint"] == fingerprint(), "selection version")
    require(
        r["validation_fit_seal"] == pin(OUT / "validation/fit-seal.json"),
        "selection fit seal",
    )
    require(
        r["validation_labels"] == pin(OUT / "validation/labels.parquet"),
        "selection labels seal",
    )
    return r


def select():
    reviewed()
    folder = OUT / "validation"
    require(not (OUT / "selection.json").exists(), "preserve selection")
    labels = load_labels("validation")
    ids = np.load(folder / "catalog.npz")["movie_ids"]
    contexts = json.loads((folder / "contexts.json").read_text())
    losses = []
    for weight in config()["blend_als_weights"]:
        by_level = []
        for level in LEVELS:
            a = np.load(folder / "models" / f"p{level}" / "ALS/aligned-predictions.npz")
            f = np.load(folder / "models" / f"p{level}" / "FM/aligned-predictions.npz")[
                "predictions"
            ]
            p = np.where(a["direct"], weight * a["predictions"] + (1 - weight) * f, f)
            check_prediction(a["predictions"], len(p), a["direct"])
            check_prediction(f, len(p))
            check_prediction(p, len(p))
            by_k = []
            for k in KS:
                values = []
                for c in contexts:
                    if c["k"] != k:
                        continue
                    y = np.asarray([labels[(c["uid"], int(ids[i]))] for i in c["ei"]])
                    values.append(
                        float(
                            np.mean(
                                (np.clip(p[c["start"] : c["stop"]], 0.5, 5) - y) ** 2
                            )
                        )
                    )
                require(bool(values), "validation K represented")
                by_k.append(float(np.mean(values)))
            by_level.append(float(np.mean(by_k)))
        losses.append(
            {
                "als_weight": weight,
                "equal_level_equal_k_user_macro_mse": float(np.mean(by_level)),
                "level_mse": by_level,
            }
        )
    best = min(
        losses, key=lambda a: (a["equal_level_equal_k_user_macro_mse"], a["als_weight"])
    )
    write_json(
        OUT / "selection.json",
        {
            "fingerprint": fingerprint(),
            "common_als_weight": best["als_weight"],
            "losses": losses,
            "validation_fit_seal": pin(folder / "fit-seal.json"),
            "validation_labels": pin(folder / "labels.parquet"),
            "evaluation_target_stars_decoded": 0,
        },
    )
    print(json.dumps(best), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["fit", "select"])
    p.add_argument("stage", nargs="?", choices=["validation", "evaluation"])
    a = p.parse_args()
    if a.action == "fit":
        require(a.stage is not None, "stage required")
        fit(a.stage)
    else:
        select()
