"""Package the separately completed retrain for transfer; never upload/activate it."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import zipfile

import numpy as np
import pandas as pd

from export_service_bundle_v12 import export
from service_model_v12 import PersonalScorer, merge_inputs, sha256


def k_summary(dataset):
    rows = pd.read_parquet(dataset / "rows.parquet", columns=["split", "k"])
    out = {}
    for split, frame in rows.groupby("split"):
        counts = frame.k.value_counts().sort_index()
        out[split] = {"rows": len(frame), "minimum": int(frame.k.min()), "maximum": int(frame.k.max()),
            "distinct_k": int(frame.k.nunique()), "missing_k_1_100": sorted(set(range(1, 101)) - set(counts.index.astype(int))),
            "count_by_k": {str(int(k)): int(n) for k, n in counts.items()}}
    return out


def training_reproduction(models, previous):
    if models.resolve() == previous.resolve():
        raise ValueError("a distinct completed training run is required")
    metrics = json.loads((models / "metrics.json").read_text())
    old = json.loads((previous / "metrics.json").read_text())
    for key in ("features", "calibration", "configuration", "dataset_manifest_sha256", "sources", "public_reference_sha256", "trainer_sha256", "evidence_code_sha256"):
        if metrics[key] != old[key]:
            raise ValueError(f"training inputs/configuration changed: {key}")
    if metrics["test_split_used"] or metrics["new_holdout_used"]:
        raise ValueError("unexpected holdout use")
    before = pd.read_parquet(previous / "valid_predictions.parquet")
    after = pd.read_parquet(models / "valid_predictions.parquet")
    keys = ["uid", "movie_id", "tmdb_id", "rating", "k", "candidate_timestamp", "sampling_probability"]
    if not after[keys].equals(before[keys]):
        raise ValueError("validation target/input keys differ")
    comparison = {}
    for family in ("gbt", "fm"):
        error = float(np.max(np.abs(after[family].to_numpy() - before[family].to_numpy())))
        if error != 0:
            raise ValueError(f"fixed run failed exact reproduction: {family} {error}")
        filename = "gbt-per-target.json" if family == "gbt" else "fm-per-target.npz"
        equal = sha256(models / filename) == sha256(previous / filename)
        if family == "fm":
            with np.load(models / filename, allow_pickle=False) as new, np.load(previous / filename, allow_pickle=False) as original:
                if set(new.files) != set(original.files) or any(not np.array_equal(new[name], original[name]) for name in new.files):
                    raise ValueError("FM parameter mismatch")
        elif not equal:
            if json.loads((models / filename).read_text()) != json.loads((previous / filename).read_text()):
                raise ValueError("GBT parameter mismatch")
        comparison[family] = {"file_sha_equal": equal, "parameters_equal": True, "valid_prediction_max_error": error,
                              "model_sha256": sha256(models / filename)}
    return {"actual_retraining_executed": True, "new_training_run": models.name, "previous_run": previous.name,
        "train_rows": metrics["train_rows"], "valid_rows": metrics["valid_rows"], "seconds": metrics["seconds"],
        "models": comparison, "configuration": metrics["configuration"],
        "trainer_sha256": metrics["trainer_sha256"], "dataset_manifest_sha256": metrics["dataset_manifest_sha256"],
        "new_training_metrics_sha256": sha256(models / "metrics.json"),
        "new_quality_evidence": False, "new_holdout_used": False}


def validated_k_summary(dataset, training):
    manifest_path = dataset / "manifest.json"
    if sha256(manifest_path) != training["dataset_manifest_sha256"]:
        raise ValueError("K coverage dataset differs from the trained dataset")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = dataset / "rows.parquet"
    record = manifest["artifacts"]["rows.parquet"]
    if rows.stat().st_size != record["bytes"] or sha256(rows) != record["sha256"]:
        raise ValueError("K coverage rows checksum mismatch")
    summary = k_summary(dataset)
    if set(summary) != {"train", "valid"}:
        raise ValueError("unexpected K coverage split")
    for split in ("train", "valid"):
        if summary[split]["rows"] != training[f"{split}_rows"]:
            raise ValueError("K coverage row count differs from training")
    return summary


def prepare(dataset, models, previous, catalog, references, output):
    archive = output.with_suffix(".zip")
    checksum = Path(str(archive) + ".sha256.txt")
    if output.exists() or archive.exists() or checksum.exists():
        raise FileExistsError("refusing to replace an existing handoff")
    training = training_reproduction(models, previous)
    k = validated_k_summary(dataset, training)
    output.mkdir(parents=True)
    export(catalog, models, references, output / "bundle")
    (output / "reports").mkdir()
    (output / "checks").mkdir()
    source = Path(__file__).resolve().parent
    shutil.copyfile(source / "verify_server_handoff_v12.py", output / "verify_server.py")
    (output / "reports" / "training-reproduction.json").write_text(json.dumps(training, indent=2), encoding="utf-8")
    (output / "reports" / "training-k-coverage.json").write_text(json.dumps(k, indent=2), encoding="utf-8")
    scorer = PersonalScorer(output / "bundle")
    # Public high-evidence films are selected deterministically, NOT from any user's history.
    positions = np.flatnonzero(scorer.refs.eligible_movie.to_numpy(bool) & scorer.refs.required.eq(0).to_numpy())[:102]
    if len(positions) < 102:
        raise ValueError("insufficient public-only synthetic fixture movies")
    mids = scorer.ids[positions].astype(int).tolist()
    star_pattern = (4.5, 1., 3., 5., 2.5, 4., 3.5)
    rows = [{"movieId": mid, "score": star_pattern[i % len(star_pattern)]} for i, mid in enumerate(mids)]
    fixtures = [(f"k{count}", {"ratings": rows[:count]}) for count in (0, 1, 3, 17, 37, 101)]
    fixtures.append(("rating-precedes-dismiss", {"ratings": rows[:17],
        "dismissedMovieIds": [mids[0], mids[101]], "watchedMovieIds": [mids[100]]}))
    checks = []
    for name, request in fixtures:
        result = scorer.recommend(request, limit=100)
        request_file, expected_file = f"checks/{name}-request.json", f"checks/{name}-expected.json"
        (output / request_file).write_text(json.dumps(request, indent=2), encoding="utf-8")
        (output / expected_file).write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        merged, _ = merge_inputs(request)
        if result["input"] != merged:
            raise AssertionError("all-input fixture contract failed")
        checks.append({"name": name, "request": request_file, "expected": expected_file, "effective_k": len(merged),
                       "synthetic_inference_only": True, "training_label": False})
        print(json.dumps({"fixture": name, "effective_k": len(merged), "seconds": result.get("seconds", 0)}), flush=True)
    readme = """# FEELM GBT / FM 서버 전달본

