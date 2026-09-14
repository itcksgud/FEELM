"""Read-only external Docker samples; never changes the sealed experiment."""

import datetime
import json
import subprocess
import time
from pathlib import Path

out = Path(__file__).resolve().parents[1] / "outputs/recommendation-evidence/rec-ev-046"
with (out / "resource-samples.jsonl").open("a", encoding="utf-8") as stream:
    while (
        not (out / "all-fit-seal.json").exists() and not (out / "failure.json").exists()
    ):
        names = subprocess.check_output(
            ["docker", "ps", "--filter", "name=rec046-r", "--format", "{{.Names}}"],
            text=True,
        ).splitlines()
        if names:
            result = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{json .}}", *names],
                capture_output=True,
                text=True,
                timeout=20,
            )
            for line in result.stdout.splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                row["observed_at"] = datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat()
                row["measurement"] = "periodic_sample_not_exact_peak"
                stream.write(json.dumps(row) + "\n")
                stream.flush()
        time.sleep(5)
