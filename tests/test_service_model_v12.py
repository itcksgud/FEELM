import json
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from service_model_v12 import PersonalScorer, merge_inputs, sha256, verify_bundle


def test_actual_precedes_one_star_dismiss_and_all_excluded():
    request = {"ratings": [{"movieId": 9, "score": 4.5}], "dismissedMovieIds": [9, 7, 7], "watchedMovieIds": [3]}
    history, excluded = merge_inputs(request)
    assert history == [{"movieId": 9, "score": 4.5, "source": "RATING"}, {"movieId": 7, "score": 1., "source": "DISMISS"}]
    assert excluded == {3, 7, 9}


def test_all_101_inputs_and_no_recency_rewrite():
    ratings = [{"movieId": mid, "score": 3.} for mid in range(101, 0, -1)]
    history, excluded = merge_inputs({"ratings": ratings})
    assert [row["movieId"] for row in history] == list(range(101, 0, -1))
    assert len(excluded) == 101
    assert all(row["score"] == 3 for row in history)


@pytest.mark.parametrize("score", [0, 5.5, 3.2, float("nan"), float("inf"), True, "4"])
def test_invalid_star(score):
    with pytest.raises(ValueError):
        merge_inputs({"ratings": [{"movieId": 1, "score": score}]})


@pytest.mark.parametrize("mid", [0, -1, True, "9", 2.5])
def test_invalid_id(mid):
    with pytest.raises(ValueError):
        merge_inputs({"dismissedMovieIds": [mid]})


@pytest.mark.parametrize("payload", [{"ratings": None}, {"ratings": [2]}, {"ratings": [{"movieId": 1}]}, {"k": 10}, []])
def test_invalid_request(payload):
    with pytest.raises(ValueError):
        merge_inputs(payload)


def test_duplicate_actual_rejected_not_averaged():
    with pytest.raises(ValueError, match="duplicate"):
        merge_inputs({"ratings": [{"movieId": 1, "score": 2}, {"movieId": 1, "score": 5}]})


def bare_scorer():
    scorer = PersonalScorer.__new__(PersonalScorer)
    scorer.ids = np.asarray([2, 7], np.int64)
    scorer.manifest = {"format": "test", "policy_version": "test", "feature_version": "test"}
    scorer.families = ("gbt", "fm")
    return scorer


def test_cold_start_not_fake_model_score():
    result = bare_scorer().recommend({"watchedMovieIds": [7]})
    assert result["status"] == "NEEDS_HOST_COLD_START"
    assert result["inputCount"] == 0
    assert all(not row["candidates"] for row in result["models"].values())


def test_unknown_preference_is_explicit_error():
    for request in ({"ratings": [{"movieId": 1, "score": 5}]}, {"dismissedMovieIds": [9999]}):
        with pytest.raises(ValueError, match="no silent drop"):
            bare_scorer().recommend(request)


@pytest.mark.parametrize("limit", [0, 501, 1.5, True])
def test_invalid_limit(limit):
    with pytest.raises(ValueError):
        bare_scorer().recommend({}, limit)


def test_bundle_tamper_detected(tmp_path):
    source = tmp_path / "data"
    source.write_bytes(b"original")
    manifest = {"format": "feelm-personal-v12-local-v1", "adoption": "NOT_ADOPTED",
                "files": {"data": {"bytes": source.stat().st_size, "sha256": sha256(source)}}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    verify_bundle(tmp_path)
    source.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        verify_bundle(tmp_path)


def test_bundle_path_escape_detected(tmp_path):
    child = tmp_path / "bundle"
    child.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"test")
    (child / "manifest.json").write_text(json.dumps({"format": "feelm-personal-v12-local-v1", "adoption": "NOT_ADOPTED",
        "files": {"../outside": {"bytes": 4, "sha256": sha256(outside)}}}))
    with pytest.raises(ValueError):
        verify_bundle(child)


def test_unlisted_bytecode_rejected(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"format": "feelm-personal-v12-local-v1",
        "adoption": "NOT_ADOPTED", "files": {}}))
    (tmp_path / "unlisted.pyc").write_bytes(b"unexpected")
    with pytest.raises(ValueError, match="unlisted"):
        verify_bundle(tmp_path)