상태: 로컬 재학습·추론 검증용 / NOT_ADOPTED. 서버 업로드·운영 전환은 하지 않았습니다.
이번 두 모델은 기존 검증 학습셋으로 실제 다시 학습했습니다. 기존 설정 그대로여서 동일
가중치/예측을 재현한 것이며, 기존 v12 추천 품질 악화가 해결됐다는 뜻은 아닙니다.

## 서버에 함께 둘 파일

ZIP 전체를 한 폴더에 풉니다. `bundle/` 일부만 떼어 올리면 안 됩니다.
bundle에는 GBT/FM 가중치, 113-feature 순서, TMDB/KOBIS와 인물/장르 메타데이터,
고정 공개근거 기준/정규화, FM 영화별 인자, 추론 코드, 정확한 의존성과 SHA가 있습니다.
`checks/`는 합성 소프트웨어 테스트일 뿐 실사용자 평가나 학습 정답이 아닙니다.
`reports/`는 학습 재현 및 K 분포입니다. 원본 ML 학습 데이터나 FEELM 평점은 포함하지 않았습니다.

## 설치 및 확인 (Linux, Python 3.12)

아래 경로는 예시입니다. 실제 서버 경로·권한·자원·용도 확인 후 실행하세요.
venv와 결과 파일은 패키지 **밖**에 만듭니다. 패키지 내부 추가/변경 파일은 무결성 검사에 실패합니다.

```bash
python3.12 -m venv /srv/feelm-v12-runtime
/srv/feelm-v12-runtime/bin/python -m pip install -r /srv/feelm-v12/bundle/requirements.txt
/srv/feelm-v12-runtime/bin/python -B /srv/feelm-v12/verify_server.py --report /srv/feelm-v12-check.json
```

실행 환경에 따라 XGBoost의 OpenMP 런타임(libgomp 등)이 필요할 수 있습니다.
의존성 wheel은 ZIP에 넣지 않았으므로 설치 시 패키지 저장소 접근 또는 별도 Linux wheel 저장소가 필요합니다.
패키지 준비 PC에서는 Windows CPU 검산을 수행했습니다. Linux/실제 서버 실행·SLA는 아직 미검증입니다.
검사가 실패하면 운영 후보를 발행하지 말고 출력된 오류와 실행 환경을 확인하세요.
상위 ZIP SHA256는 옆 `.zip.sha256.txt`, 내부 파일은 `handoff.json`으로 확인합니다.

