"""Publish local aggregate views from sealed REC047 results; never fits or opens labels."""

import json
import shutil
import pandas as pd
from rec047_common import DOC, OUT, pin, require, write_json, fingerprint


def main():
    completion = json.loads((OUT / "completion.json").read_text(encoding="utf-8"))
    require(completion["fingerprint"] == fingerprint(), "completion version")
    require(
        pin(OUT / "selection.json") == completion["selection"], "completion selection"
    )
    selection = json.loads((OUT / "selection.json").read_text(encoding="utf-8"))
    require(selection["fingerprint"] == fingerprint(), "selection version")
    require(
        pin(OUT / "evaluation/fit-seal.json") == completion["evaluation_fit_seal"],
        "completion evaluation fit",
    )
    require(
        pin(OUT / "validation/fit-seal.json") == selection["validation_fit_seal"],
        "selection validation fit",
    )
    for stage in ["validation", "evaluation"]:
        fit = json.loads((OUT / stage / "fit-seal.json").read_text(encoding="utf-8"))
        require(fit["fingerprint"] == fingerprint(), "fit version " + stage)
        if stage == "evaluation":
            require(fit["selection"] == pin(OUT / "selection.json"), "fit selection")
    names = ["metrics.csv", "contrasts.csv", "strata.csv", "training-composition.csv"]
    for name in [*names, "user-metrics.parquet"]:
        require(pin(OUT / name) == completion["files"][name], "result drift " + name)
    for name in names:
        shutil.copyfile(OUT / name, DOC / name)
    user = pd.read_parquet(OUT / "user-metrics.parquet")
    selected = user[(user.level == 100) & (user.support != "ALL")]
    rows = []
    for (method, k, support), s in selected.groupby(["method", "k", "support"]):
        require(s.uid.is_unique, "one row per user in support group")
        for metric in ["mse", "pa"]:
            values = s[metric].dropna()
            rows.append(
                {
                    "level": 100,
                    "cohort": "eligible_k",
                    "method": method,
                    "k": k,
                    "support": support,
                    "metric": metric,
                    "users": len(values),
                    "observed_pairs_all_group_users": int(s.targets.sum()),
                    "mean": float(values.mean()) if len(values) else None,
                    "status": "NO_DATA"
                    if not len(values)
                    else "DESCRIPTIVE_SMALL_N"
                    if len(values) < 30
                    else "DESCRIPTIVE",
                }
            )
    pd.DataFrame(rows).to_csv(DOC / "support-summary.csv", index=False)
    runtime = []
    for stage in ["validation", "evaluation"]:
        fit = json.loads((OUT / stage / "fit-seal.json").read_text(encoding="utf-8"))
        for p in sorted((OUT / stage / "models").glob("*/*/metrics.json")):
            require(
                pin(p) == fit["files"][p.relative_to(OUT / stage).as_posix()],
                "runtime drift",
            )
            d = json.loads(p.read_text())
            runtime.append(
                {
                    "stage": stage,
                    "level": d["level"],
                    "method": d["algorithm"],
                    "training_rows": d["training_rows"],
                    "fit_seconds": d["fit_seconds"],
                    "worker_seconds": d["seconds"],
                    "cgroup_peak_bytes": d["cgroup_peak_bytes"],
                    "total_iterations": d.get("total_iterations"),
                }
            )
    require(len(runtime) == 24, "all fits present")
    pd.DataFrame(runtime).to_csv(DOC / "runtime.csv", index=False)
    names.extend(["support-summary.csv", "runtime.csv", "population.csv"])
    write_json(
        DOC / "aggregate-export.json",
        {
            "status": "DESCRIPTIVE_AGGREGATES_EXPORTED",
            "completion": pin(OUT / "completion.json"),
            "source_user_metrics": pin(OUT / "user-metrics.parquet"),
            "export_script": pin(DOC.parents[3] / "scripts/summarize_rec047.py"),
            "files": {n: pin(DOC / n) for n in names},
            "claim": "No new fits, tuning, confidence tests or target-label opening. Support means are user-weighted within each movie-support group; groups can share users.",
        },
    )
    print("DESCRIPTIVE_AGGREGATES_EXPORTED")


if __name__ == "__main__":
    main()
