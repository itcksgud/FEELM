from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rec_ev_021v_pooled_judgment import (
    DEFAULT_CONTRACT,
    analyze_judgments,
    load_contract,
    read_json,
    read_jsonl,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze preregistered REC-EV-021V pooled judgments.")
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--normalized-judgments", type=Path, required=True)
    parser.add_argument("--sealed-pool-source", type=Path, required=True)
    parser.add_argument("--import-summary", type=Path, required=True)
    parser.add_argument("--evidence-mode", choices=["EXTERNAL", "SYNTHETIC_FIXTURE"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract.resolve())
        result = analyze_judgments(
            read_jsonl(args.normalized_judgments.resolve()),
            read_jsonl(args.sealed_pool_source.resolve()),
            read_json(args.import_summary.resolve()),
            contract,
            evidence_mode=args.evidence_mode,
        )
        write_json(args.output.resolve(), result)
        print(json.dumps({"status": result["status"], "completion": result["completion"]}, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(f"REC-EV-021V analysis failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