## 내 입력으로 실행

```bash
/srv/feelm-v12-runtime/bin/python -B /srv/feelm-v12/bundle/service_model_v12.py --request /srv/request.json --output /srv/new-result.json --model both --limit 500
```

`--model gbt`, `fm`, `both` 중 선택합니다. 출력 파일은 기존 파일을 덮어쓰지 않습니다.

```json
{"ratings": [{"movieId": 123, "score": 4.5}], "dismissedMovieIds": [], "watchedMovieIds": []}
```

123은 형식 설명용입니다. 실제 검증된 service movie ID를 넣어야 하며 TMDB/ML ID를 넣으면 안 됩니다.
`K`라는 요청 필드는 없습니다. ratings 및 미평가 dismiss 입력 개수에서 계산합니다.
K=1 이상 임의 개수의 입력을 모두 사용합니다. 10/20/30 버튼에 한정하거나 최신30개만 쓰지 않습니다.
K별 별도 모델이 아니며, 학습 K1~100 모두 존재/최대7128이라고 모든 K의 품질을 보증하진 않습니다.
입력이0개이면 NEEDS_HOST_COLD_START를 반환합니다. 별도 서비스 cold-start를 연결해야 합니다.
실제 별점이 있으면 그 값 우선, 없으면 관심없음을1점으로 합칩니다. 본 영화·관심없음은 후보 제외합니다.
별점은0.5~5 반점 단위입니다. 미연결 입력은 몰래 버리지 않고 오류를 냅니다.

## 현재 consumer에 연결하기 전

이 출력은 개인 후보 core이며 Redis v6 envelope가 아닙니다. ALS artifact loader를 대체하는
호출부·의존성을 연결하고, modelVersion/generationId/벡터의 일관성·재발행·rollback을 검증해야 합니다.
FM 인자는 ALS 벡터가 아니고 GBT 가짜 벡터는 없습니다. 파티 ALS 내적, discovery 2+1, K0,
최종 백엔드 제외·이유 코드와 새 영화/최신 메타데이터 갱신은 별도 통합 작업입니다.
현재 후보 eligibility는2026-09-21 snapshot입니다. 참고 GitLab revision은 bundle manifest에 있습니다.
모델 원점수와 공통 공개/개인 자격 정책이 둘 다 들어갑니다. score는 표시용 보정 별점이 아닙니다.
NOT_ADOPTED 상태를 임의로 SERVING으로 바꾸지 말고 품질·제품 계약·운영 검증 후 승격하세요.
TMDB/KOBIS/MovieLens 출처·표시·이용 범위를 보존하고 외부 배포 전 사용권을 확인하세요.
"""
    (output / "README-server.md").write_text(readme, encoding="utf-8")
    files = {path.relative_to(output).as_posix(): {"bytes": path.stat().st_size, "sha256": sha256(path)}
             for path in sorted(output.rglob("*")) if path.is_file()}
    manifest = {"format": "feelm-v12-server-handoff-v1", "adoption": "NOT_ADOPTED", "sharing": "LOCAL_PREPARED_NOT_UPLOADED",
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "actual_retraining_executed": True,
        "training_run": models.name, "checks": checks, "files": files,
        "packager_sha256": sha256(Path(__file__)), "verifier_sha256": sha256(source / "verify_server_handoff_v12.py"),
        "private_user_data_included": False, "linux_runtime_verified": False, "service_integration_completed": False}
    (output / "handoff.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                target.write(path, path.relative_to(output).as_posix())
    with zipfile.ZipFile(archive) as source_zip:
        if source_zip.testzip() is not None or set(source_zip.namelist()) != set(files) | {"handoff.json"}:
            raise AssertionError("ZIP CRC/inventory failure")
    checksum.write_text(f"{sha256(archive)}  {archive.name}\n", encoding="utf-8")
    print(json.dumps({"zip": str(archive), "bytes": archive.stat().st_size, "sha256": sha256(archive)}), flush=True)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("dataset", "models", "previous", "catalog", "references", "output"):
        parser.add_argument(f"--{arg}", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.dataset, args.models, args.previous, args.catalog, args.references, args.output)
