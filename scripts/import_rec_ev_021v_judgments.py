from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rec_ev_021v_pooled_judgment import (
    DEFAULT_CONTRACT,
    import_judgments,
    load_contract,
    read_jsonl,
    write_json,
    write_jsonl,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and normalize deidentified REC-EV-021V judgments.")
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--participants", type=Path, required=True)
    parser.add_argument("--onboarding-inputs", type=Path, required=True)
    parser.add_argument("--judgments", type=Path, required=True)
    parser.add_argument("--blind-pool", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract.resolve())
        participants = read_jsonl(args.participants.resolve())
        onboarding = read_jsonl(args.onboarding_inputs.resolve())
        judgments = read_jsonl(args.judgments.resolve())
        pool = read_jsonl(args.blind_pool.resolve())
        normalized, summary = import_judgments(participants, onboarding, judgments, pool, contract)
        output_root = args.output_root.resolve()
        write_jsonl(output_root / "normalized-judgments.jsonl", normalized)
        write_json(output_root / "import-summary.json", summary)
        print(json.dumps({"status": "PASS_IMPORT", **summary}, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(f"REC-EV-021V import failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
