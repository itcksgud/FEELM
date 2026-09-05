#!/usr/bin/env python3
"""Run the REC-EV-025AB R1 correction without modifying the failed locked runner."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

try:
    import run_rec_ev_025_feature_transfer as base
    from validate_rec_ev_025_feature_transfer_r1_contract import materialize_contract, validate_contract
except ImportError:
    from scripts import run_rec_ev_025_feature_transfer as base
    from scripts.validate_rec_ev_025_feature_transfer_r1_contract import materialize_contract, validate_contract


R1_DEFAULT = Path(__file__).resolve().parents[1] / "docs/recommendation/contracts/rec-ev-025ab-feature-transfer-execution-r1.json"


def load_contract(path: Path) -> dict[str, Any]:
    contract = materialize_contract(path)
    validate_contract(contract)
    return contract


def install_r1_validation() -> None:
    base.validate_contract = validate_contract
    base.load_contract = load_contract
    base.DEFAULT = R1_DEFAULT


def main() -> int:
    install_r1_validation()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
