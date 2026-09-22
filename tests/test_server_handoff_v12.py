import json
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_server_handoff_v12 import digest, verify_files, score_error
from prepare_server_handoff_v12 import k_summary, training_reproduction, validated_k_summary


def manifest_fixture(root):
    data = root / "data.json"
    data.write_text("{}", encoding="utf-8")
    (root / "handoff.json").write_text(json.dumps({"format": "feelm-v12-server-handoff-v1", "adoption": "NOT_ADOPTED",
        "files": {"data.json": {"bytes": 2, "sha256": digest(data)}}}), encoding="utf-8")


def test_handoff_hashes(tmp_path):
    manifest_fixture(tmp_path)
    assert verify_files(tmp_path)["adoption"] == "NOT_ADOPTED"


def test_handoff_tamper(tmp_path):
    manifest_fixture(tmp_path)
    (tmp_path / "data.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        verify_files(tmp_path)


def test_handoff_added_bytecode(tmp_path):
    manifest_fixture(tmp_path)
    (tmp_path / "runtime.pyc").write_bytes(b"unexpected")
    with pytest.raises(ValueError, match="inventory"):
        verify_files(tmp_path)


def test_handoff_missing_file(tmp_path):
    (tmp_path / "handoff.json").write_text(json.dumps({"format": "feelm-v12-server-handoff-v1", "adoption": "NOT_ADOPTED",
        "files": {"absent": {"bytes": 0, "sha256": "unknown"}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="inventory"):
        verify_files(tmp_path)


def test_k_summary_does_not_claim_unobserved_k(tmp_path):
    pd.DataFrame({"split": ["train", "train", "train", "valid"], "k": [1, 1, 101, 3]}).to_parquet(tmp_path / "rows.parquet")
    summary = k_summary(tmp_path)
    assert summary["train"]["distinct_k"] == 2
    assert summary["train"]["maximum"] == 101
    assert summary["train"]["missing_k_1_100"] == list(range(2, 101))
    assert summary["valid"]["count_by_k"] == {"3": 1}


def test_packaging_is_not_itself_new_training(tmp_path):
    with pytest.raises(ValueError, match="distinct"):
        training_reproduction(tmp_path, tmp_path)


def test_k_provenance_requires_trained_dataset(tmp_path):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="trained dataset"):
        validated_k_summary(tmp_path, {"dataset_manifest_sha256": "different"})


def test_k_provenance_rejects_changed_rows(tmp_path):
    rows = tmp_path / "rows.parquet"
    rows.write_bytes(b"changed")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"artifacts": {"rows.parquet": {"bytes": 7, "sha256": "expected"}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        validated_k_summary(tmp_path, {"dataset_manifest_sha256": digest(manifest)})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_score_parity_nonfinite_fails(value):
    for left, right in ((value, 1.), (1., value)):
        with pytest.raises(AssertionError, match="nonfinite"):
            score_error([{"score": left}], [{"score": right}])


def test_score_parity_tolerance_and_length():
    assert score_error([{"score": 1.}], [{"score": 1. + 1e-6}]) < 1e-5
    assert score_error([], []) == 0
    with pytest.raises(AssertionError, match="length"):
        score_error([], [{"score": 1.}])
    with pytest.raises(AssertionError, match="parity"):
        score_error([{"score": 1.}], [{"score": 1.1}])
