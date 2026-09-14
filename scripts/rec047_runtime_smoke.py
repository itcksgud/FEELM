"""Pure synthetic Spark API/parity/resource smoke; no source ratings are read."""

from __future__ import annotations
import json
import subprocess
import time
import numpy as np
import pandas as pd
from rec047_common import ROOT, config, pin, write_json
from rec047_features import Relations, GENRES


def main():
    out = ROOT / "outputs/recommendation-evidence/rec-ev-047-runtime"
    assert not out.exists(), "preserve runtime evidence"
    out.mkdir(parents=True)
    data = out / "data"
    data.mkdir()
    conf = out / "config"
    conf.mkdir()
    cfg = config()
    toy = {
        "models": {m: {**p, "maxIter": 2} for m, p in cfg["models"].items()},
        "seed": 47,
    }
    write_json(conf / "config.json", toy)
    frame = pd.DataFrame(
        [
            dict(
                movie_id=i,
                genre_ids=[GENRES[i % 18]],
                keyword_ids=[i % 5],
                production_country_codes=["KR" if i % 2 else "US"],
                original_language="ko" if i % 2 else "en",
                release_year=1950 + i,
                runtime_minutes=75 + i,
                director_ids=[i % 12],
                top5_cast_ids=[i % 16, (i + 3) % 16],
                production_company_ids=[i % 5],
                collection_ids=[],
            )
            for i in range(64)
        ]
    )
    rel = Relations(frame)
    xs = []
    ys = []
    ratings = []
    for uid in range(1, 81):
        for j in range(4):
            oi = (uid * 3 + j) % 64
            ei = (oi + 1 + uid % 7) % 64
            star = 0.5 * ((uid + j) % 10 + 1)
            label = 0.5 * ((uid + j + 3) % 10 + 1)
            xs.append(rel.transform([oi], [star], [ei]).toarray()[0])
            ys.append(label)
            ratings.append((uid, ei, label))
    x = np.vstack(xs)
    table = pd.DataFrame(x, columns=[f"x{i:03d}" for i in range(x.shape[1])])
    table["row_id"] = np.arange(len(table))
    table["label"] = ys
    table["level"] = 25
    table.to_parquet(data / "train.parquet", index=False)
    table.iloc[:24].to_parquet(data / "score.parquet", index=False)
    a = pd.DataFrame(ratings, columns=["uid", "movie_id", "rating"])
    a["level"] = 25
    a.to_parquet(data / "ratings.parquet", index=False)
    write_json(
        data / "feature-info.json",
        {
            "stage": "synthetic",
            "dimensions": x.shape[1],
            "levels": {"25": {"ratings": len(table)}},
        },
    )
    result = []
    for method in toy["models"]:
        name = "rec047-synthetic-" + method.lower()
        started = time.monotonic()
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
            f"type=bind,source={data},target=/data",
            "--mount",
            f"type=bind,source={conf},target=/config,readonly",
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
            "25",
            method,
        ]
        print("SYNTHETIC START " + method, flush=True)
        with (out / (method + ".log")).open("w", encoding="utf-8") as log:
            p = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            try:
                code = p.wait(timeout=300)
                assert code == 0, method + " failed; preserve log"
            except BaseException:
                subprocess.run(
                    ["docker", "stop", "--time", "2", name],
                    capture_output=True,
                    timeout=20,
                )
                if p.poll() is None:
                    p.kill()
                raise
        metrics = json.loads(
            (data / "models/p25" / method / "metrics.json").read_text()
        )
        result.append(metrics)
        print(f"SYNTHETIC PASS {method} {time.monotonic() - started:.1f}s", flush=True)
    write_json(
        out / "completion.json",
        {
            "status": "PASS_SYNTHETIC_RUNTIME",
            "source_rating_values_decoded": 0,
            "worker": pin(ROOT / "scripts/rec047_worker.py"),
            "runtime_script": pin(ROOT / "scripts/rec047_runtime_smoke.py"),
            "toy_iterations": 2,
            "models": result,
        },
    )


if __name__ == "__main__":
    main()
