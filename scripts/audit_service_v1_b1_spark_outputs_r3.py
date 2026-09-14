"""Independent, read-only B1 bundle audit; only an explicit CLI writes a sibling review.

No runner/worker module is imported.  Spark, Docker, fitting and evaluation labels
are never opened by this auditor.  Private fixture limits are not CLI options.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import uuid
from typing import Any, Mapping

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

RECOVERY_PLAN_SHA256 = "4de13f041532b7249b22f5a2bbfbd7919f79d94e4c25f2ae16fe95104a2b3d9a"
R2_PLAN_SHA256 = "c5b90b0d19374588fc5cb7a1d98cd6510a1c3708df9667131df5eb3826c79d49"
R2_RUN_ID = "b1-gbt120-s339-v1-r2"
RUN_ID = "b1-gbt120-s339-v1-r3-local4c12g-t14400"
PROFILE_ID = "local4c12g-t14400-v1"
R2_FIT_FAILURE_BYTES = 24_854
R2_FIT_FAILURE_SHA256 = "4ad571439af7f290dd32ce27922519f31e3b156f90998b26a69e2c892aba7e37"
R2_OUTER_RUNNER_BYTES = 88_623
R2_OUTER_RUNNER_SHA256 = "cbc8983a0047b5aa0a2fc5a2d3794080afcf48583fe1be90c6d2dd3dd5af6f8e"
R2_PREFLIGHT_MANIFEST_BYTES = 2_351
R2_PREFLIGHT_MANIFEST_SHA256 = "7a47a40ac923ba1f8a638ad6029aa72349245b65055f3db8915a2327de174e01"
R2_PREFLIGHT_REVIEW_BYTES = 8_706
R2_PREFLIGHT_REVIEW_SHA256 = "cc49688618bfd408a957a22522456912320519d981c34ad87aefb190a0910132"
R2_TRAINING_SOURCE_SET_SHA256 = "6d82b745ec4e796f823f6506497ad9a15236f92a91a04d3452c4755fada499f9"
MODEL_INPUT_SET_SHA256 = "cd41a8ac341cdbc1435bbce21bd78e3b3012e2c50ca25cfac0ef034088b1ccfd"
WORKER_RUNTIME_SET_SHA256 = "ff90bcd014b8ac498f2c5ccba27eb33d0a83f3ee062d36dc426e33a95c6a0a0a"
EXPECTED_WORKER_SHA256 = "9708e0b3fdc618c5df2240c9e1dcf368fd4f6fa411d7915c31104b3af2146d42"
IMAGE_ID = "sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8"
MAX_MEMORY_BYTES = 12 * 1024**3
CLOCK_TOLERANCE_SECONDS = 2.0
FEATURES = tuple(f"x{i:03}" for i in range(230))
FORBIDDEN = {"labels.parquet", "evaluation-seal.json"}
PINNED = {
    "contract/training-recipe.v1.json": (15764, "d403a27fab09453f98b9988fcfb3867b83e41ae217f9f5f3be5976321eef8d7b"),
    "contract/service-v1.json": (15504, "1a9ba0cd0101f6d065227c37ee642a3fc75c2c69b34c0da8fa6dacff351c2343"),
    "contract/MODELS.md": (25079, "5ad4c852b89ca02da00c30f0fc184aa6018db4abf1808c057772a4908d376701"),
    "contract/feature-schema.v1.json": (39243, "fda2be4f40b76e46b88dbb53523ef404bbf8a13c68bbf012acc58da9a63948ca"),
    "source/natural-train.parquet": (832717601, "9d8d33a252991c032704d4072003b4fb9f400136f3c411a2698ae5fee592fa45"),
    "source/tmdb-masked-train.parquet": (891461814, "27aee771597ba230654b2e99c1eea047fd25e3265161c51b6873474d47c65f01"),
    "source/masked-manifest.json": (5176, "82eb3635f1ae914a056c2813c4786dfa2de56abbb37aaf5ce852685f6eb6a557"),
    "source/views-manifest.json": (1710, "df8dd8bbfea4a71e506958c5b7e1499b5010350a326cbfac2c9bfe23a607792a"),
    "source/masked-review.json": (10174, "f7e322206d95c8ad418926a08394625f84a25b611f4e9595c97dd03cca6c7b1c"),
    "source/natural-score.parquet": (2985357, "9832425537943823f524ae6730aa1059b77d2c0464664f1dc29357bf95078f9b"),
}
MODEL_INPUT_NAMES = {
    "source/natural-train.parquet", "source/tmdb-masked-train.parquet",
    "source/masked-manifest.json", "source/views-manifest.json", "source/masked-review.json",
    "contract/training-recipe.v1.json", "contract/service-v1.json", "contract/MODELS.md",
    "contract/feature-schema.v1.json",
}
WORKER_RUNTIME_NAMES = {
    "implementation/service_v1_b1_spark_worker.py", "implementation/combination340_models.py",
    "implementation/rec046_common.py", "runtime/docker-image-id",
}
EXECUTION_NAMES = {
    "execution/service-v1-b1-r3-fit-recovery.md", "execution/local4c12g-t14400-profile.json",
    "execution/run_service_v1_b1_gbt_r3.py", "execution/test_service_v1_b1_gbt_runner_r3.py",
}
SCORE_INPUT_NAMES = {"source/natural-score.parquet"}
R2_PREFLIGHT_INVENTORY = {
    "command.json": (7720, "86e05c4b3555297c9a803b91cea840b2f7ea83286a2a82591a5eb1f950b15859"),
    "input-lock.json": (3489, "fbd4ae898a8cc35e3e4e7c4442298d6940dc88b3854d20182b7f38c87334d775"),
    "manifest.json": (2351, R2_PREFLIGHT_MANIFEST_SHA256),
    "partition-identity.json": (2489, "2a7f7fbd9000730db749cc673d9a43a5027fa3ca76d517cda826f3f420d45c99"),
    "recovery-reference.json": (959, "9c7b39b342743e40461f5d5b9c8f0c02268d3c4f82c177ac39b4f97ad8b4133d"),
    "resource.json": (1565, "a91445352b3fd2f53b84106667686f1d8778ef5b4399d2c15631c7844b634ffd"),
    "run.log": (12955, "52a3c6a0f78b293f11ec2af40d2cd23089d695f4c063bebb6a416aab0f87a063"),
}

PARTITION_COUNTS = (624054, 624438, 624481, 625449, 624427, 624773, 624390, 625057)
PARTITION_PINS = (
    "21fbf39b23490f46a935ff54dcdab20a1f38361351840780f278a27c4c8de4ea",
    "b54c60df6c9ebf0b1268c60124f9908b2d86a7bbb9f2fceffce520a344123891",
    "5c072bdc8ec9396af8e6dad574d95b13c20988765acf1177d46da6e2c82500d8",
    "84692cce25046923f3a23751f3c1ca2902324f0e44650140dcdf607322cb8f7d",
    "51e8100ac0aba07e8b3a0852f375924ae15a6baa2c81bb80f5953396efd2d2a1",
    "14b8332eebabecb4cdb90975220967a2c258f7b2aae1ebd20bcbcc410ac6d4d0",
    "442632597415d7f1e194fb6133a1675997a0e5d741bc9242dded088a0bb35923",
    "2f51a1ffa91329f80d2802843df72e11ad387b2e304f82a653fcee3294336344",
)
TARGETS = {
    "preflight": {"input_lock": "input-lock.json", "recovery_reference": "recovery-reference.json",
                  "partition_identity": "partition-identity.json",
                  "command": "command.json", "resource": "resource.json", "run_log": "run.log",
                  "execution_profile": "execution-profile.json"},
    "fit": {"input_lock": "input-lock.json", "preflight_reference": "preflight-reference.json",
            "recovery_reference": "recovery-reference.json", "execution_profile": "execution-profile.json",
            "command": "command.json", "partition_identity": "partition-identity.json",
            "resolved_estimator": "resolved-estimator.json", "model_file_inventory": "model-file-inventory.json",
            "threshold_fixtures": "threshold-fixtures.npz", "fit_metrics": "fit-metrics.json",
            "resource": "resource.json", "run_log": "run.log"},
    "score": {"fit_reference": "fit-reference.json", "score_input_lock": "score-input-lock.json",
              "recovery_reference": "recovery-reference.json", "execution_profile": "execution-profile.json",
              "command": "command.json", "analyzed_plan": "score/analyzed-plan.txt",
              "predictions": "score/predictions.parquet", "resource": "resource.json", "run_log": "run.log"},
}
STATUSES = {"preflight": "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW",
            "fit": "B1_MODEL_FIT_COMPLETE_AUDIT_PENDING", "score": "B1_NATURAL_SCORE_COMPLETE_AUDIT_PENDING"}


class AuditError(ValueError):
    """A failed invariant; never publish a PASS review after this exception."""


def need(value: Any, message: str) -> None:
    if not value:
        raise AuditError(message)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024**2), b""):
            h.update(chunk)
    return h.hexdigest()


def pin(path: Path, logical: str | None = None) -> dict[str, Any]:
    safe_file(path)
    result = {"bytes": path.stat().st_size, "sha256": digest(path)}
    if logical is not None:
        result["path"] = logical
    return result


def safe_file(path: Path) -> None:
    need(path.is_file() and not path.is_symlink(), f"not a regular file: {path}")
    for parent in (path, *path.parents):
        need(not parent.is_symlink() and not (hasattr(parent, "is_junction") and parent.is_junction()),
             f"link/reparse path forbidden: {path}")


def relative_name(name: Any) -> str:
    need(isinstance(name, str) and name and "\\" not in name and "\x00" not in name,
         "invalid relative path")
    p = PurePosixPath(name)
    need(not p.is_absolute() and ":" not in name and all(x not in ("", ".", "..") for x in name.split("/")),
         f"path traversal: {name}")
    need(p.name.casefold() not in FORBIDDEN, "evaluation target is forbidden in audit input")
    return name


def json_object(path: Path) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            need(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result
    safe_file(path)
    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                        parse_constant=lambda value: (_ for _ in ()).throw(AuditError(f"nonfinite JSON: {value}")))
    need(isinstance(result, dict), f"JSON object required: {path}")
    return result


def valid_pin(record: Any) -> None:
    need(isinstance(record, dict), "pin must be an object")
    need(type(record.get("bytes")) is int and record["bytes"] >= 0, "invalid pin bytes")
    need(isinstance(record.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", record["sha256"]),
         "invalid pin sha256")


def check_pin(path: Path, record: Mapping[str, Any]) -> None:
    valid_pin(record)
    actual = pin(path)
    need(actual == {"bytes": record["bytes"], "sha256": record["sha256"]}, f"pin mismatch: {path}")


def set_digest(records: list[dict[str, Any]]) -> str:
    need(isinstance(records, list), "records must be a list")
    seen = set()
    parts = []
    for item in records:
        valid_pin(item)
        name = relative_name(item.get("path"))
        need(name not in seen, f"duplicate record: {name}")
        seen.add(name)
        parts.append((name, item["bytes"], item["sha256"]))
    payload = b"".join(f"{p}\x00{n}\x00{h}\n".encode("utf-8") for p, n, h in sorted(parts))
    return hashlib.sha256(payload).hexdigest()


def inventory(root: Path) -> dict[str, dict[str, Any]]:
    need(root.is_dir() and not root.is_symlink(), "bundle directory missing or linked")
    result = {}
    for path in sorted(root.rglob("*")):
        need(not path.is_symlink() and not (hasattr(path, "is_junction") and path.is_junction()), "linked bundle member")
        if path.is_file():
            result[relative_name(path.relative_to(root).as_posix())] = pin(path)
    return result


@dataclass(frozen=True)
class Limits:
    source_rows: int = 4_997_069
    score_rows: int = 93_230
    trees: int = 120
    canonical: bool = True


def spark_long_partitions(row_ids: np.ndarray) -> np.ndarray:
    """Spark Murmur3_x86_32.hashLong(value, 42), then positive modulo eight."""
    mask = np.uint64(0xFFFFFFFF)
    def rot(value: np.ndarray, n: int) -> np.ndarray:
        return ((value << np.uint64(n)) | (value >> np.uint64(32-n))) & mask
    h = np.full(len(row_ids), 42, dtype=np.uint64)
    words = np.asarray(row_ids, dtype=np.uint64)
    for k in (words & mask, words >> np.uint64(32)):
        k = (k * np.uint64(0xCC9E2D51)) & mask
        k = rot(k, 15)
        k = (k * np.uint64(0x1B873593)) & mask
        h = rot(h ^ k, 13)
        h = (h * np.uint64(5) + np.uint64(0xE6546B64)) & mask
    h ^= np.uint64(8)
    h ^= h >> np.uint64(16)
    h = (h * np.uint64(0x85EBCA6B)) & mask
    h ^= h >> np.uint64(13)
    h = (h * np.uint64(0xC2B2AE35)) & mask
    h ^= h >> np.uint64(16)
    return (h & np.uint64(7)).astype(np.int64)


def train_schema() -> pa.Schema:
    return pa.schema([("row_id", pa.int64()), ("uid", pa.int32()), ("label", pa.float64())]
                     + [(x, pa.float32()) for x in FEATURES])


def paired_batches(left: Path, right: Path):
    with pq.ParquetFile(left) as lp, pq.ParquetFile(right) as rp:
        need(lp.schema_arrow.equals(train_schema(), check_metadata=False)
             and rp.schema_arrow.equals(train_schema(), check_metadata=False), "training schema")
        li, ri = iter(lp.iter_batches(batch_size=4096)), iter(rp.iter_batches(batch_size=4096))
        a = b = None
        while True:
            if a is None or not a.num_rows:
                a = next(li, None)
            if b is None or not b.num_rows:
                b = next(ri, None)
            if a is None or b is None:
                need(a is None and b is None, "training axes have different row counts")
                break
            n = min(a.num_rows, b.num_rows)
            yield a.slice(0, n), b.slice(0, n)
            a, b = a.slice(n), b.slice(n)


def recalculate_identity(natural: Path, masked: Path, limits: Limits) -> dict[str, Any]:
    rows = [0] * 8
    ids = [hashlib.sha256() for _ in range(8)]
    logical = [hashlib.sha256() for _ in range(8)]
    dtype = np.dtype([("rid", "<i8"), ("view", "<i4"), ("uid", "<i4"),
                      ("label", "<f8"), ("weight", "<f8")])
    cursor = 0
    for a, b in paired_batches(natural, masked):
        need(all(column.null_count == 0 for column in (*a.columns, *b.columns)), "null training value")
        na = {name: a.column(i).to_numpy(zero_copy_only=False) for i, name in enumerate(a.schema.names)}
        nb = {name: b.column(i).to_numpy(zero_copy_only=False) for i, name in enumerate(b.schema.names)}
        n = len(na["row_id"])
        expected = np.arange(cursor, cursor+n, dtype=np.int64)
        need(np.array_equal(na["row_id"], expected) and np.array_equal(nb["row_id"], expected), "training row identity")
        need(np.array_equal(na["uid"], nb["uid"]) and np.array_equal(na["label"].view(np.uint64), nb["label"].view(np.uint64)),
             "training uid/label bit parity")
        y = na["label"]
        need(np.isfinite(y).all() and ((y >= .5) & (y <= 5) & (y * 2 == np.floor(y * 2))).all(), "invalid training stars")
        for i, feature in enumerate(FEATURES):
            need(np.isfinite(na[feature]).all() and np.isfinite(nb[feature]).all(), "nonfinite feature")
            if i < 200:
                need(np.array_equal(na[feature].view(np.uint32), nb[feature].view(np.uint32)), "masked prefix drift")
        part = spark_long_partitions(expected)
        for index in range(8):
            take = part == index
            count = int(take.sum())
            block = np.empty((count, 4), dtype=dtype)
            block["rid"] = expected[take, None]
            block["view"] = np.arange(4, dtype=np.int32)
            block["uid"] = na["uid"][take, None]
            block["label"] = y[take, None]
            block["weight"] = .25
            ids[index].update(expected[take].astype("<i8").tobytes())
            logical[index].update(block.tobytes())
            rows[index] += count
        cursor += n
    need(cursor == limits.source_rows, "full source row count")
    records = [{"partition": i, "sourceRows": rows[i], "logicalRows": rows[i]*4,
                "view0RowIdSha256": ids[i].hexdigest(), "logicalIdentitySha256": logical[i].hexdigest()}
               for i in range(8)]
    if limits.canonical:
        need(tuple(rows) == PARTITION_COUNTS, "Spark partition census")
        need(tuple(h.hexdigest() for h in ids) == PARTITION_PINS, "B0 partition digest")
    return {"schemaVersion": "feelm-service-v1-b1-partition-identity/1", "partitions": records,
            "sourceRows": cursor, "logicalRows": cursor*4, "viewIds": [0, 1, 2, 3], "viewWeight": .25}


class NativeTrees:
    """Independent traversal of the persisted Spark GBT Parquet, never unpickle."""
    def __init__(self, root: Path, expected_trees: int):
        metadata = [x for x in (root / "metadata").glob("part-*") if x.is_file()]
        need(len(metadata) == 1, "single native metadata part required")
        self.metadata = json_object(metadata[0])
        need(self.metadata.get("class", "").endswith("GBTRegressionModel")
             and self.metadata.get("numFeatures") == 230 and self.metadata.get("numTrees") == expected_trees,
             "native GBT metadata/tree count")
        data = pq.read_table(root / "data").to_pylist()
        weights = pq.read_table(root / "treesMetadata").to_pylist()
        w = {}
        for row in weights:
            tid = row["_1"]
            need(type(tid) is int and tid not in w and math.isfinite(row["_3"]), "native tree weight")
            w[tid] = float(row["_3"])
        need(set(w) == set(range(expected_trees)), "native tree IDs")
        trees: dict[int, dict[int, Any]] = {i: {} for i in w}
        for row in data:
            tid, node = row["treeID"], row["nodeData"]
            need(tid in trees and node["id"] not in trees[tid], "duplicate or unknown native node")
            trees[tid][node["id"]] = node
        self.trees = []
        self.splits = []
        for tid in sorted(trees):
            nodes, visited, stack = trees[tid], set(), [0]
            while stack:
                index = stack.pop()
                need(index in nodes and index not in visited, "native missing/cyclic child")
                visited.add(index)
                node = nodes[index]
                left, right = node["leftChild"], node["rightChild"]
                if left == -1:
                    need(right == -1 and math.isfinite(node["prediction"]), "invalid native leaf")
                else:
                    split = node["split"]
                    feature = split["featureIndex"]
                    threshold = split["leftCategoriesOrThreshold"]
                    need(right >= 0 and type(feature) is int and 0 <= feature < 230 and
                         split["numCategories"] == -1 and len(threshold) == 1 and math.isfinite(threshold[0]),
                         "unsupported native split")
                    self.splits.append((feature, float(threshold[0])))
                    stack.extend([right, left])
            need(visited == set(nodes), "unreachable native nodes")
            self.trees.append((w[tid], nodes))

    def predict(self, features: np.ndarray) -> np.ndarray:
        # Spark vectors compare binary64 values. NumPy otherwise casts a scalar
        # threshold down to float32, changing midpoint branch decisions.
        features = np.asarray(features, dtype=np.float64)
        need(features.ndim == 2 and features.shape[1] == 230 and np.isfinite(features).all(), "native input features")
        total = np.zeros(len(features), dtype=np.float64)
        for weight, nodes in self.trees:
            pending = [(0, np.arange(len(features)))]
            while pending:
                index, rows = pending.pop()
                if not len(rows):
                    continue
                node = nodes[index]
                if node["leftChild"] == -1:
                    total[rows] += weight * node["prediction"]
                else:
                    split = node["split"]
                    go_left = features[rows, split["featureIndex"]] <= split["leftCategoriesOrThreshold"][0]
                    pending.extend([(node["leftChild"], rows[go_left]), (node["rightChild"], rows[~go_left])])
        need(np.isfinite(total).all(), "nonfinite independent tree output")
        return total


def audit_thresholds(path: Path, model: NativeTrees) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as z:
        need(set(z.files) == {"features", "predictions", "indices", "split_features", "split_thresholds"}, "threshold fixture inventory")
        x, y = z["features"], z["predictions"]
        fs, ts = z["split_features"], z["split_thresholds"]
        need(np.array_equal(z["indices"], np.arange(230)) and z["indices"].dtype == np.dtype("int32"), "fixture feature indices")
        expected = [pair for pair in model.splits for _ in range(3)]
        need(x.shape == (len(expected), 230) and x.dtype == np.dtype("float64") and
             y.shape == (len(expected),) and y.dtype == np.dtype("float64") and
             fs.dtype == np.dtype("int32") and ts.dtype == np.dtype("float64"), "fixture shape/dtype")
        need(len(expected) > 0 and len(fs) == len(ts) == len(expected) and np.isfinite(x).all() and np.isfinite(y).all(), "fixture finite/count")
        need(list(zip(fs.tolist(), ts.tolist())) == expected, "all native split fixtures/order required")
        values = x[np.arange(len(x)), fs]
        expected_values = np.array([value for _, threshold in model.splits
                                    for value in (np.nextafter(threshold, -np.inf), threshold, np.nextafter(threshold, np.inf))])
        need(np.array_equal(values.view(np.uint64), expected_values.view(np.uint64)), "threshold below/equal/above coverage")
        actual = model.predict(x)
        error = float(np.max(np.abs(actual-y)))
        need(error <= 1e-6, "independent portable threshold parity")
        return {"rows": len(x), "nonLeafSplitCount": len(model.splits), "maxAbsError": error, "status": "PASS"}


def audit_predictions(source: Path, output: Path, model: NativeTrees, limits: Limits) -> dict[str, Any]:
    safe_file(output)
    wanted = pa.schema([("row_id", pa.int64()), ("uid", pa.int32()), ("prediction", pa.float64())])
    with pq.ParquetFile(output) as pp:
        need(pp.schema_arrow.equals(wanted, check_metadata=False), "prediction schema")
        predicted = pp.read()
    # Explicit projection: the physical source label is neither selected nor read.
    natural = pq.read_table(source, columns=["row_id", "uid", *FEATURES])
    need(natural.schema.equals(pa.schema([("row_id", pa.int64()), ("uid", pa.int32())]
                                         + [(f, pa.float32()) for f in FEATURES]), check_metadata=False), "score projection schema")
    need(natural.num_rows == predicted.num_rows == limits.score_rows, "score row count")
    need(all(c.null_count == 0 for c in (*natural.columns, *predicted.columns)), "null score field")
    expected = np.arange(limits.score_rows, dtype=np.int64)
    for frame in (natural, predicted):
        need(np.array_equal(frame["row_id"].to_numpy(), expected), "duplicate/missing/out-of-order score ID")
    need(np.array_equal(natural["uid"].to_numpy(), predicted["uid"].to_numpy()), "score UID swap")
    y = predicted["prediction"].to_numpy()
    need(np.isfinite(y).all(), "nonfinite prediction")
    max_error = 0.0
    for start in range(0, limits.score_rows, 4096):
        table = natural.slice(start, min(4096, limits.score_rows-start))
        x = np.column_stack([table[f].to_numpy() for f in FEATURES])
        actual = model.predict(x)
        max_error = max(max_error, float(np.max(np.abs(actual-y[start:start+len(actual)]))))
    need(max_error <= 1e-6, "full natural score independent portable parity")
    return {"rows": limits.score_rows, "uniqueRowIds": limits.score_rows,
            "strictlyIncreasing": True, "uidParity": True, "finite": True, "maxAbsError": max_error,
            "labelRead": False, "singlePhysicalFile": True}


@dataclass(frozen=True)
class Roots:
    standalone: Path
    team: Path

    def bundle(self, phase: str) -> Path:
        need(phase in TARGETS, "unknown phase")
        return self.standalone / "outputs/recommendation-evidence/service-v1-pretraining-20260913" / (RUN_ID + "-" + phase)

    def review(self, phase: str) -> Path:
        b = self.bundle(phase)
        return b.with_name(b.name + "-result-review.json")

    def r2_preflight(self) -> Path:
        return self.standalone / "outputs/recommendation-evidence/service-v1-pretraining-20260913" / (R2_RUN_ID + "-preflight")

    def r2_preflight_review(self) -> Path:
        return self.r2_preflight().with_name(self.r2_preflight().name + "-result-review.json")

    def r2_fit_failure(self) -> Path:
        return self.standalone / "outputs/recommendation-evidence/service-v1-pretraining-20260913" / (R2_RUN_ID + "-fit-failure.json")

    def profile_contract(self) -> Path:
        return self.standalone / "docs/recommendation/plans/service-v1-b1-r3-local4c12g-t14400-profile.json"

    def r2_auditor(self) -> Path:
        return self.standalone / "scripts/audit_service_v1_b1_spark_outputs.py"

    def logical(self, path: Path) -> str:
        for name, root in (("standalone", self.standalone), ("team", self.team)):
            try:
                return name + "/" + path.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                pass
        raise AuditError("file is outside the two repositories")

    def resolve_logical(self, logical: str) -> Path:
        need(isinstance(logical, str) and "\\" not in logical, "invalid logical path")
        if logical == "ancestor/service-v1-b1-spark-runner.md":
            return self.standalone / "docs/recommendation/plans/service-v1-b1-spark-runner.md"
        need(not logical.startswith("ancestor/"), "unknown legacy ancestor alias")
        prefix, separator, relative = logical.partition("/")
        need(separator == "/" and prefix in {"standalone", "team"} and relative, "invalid logical prefix")
        parts = Path(relative).parts
        need(all(part not in {"", ".", ".."} for part in parts), "unsafe logical path")
        root = self.standalone if prefix == "standalone" else self.team
        result = root.joinpath(*parts)
        need(result.resolve().is_relative_to(root.resolve()), "logical path escapes repository")
        return result

    def sources(self) -> dict[str, Path]:
        base = self.standalone / "outputs/recommendation-evidence"
        masked = base / "service-v1-pretraining-20260913/b1-masked-views-v1"
        result = {
            "contract/training-recipe.v1.json": self.team / "pipeline/configs/service-v1/training-recipe.v1.json",
            "contract/service-v1.json": self.team / "pipeline/artifacts/service-v1.json",
            "contract/MODELS.md": self.team / "pipeline/docs/service-v1/MODELS.md",
            "contract/feature-schema.v1.json": self.team / "pipeline/configs/service-v1/feature-schema.v1.json",
            "source/natural-train.parquet": base / "foundation340/RH/train.parquet",
            "source/natural-score.parquet": base / "foundation340/RH/score.parquet",
            "source/tmdb-masked-train.parquet": masked / "tmdb-masked-rh230.parquet",
            "source/masked-manifest.json": masked / "manifest.json",
            "source/views-manifest.json": masked / "views-manifest.json",
            "source/masked-review.json": masked.with_name(masked.name + "-result-review.json"),
            "execution/service-v1-b1-r3-fit-recovery.md": self.standalone / "docs/recommendation/plans/service-v1-b1-r3-fit-recovery.md",
            "execution/local4c12g-t14400-profile.json": self.profile_contract(),
            "execution/run_service_v1_b1_gbt_r3.py": self.standalone / "scripts/run_service_v1_b1_gbt_r3.py",
            "execution/test_service_v1_b1_gbt_runner_r3.py": self.standalone / "tests/test_service_v1_b1_gbt_runner_r3.py",
            "implementation/service_v1_b1_spark_worker.py": self.standalone / "scripts/service_v1_b1_spark_worker.py",
            "implementation/combination340_models.py": self.standalone / "scripts/combination340_models.py",
            "implementation/rec046_common.py": self.standalone / "scripts/rec046_common.py",
        }
        return result


def record_map(records: Any) -> dict[str, dict[str, Any]]:
    set_digest(records)
    return {r["path"]: r for r in records}


def same_records(actual: Any, expected: list[dict[str, Any]], message: str) -> None:
    need(set_digest(actual) == set_digest(expected), message)


def audit_profile_contract(roots: Roots) -> tuple[dict[str, Any], dict[str, Any]]:
    path = roots.profile_contract()
    profile = json_object(path)
    need(set(profile) == {"schemaVersion", "status", "profileId", "runId", "recoveryOrdinal", "hostClass",
         "singleAttempt", "automaticRetry", "docker", "spark", "timeoutsSeconds", "resourceObservation",
         "modelContract", "authorization"}, "profile top-level fields")
    need(profile.get("schemaVersion") == "feelm-service-v1-b1-execution-profile/1"
         and profile.get("status") == "REVIEWED_LOCAL_BOUNDED_PROBE"
         and profile.get("profileId") == PROFILE_ID and profile.get("runId") == RUN_ID
         and profile.get("recoveryOrdinal") == "r3"
         and profile.get("hostClass") == "current-local-docker-desktop"
         and profile.get("singleAttempt") is True and profile.get("automaticRetry") is False,
         "profile identity/meaning")
    need(profile.get("docker") == {"cpus": "4", "memory": "12g", "memorySwap": "12g",
         "maxMemoryBytes": MAX_MEMORY_BYTES, "network": "none"}, "profile Docker resources")
    need(profile.get("spark") == {"master": "local[4]", "driverMemory": "8g", "shufflePartitions": 8,
         "adaptiveExecution": False}, "profile Spark resources")
    need(profile.get("timeoutsSeconds") == {"preflightDryRun": 14400, "preflightFull": 14400,
         "fit": 14400, "score": 14400}, "profile timeout")
    need(profile.get("resourceObservation") == {"hostCgroupPollIntervalSeconds": 2.0,
         "sampleBeforeTimeoutStop": True, "inspectBeforeTimeoutStop": True,
         "unknownPeakBlocksSuccess": True}, "profile resource observation")
    need(profile.get("authorization") == {"implementation": True, "publicPreflight": True,
         "fitBeforeNewSiblingReviewPass": False, "deployment": False}, "profile authorization")
    model = profile.get("modelContract")
    need(isinstance(model, dict) and set(model) == {"sourceRows", "logicalRows", "scoreRows", "featureCount",
         "partitions", "seed", "trees", "workerSha256", "modelInputSetSha256", "workerRuntimeSetSha256"}
         and model.get("sourceRows") == 4_997_069
         and model.get("logicalRows") == 19_988_276 and model.get("scoreRows") == 93_230
         and model.get("featureCount") == 230 and model.get("partitions") == 8
         and model.get("seed") == 339 and model.get("trees") == 120
         and model.get("workerSha256") == EXPECTED_WORKER_SHA256
         and model.get("modelInputSetSha256") == MODEL_INPUT_SET_SHA256
         and model.get("workerRuntimeSetSha256") == WORKER_RUNTIME_SET_SHA256,
         "profile model contract")
    return profile, pin(path, "execution/local4c12g-t14400-profile.json")


def audit_r2_ancestry(roots: Roots, limits: Limits) -> dict[str, Any]:
    bundle = roots.r2_preflight()
    need(
        bundle.is_dir()
        and not bundle.is_symlink()
        and not (hasattr(bundle, "is_junction") and bundle.is_junction()),
        "r2 preflight bundle missing/linked",
    )
    children = list(bundle.iterdir())
    need(all(path.is_file() and not path.is_symlink() for path in children), "r2 bundle has non-regular entry")
    if limits.canonical:
        need({path.name for path in children} == set(R2_PREFLIGHT_INVENTORY), "r2 exact inventory")
    records = []
    for name in sorted(R2_PREFLIGHT_INVENTORY):
        path = bundle / name
        record = pin(path, roots.logical(path))
        if limits.canonical:
            need((record["bytes"], record["sha256"]) == R2_PREFLIGHT_INVENTORY[name], "r2 file pin: " + name)
        records.append(record)
    manifest = json_object(bundle / "manifest.json")
    lock = json_object(bundle / "input-lock.json")
    need(manifest.get("schemaVersion") == "feelm-service-v1-b1-preflight-manifest/1"
         and manifest.get("runId") == R2_RUN_ID
         and manifest.get("status") == "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW"
         and manifest.get("sourceRows") == limits.source_rows
         and manifest.get("logicalRows") == limits.source_rows * 4
         and manifest.get("partitionCount") == 8 and manifest.get("resourceStatus") == "PASS"
         and manifest.get("modelFitPerformed") is False and manifest.get("fitAuthorized") is False,
         "r2 preflight manifest semantics")
    need(lock.get("schemaVersion") == "feelm-service-v1-b1-input-lock/1"
         and lock.get("phase") == "preflight" and lock.get("evaluationTargetsRead") is False
         and lock.get("inputSetSha256") == manifest.get("inputSetSha256")
         and lock.get("controlReferenceSetSha256") == manifest.get("controlReferenceSetSha256"),
         "r2 lock semantics")
    if limits.canonical:
        need(manifest.get("trainingSourceSetSha256") == R2_TRAINING_SOURCE_SET_SHA256
             and lock.get("trainingSourceSetSha256") == R2_TRAINING_SOURCE_SET_SHA256,
             "r2 historical source digest")
    review_path = roots.r2_preflight_review()
    need(review_path.is_file() and not review_path.is_symlink(), "r2 review missing/linked")
    review_pin = pin(review_path, roots.logical(review_path))
    if limits.canonical:
        need((review_pin["bytes"], review_pin["sha256"]) == (R2_PREFLIGHT_REVIEW_BYTES, R2_PREFLIGHT_REVIEW_SHA256), "r2 review pin")
    review = json_object(review_path)
    target = review.get("target")
    need(review.get("schemaVersion") == "feelm-service-v1-b1-result-review/1"
         and review.get("phase") == "preflight" and review.get("status") == "PASS"
         and review.get("readyForService") is False and review.get("modelFitPerformed") is False
         and isinstance(target, dict), "r2 review semantics")
    dependency = review.get("dependencyFingerprint")
    need(isinstance(dependency, dict) and isinstance(dependency.get("files"), dict)
         and isinstance(dependency.get("bundleInventories"), dict), "r2 review dependency closure")
    auditor_pin = {"bytes": 63_141, "sha256": "e23e3e4e7b9830172b5a717a08bf38f5bef415155eb4235440b830c434ba5020"}
    if limits.canonical:
        need(dependency.get("auditorImplementation") == auditor_pin, "r2 auditor historical pin")
        need(pin(roots.r2_auditor()) == auditor_pin, "r2 auditor current-file drift")
        for logical, expected in dependency["files"].items():
            path = roots.resolve_logical(logical)
            need(path.is_file() and not path.is_symlink(), "r2 dependency missing/linked: " + logical)
            need(pin(path) == expected, "r2 dependency current-file drift: " + logical)
        expected_bundle_logical = roots.logical(bundle)
        need(set(dependency["bundleInventories"]) == {expected_bundle_logical}
             and dependency["bundleInventories"][expected_bundle_logical] == inventory(bundle),
             "r2 dependency bundle drift")
        for key, record in target.items():
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                continue
            path = roots.resolve_logical(record["path"])
            observed = pin(path)
            need(observed.get("bytes") == record.get("bytes") and observed.get("sha256") == record.get("sha256"),
                 "r2 review target drift: " + key)
    failure_path = roots.r2_fit_failure()
    need(failure_path.is_file() and not failure_path.is_symlink(), "r2 fit failure missing/linked")
    failure_pin = pin(failure_path, roots.logical(failure_path))
    if limits.canonical:
        need((failure_pin["bytes"], failure_pin["sha256"]) == (R2_FIT_FAILURE_BYTES, R2_FIT_FAILURE_SHA256), "r2 fit failure pin")
    failure = json_object(failure_path)
    run = failure.get("containerRun")
    state = run.get("dockerState") if isinstance(run, dict) else None
    resource = run.get("resource") if isinstance(run, dict) else None
    need(failure.get("schemaVersion") == "feelm-service-v1-b1-failure/1"
         and failure.get("phase") == "fit" and failure.get("status") == "FAILED"
         and failure.get("cleanupComplete") is True and isinstance(run, dict)
         and run.get("timedOut") is True and run.get("workerTerminalResultPresent") is False
         and isinstance(state, dict) and state.get("ExitCode") == 143 and state.get("OOMKilled") is False
         and isinstance(resource, dict) and resource.get("resourceStatus") == "RESOURCE_STOP"
         and resource.get("timedOut") is True, "r2 fit failure semantics")
    need(not os.path.lexists(bundle.with_name(R2_RUN_ID + "-fit")), "r2 failure/success coexist")
    old_runner = roots.standalone / "scripts/run_service_v1_b1_gbt.py"
    old_plan = roots.standalone / "docs/recommendation/plans/service-v1-b1-spark-runner.md"
    old_runner_pin = pin(old_runner, roots.logical(old_runner))
    old_plan_pin = pin(old_plan, "ancestor/service-v1-b1-spark-runner.md")
    if limits.canonical:
        need((old_runner_pin["bytes"], old_runner_pin["sha256"]) == (R2_OUTER_RUNNER_BYTES, R2_OUTER_RUNNER_SHA256), "r2 runner drift")
        need(old_plan_pin["sha256"] == R2_PLAN_SHA256, "r2 plan drift")
    return {"r2PreflightManifest": pin(bundle / "manifest.json", roots.logical(bundle / "manifest.json")),
            "r2PreflightReview": review_pin, "r2PreflightBundleInventory": records,
            "r2PreflightDigests": {"trainingSourceSetSha256": lock.get("trainingSourceSetSha256"),
                "controlReferenceSetSha256": lock.get("controlReferenceSetSha256"),
                "inputSetSha256": lock.get("inputSetSha256"),
                "implementationSetSha256": manifest.get("implementationSetSha256")},
            "r2FitFailure": failure_pin,
            "r2FitFailureFacts": {"phase": "fit", "status": "FAILED", "timedOut": True,
                "resourceStatus": "RESOURCE_STOP", "exitCode": 143, "oomKilled": False,
                "cleanupComplete": True, "workerTerminalResultPresent": False, "modelWritten": False},
            "r2OuterRunner": old_runner_pin, "r2Plan": old_plan_pin}


def r2_control_records(ancestry: Mapping[str, Any]) -> list[dict[str, Any]]:
    records = [dict(record) for record in ancestry["r2PreflightBundleInventory"]]
    records += [dict(ancestry[key]) for key in ("r2PreflightReview", "r2FitFailure", "r2OuterRunner", "r2Plan")]
    return records


def controls_for(roots: Roots, phase: str, limits: Limits) -> list[dict[str, Any]]:
    ancestry = audit_r2_ancestry(roots, limits)
    base = r2_control_records(ancestry)
    if phase == "preflight":
        return base
    preflight = roots.bundle("preflight")
    preflight_records = [pin(preflight / name, roots.logical(preflight / name)) for name in sorted(inventory(preflight))]
    preflight_records.append(pin(roots.review("preflight"), roots.logical(roots.review("preflight"))))
    if phase == "fit":
        return preflight_records + base
    fit = roots.bundle("fit")
    fit_records = [pin(fit / name, roots.logical(fit / name)) for name in sorted(inventory(fit))]
    fit_records.append(pin(roots.review("fit"), roots.logical(roots.review("fit"))))
    return fit_records + preflight_records + base


def expected_recovery_reference(
    roots: Roots, limits: Limits, ancestry: Mapping[str, Any], outer: Mapping[str, Any],
    worker: Mapping[str, Any], profile_pin: Mapping[str, Any], execution_digest: str,
) -> dict[str, Any]:
    return {"schemaVersion": "feelm-service-v1-b1-r3-recovery-reference/1",
            "status": "R2_ANCESTRY_VERIFIED", "runId": RUN_ID, "profileId": PROFILE_ID,
            "r2RunId": R2_RUN_ID, "r2PreflightManifest": dict(ancestry["r2PreflightManifest"]),
            "r2PreflightReview": dict(ancestry["r2PreflightReview"]),
            "r2PreflightBundleInventory": [dict(record) for record in ancestry["r2PreflightBundleInventory"]],
            "r2PreflightDigests": dict(ancestry["r2PreflightDigests"]),
            "r2FitFailure": dict(ancestry["r2FitFailure"]),
            "r2FitFailureFacts": dict(ancestry["r2FitFailureFacts"]),
            "r2OuterRunner": dict(ancestry["r2OuterRunner"]), "r2Plan": dict(ancestry["r2Plan"]),
            "sparkWorker": dict(worker), "outerRunner": dict(outer), "executionProfile": dict(profile_pin),
            "digestComparison": {"r2HistoricalTrainingSourceSetSha256": R2_TRAINING_SOURCE_SET_SHA256,
                "trainingRecipe": pin(roots.sources()["contract/training-recipe.v1.json"], "contract/training-recipe.v1.json"),
                "sourceRows": limits.source_rows, "logicalRows": limits.source_rows * 4,
                "modelInputSetSha256": MODEL_INPUT_SET_SHA256, "workerRuntimeSetSha256": WORKER_RUNTIME_SET_SHA256,
                "executionSetSha256": execution_digest, "modelInputUnchanged": True,
                "workerRuntimeUnchanged": True, "partitionCount": 8, "seed": 339},
            "modelFitPerformedByPreflight": False, "deploymentAuthorized": False}


def audit_recovery_reference(
    roots: Roots, phase: str, manifest: dict[str, Any], pins: dict[str, Any], limits: Limits
) -> dict[str, Any]:
    ancestry = audit_r2_ancestry(roots, limits)
    profile, profile_pin = audit_profile_contract(roots)
    del profile
    expected = expected_recovery_reference(roots, limits, ancestry, pins["outer"], pins["worker"],
                                           profile_pin, pins["executionDigest"])
    path = roots.bundle(phase) / "recovery-reference.json"
    reference = json_object(path)
    need(reference == expected, "r3 recovery reference drift")
    need(manifest.get("runId") == RUN_ID and manifest.get("profileId") == PROFILE_ID, "r3 manifest identity")
    need(manifest.get("r2FitFailureSha256") == R2_FIT_FAILURE_SHA256, "manifest r2 failure pin")
    need(manifest.get("recoveryReferenceSha256") == digest(path), "manifest recovery pin")
    return {"r2FitFailure": ancestry["r2FitFailure"], "reference": pin(path)}


def audit_lock(roots: Roots, phase: str, lock: dict[str, Any], manifest: dict[str, Any],
               expected_outer: str, expected_worker: str, limits: Limits) -> dict[str, Any]:
    schema = "feelm-service-v1-b1-score-input-lock/2" if phase == "score" else "feelm-service-v1-b1-input-lock/2"
    exact_keys = {"schemaVersion", "phase", "runId", "profileId", "modelInputRecords", "workerRuntimeRecords",
                  "executionRecords", "controlReferences", "modelInputSetSha256", "workerRuntimeSetSha256",
                  "executionSetSha256", "controlReferenceSetSha256", "inputSetSha256",
                  "r2TrainingSourceSetSha256", "evaluationTargetsRead"}
    if phase == "score":
        exact_keys |= {"scoreInputRecords", "scoreInputSetSha256"}
    need(set(lock) == exact_keys and lock.get("schemaVersion") == schema and lock.get("phase") == phase,
         "input lock exact schema/fields")
    need(lock.get("runId") == RUN_ID and lock.get("profileId") == PROFILE_ID
         and lock.get("evaluationTargetsRead") is False
         and lock.get("r2TrainingSourceSetSha256") == R2_TRAINING_SOURCE_SET_SHA256,
         "input lock identity/history")
    groups = {"model": record_map(lock["modelInputRecords"]),
              "runtime": record_map(lock["workerRuntimeRecords"]),
              "execution": record_map(lock["executionRecords"])}
    if phase == "score":
        groups["score"] = record_map(lock["scoreInputRecords"])
    need(set(groups["model"]) == MODEL_INPUT_NAMES, "model-input record set")
    if phase == "score":
        need(set(groups["score"]) == SCORE_INPUT_NAMES, "score-input record set")
    need(set(groups["runtime"]) == WORKER_RUNTIME_NAMES, "worker-runtime record set")
    need(set(groups["execution"]) == EXECUTION_NAMES, "execution record set")
    source_paths = roots.sources()
    actual: dict[str, list[dict[str, Any]]] = {name: [] for name in groups}
    for group_name, records in groups.items():
        for name, record in sorted(records.items()):
            if name == "runtime/docker-image-id":
                raw = IMAGE_ID.encode()
                observed = {"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "value": IMAGE_ID}
            else:
                observed = pin(source_paths[name], name)
                if limits.canonical and name in PINNED:
                    need((observed["bytes"], observed["sha256"]) == PINNED[name], "canonical source changed: " + name)
            need(record == observed, "current-file record drift: " + name)
            actual[group_name].append(observed)
    need(actual["runtime"][record_index(actual["runtime"], "implementation/service_v1_b1_spark_worker.py")]["sha256"] == expected_worker,
         "explicit worker pin")
    outer = groups["execution"]["execution/run_service_v1_b1_gbt_r3.py"]
    worker = groups["runtime"]["implementation/service_v1_b1_spark_worker.py"]
    need(outer["sha256"] == expected_outer and worker["sha256"] == expected_worker == EXPECTED_WORKER_SHA256,
         "explicit implementation pins")
    need(groups["execution"]["execution/service-v1-b1-r3-fit-recovery.md"]["sha256"] == RECOVERY_PLAN_SHA256,
         "reviewed recovery plan drift")
    audit_profile_contract(roots)
    model_digest, runtime_digest, execution_digest = (set_digest(actual[name]) for name in ("model", "runtime", "execution"))
    need(model_digest == MODEL_INPUT_SET_SHA256, "model-input invariant digest")
    need(runtime_digest == WORKER_RUNTIME_SET_SHA256, "worker-runtime invariant digest")
    controls = controls_for(roots, phase, limits)
    same_records(lock["controlReferences"], controls, "control references changed/incomplete")
    all_records = actual["model"] + actual.get("score", []) + actual["runtime"] + actual["execution"] + controls
    need(len({record["path"] for record in all_records}) == len(all_records), "record groups overlap")
    expected_digests = {"modelInputSetSha256": model_digest, "workerRuntimeSetSha256": runtime_digest,
                        "executionSetSha256": execution_digest, "controlReferenceSetSha256": set_digest(controls),
                        "inputSetSha256": set_digest(all_records)}
    if phase == "score":
        expected_digests["scoreInputSetSha256"] = set_digest(actual["score"])
    for key, value in expected_digests.items():
        need(lock.get(key) == value and manifest.get(key) == value, "lock/manifest digest: " + key)
    implementation = set_digest([outer, worker])
    for key, value in (("outerRunnerSha256", expected_outer), ("sparkWorkerSha256", expected_worker),
                       ("implementationSetSha256", implementation)):
        need(manifest.get(key) == value, "manifest implementation pin: " + key)
    return {"outer": outer, "worker": worker, "implementation": implementation,
            "model": actual["model"], "score": actual.get("score", []),
            "runtime": actual["runtime"], "execution": actual["execution"],
            "controls": controls, "executionDigest": execution_digest}


def record_index(records: list[dict[str, Any]], logical: str) -> int:
    for index, record in enumerate(records):
        if record.get("path") == logical:
            return index
    raise AuditError("record missing: " + logical)

def audit_inventory(bundle: Path, phase: str, limits: Limits) -> dict[str, Any]:
    before = inventory(bundle)
    required = {"manifest.json", *TARGETS[phase].values()}
    native = {name for name in before if name.startswith("model/native/")} if phase == "fit" else set()
    need(set(before) == required | native, "bundle exact file inventory")
    allowed_dirs = set()
    for name in before:
        parent = PurePosixPath(name).parent
        while str(parent) != ".":
            allowed_dirs.add(str(parent))
            parent = parent.parent
    for path in bundle.rglob("*"):
        relative = path.relative_to(bundle).as_posix()
        need(not path.is_symlink(), "bundle contains a symlink: " + relative)
        if path.is_dir():
            need(relative in allowed_dirs, "bundle contains an unexpected directory: " + relative)
        else:
            need(path.is_file(), "bundle contains a non-regular entry: " + relative)
    need(phase != "fit" or bool(native), "native model files missing")
    manifest = json_object(bundle / "manifest.json")
    common_manifest_keys = {
        "schemaVersion", "runId", "profileId", "status", "createdAt", "files",
        "outerRunnerSha256", "sparkWorkerSha256", "implementationSetSha256",
        "modelInputSetSha256", "workerRuntimeSetSha256", "executionSetSha256",
        "controlReferenceSetSha256", "inputSetSha256", "r2FitFailureSha256",
        "recoveryReferenceSha256", "executionProfileSha256", "resourceStatus", "runtimeVersions",
    }
    phase_manifest_keys = {
        "preflight": {
            "fitAuthorized", "modelFitPerformed", "scorePerformed", "readyForService", "sourceRows",
            "logicalRows", "partitionCount", "r2TrainingSourceSetSha256", "r2PreflightManifestSha256",
            "r2PreflightReviewSha256", "dryRunPrecedesFullMaterialization", "dryRunLogicalRows",
            "partitionIdentitySha256", "identity",
        },
        "fit": {
            "scoringAuthorized", "readyForService", "modelFitPerformed", "scorePerformed", "sourceRows",
            "logicalRows", "partitionCount", "treeCount", "preflightManifestSha256",
            "preflightReviewSha256", "partitionIdentitySha256", "modelInventorySha256",
            "modelFileSetSha256", "thresholdFixtureSha256", "portableParity",
        },
        "score": {
            "evaluationAuthorized", "readyForService", "modelFitPerformed", "scorePerformed", "rows",
            "scoreInputSetSha256", "fitManifestSha256", "fitReviewSha256", "modelInventorySha256",
            "modelFileSetSha256", "predictionSha256", "scoreCensus", "labelProjected",
        },
    }
    need(set(manifest) == common_manifest_keys | phase_manifest_keys[phase], "manifest exact field set")
    need(manifest.get("schemaVersion") == f"feelm-service-v1-b1-{phase}-manifest/2", "manifest schema")
    timestamp(manifest.get("createdAt"))
    need(manifest.get("status") == STATUSES[phase], "manifest completion state")
    need(manifest.get("runId") == RUN_ID, "manifest run ID drift")
    need(manifest.get("profileId") == PROFILE_ID, "manifest profile ID drift")
    need(manifest.get("files") == {k: v for k, v in before.items() if k != "manifest.json"}, "manifest sibling hashes")
    flags = {"preflight": {"fitAuthorized": False, "modelFitPerformed": False, "scorePerformed": False,
                            "readyForService": False},
             "fit": {"scoringAuthorized": False, "readyForService": False, "modelFitPerformed": True, "scorePerformed": False},
             "score": {"evaluationAuthorized": False, "readyForService": False, "modelFitPerformed": False, "scorePerformed": True}}[phase]
    for key, value in flags.items():
        need(manifest.get(key) is value, "unaudited manifest authorization changed: " + key)
    need(manifest.get("resourceStatus") == "PASS", "manifest resource did not pass")
    need(manifest.get("r2FitFailureSha256") == R2_FIT_FAILURE_SHA256, "manifest r2 failure ancestry")
    need(manifest.get("executionProfileSha256") == before["execution-profile.json"]["sha256"], "profile document pin")
    need(manifest.get("recoveryReferenceSha256") == before["recovery-reference.json"]["sha256"], "recovery document pin")
    if phase in ("preflight", "fit"):
        need(manifest.get("sourceRows") == limits.source_rows and manifest.get("logicalRows") == limits.source_rows*4
             and manifest.get("partitionCount") == 8, "manifest training census")
        need(manifest.get("partitionIdentitySha256") == before["partition-identity.json"]["sha256"], "identity digest")
    else:
        need(manifest.get("rows") == limits.score_rows and manifest.get("labelProjected") is False, "score manifest census/label")
        need(manifest.get("predictionSha256") == before["score/predictions.parquet"]["sha256"], "prediction digest")
    if phase == "fit":
        need(manifest.get("treeCount") == limits.trees, "fit tree count")
    return {"inventory": before, "manifest": manifest}


def timestamp(text: Any) -> dt.datetime:
    need(isinstance(text, str), "missing timestamp")
    value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    need(value.tzinfo is not None, "timestamp must include timezone")
    return value


def readonly_mount_map(roots: Roots, phase: str, limits: Limits) -> dict[str, Path]:
    paths = roots.sources()
    names = {"/app/run_service_v1_b1_gbt_r3.py": "execution/run_service_v1_b1_gbt_r3.py",
             "/app/service_v1_b1_spark_worker.py": "implementation/service_v1_b1_spark_worker.py",
             "/contract/service-v1-b1-r3-fit-recovery.md": "execution/service-v1-b1-r3-fit-recovery.md",
             "/contract/execution-profile-contract.json": "execution/local4c12g-t14400-profile.json",
             "/contract/feature-schema.v1.json": "contract/feature-schema.v1.json"}
    if phase == "score":
        names["/input/natural-score.parquet"] = "source/natural-score.parquet"
    else:
        for name in ("training-recipe.v1.json", "service-v1.json", "MODELS.md"):
            names["/contract/" + name] = "contract/" + name
        names["/input/natural-train.parquet"] = "source/natural-train.parquet"
        names["/masked"] = "source/tmdb-masked-train.parquet"
        names["/review/masked-result-review.json"] = "source/masked-review.json"
    result = {target: paths[name].resolve() for target, name in names.items()}
    if phase != "score":
        result["/masked"] = paths["source/tmdb-masked-train.parquet"].parent.resolve()
    if phase == "fit":
        controls = controls_for(roots, phase, limits)
        for index, record in enumerate(controls):
            path = roots.resolve_logical(record["path"])
            result[f"/control/fit-input/{index:02d}-{path.name}"] = path.resolve()
    elif phase == "score":
        result.update({"/fit": roots.bundle("fit").resolve(),
                       "/control/fit-review.json": roots.review("fit").resolve()})
        controls = controls_for(roots, phase, limits)
        for index, record in enumerate(controls):
            path = roots.resolve_logical(record["path"])
            result[f"/control/score-input/{index:03d}-{path.name}"] = path.resolve()
    return result

def audit_resource_and_commands(roots: Roots, phase: str, manifest: dict[str, Any], limits: Limits) -> dict[str, Any]:
    bundle = roots.bundle(phase)
    command, resource = json_object(bundle / "command.json"), json_object(bundle / "resource.json")
    profile, profile_pin = audit_profile_contract(roots)
    profile_document = json_object(bundle / "execution-profile.json")
    need(profile_document == {"schemaVersion": "feelm-service-v1-b1-phase-execution-profile/1",
         "runId": RUN_ID, "profileId": PROFILE_ID, "phase": phase, "profileContract": profile_pin,
         "outerRunner": pin(roots.sources()["execution/run_service_v1_b1_gbt_r3.py"], "execution/run_service_v1_b1_gbt_r3.py"),
         "executionSetSha256": manifest.get("executionSetSha256"), "resolved": profile}, "phase execution profile")
    need(command.get("schemaVersion") == "feelm-service-v1-b1-command/2"
         and command.get("phase") == phase and command.get("runId") == RUN_ID
         and command.get("profileId") == PROFILE_ID and command.get("timeoutSeconds") == 14400,
         "command schema/identity/timeout")
    need(command.get("resources") == {"dockerCpus": "4", "dockerMemory": "12g",
         "dockerMemorySwap": "12g", "sparkMaster": "local[4]", "sparkDriverMemory": "8g",
         "shufflePartitions": 8, "adaptiveExecution": False}, "command resolved resources")
    need(command.get("dockerImage") == "feelm-rec046-spark:local" and command.get("dockerImageId") == IMAGE_ID, "command Docker image")
    created_at = timestamp(command.get("createdAt"))
    need(resource.get("schemaVersion") == "feelm-service-v1-b1-resource/1" and resource.get("phase") == phase
         and resource.get("status") == "PASS" and resource.get("limitBytes") == MAX_MEMORY_BYTES, "resource envelope")
    actions = ["dry-run-first-row-group", "preflight"] if phase == "preflight" else [phase]
    sequence, stages = command.get("sequence"), resource.get("stages")
    need(isinstance(sequence, list) and isinstance(stages, list) and len(sequence) == len(stages) == len(actions), "stage count")
    logs = []
    for line in (bundle / "run.log").read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("schemaVersion") == "feelm-service-v1-b1-spark-worker/1":
            logs.append(row)
    need(len(logs) == len(actions), "run log terminal worker result count")
    paths = roots.sources()
    expected_mounts = readonly_mount_map(roots, phase, limits)
    train_allowed = set(expected_mounts.values())
    score_allowed = set(expected_mounts.values())
    previous_end = None
    peaks = []
    for index, (action, entry, stage, worker) in enumerate(zip(actions, sequence, stages, logs)):
        need(entry.get("order") == index and entry.get("workerAction") == stage.get("workerAction") == action
             and entry.get("timeoutSeconds") == 14400, "worker action order/timeout")
        status = {"dry-run-first-row-group": "DRY_RUN_ONLY_NOT_PUBLISHED", "preflight": "B1_FULL_PREFLIGHT_WORKER_COMPLETE",
                  "fit": "B1_MODEL_FIT_WORKER_COMPLETE", "score": "B1_NATURAL_SCORE_WORKER_COMPLETE"}[action]
        need(stage.get("workerStatus") == worker.get("status") == status, "worker result status")
        need(worker.get("modelFitPerformed") is (action == "fit") and worker.get("scorePerformed") is (action == "score"), "worker phase side-effect flags")
        versions = worker.get("runtimeVersions", {})
        need(versions == manifest.get("runtimeVersions") and versions.get("sparkVersion") == "4.1.3"
             and isinstance(versions.get("javaVersion"), str) and versions["javaVersion"].split(".")[0] == "21"
             and isinstance(versions.get("pythonVersion"), str) and bool(versions["pythonVersion"]), "runtime version pin/log mismatch")
        start, end = timestamp(stage.get("startedAt")), timestamp(stage.get("completedAt"))
        elapsed = stage.get("elapsedSeconds")
        need(type(elapsed) in (int, float) and math.isfinite(elapsed) and 0 <= elapsed <= 14400 and end >= start, "stage timeout/duration")
        wall_seconds = (end-start).total_seconds()
        # Both clocks must independently fit the cap. A wall-clock adjustment
        # larger than the fixed two-second timestamp/call overhead is BLOCK.
        need(start >= created_at and 0 <= wall_seconds <= 14400
             and abs(wall_seconds-elapsed) <= CLOCK_TOLERANCE_SECONDS, "stage wall/monotonic clock inconsistency or timeout")
        need(previous_end is None or start >= previous_end, "stage overlap: dry-run must stop before full")
        previous_end = end
        observed, cleanup = stage.get("resource", {}), stage.get("cleanup", {})
        peak = observed.get("peakBytes")
        need(observed.get("resourceStatus") == "PASS" and type(peak) is int and 0 <= peak <= MAX_MEMORY_BYTES
             and observed.get("limitBytes") == MAX_MEMORY_BYTES and observed.get("oomKilled") is False
             and type(observed.get("exitCode")) is int and observed["exitCode"] == 0 and observed.get("timedOut") is False,
             "resource unknown/failed/stop")
        raw = worker.get("resourceObservation", {})
        host = observed.get("hostObservation")
        need(isinstance(host, dict) and host.get("schemaVersion") == "feelm-service-v1-b1-host-cgroup-observation/1"
             and host.get("status") == "OBSERVED" and type(host.get("sampleCount")) is int
             and host["sampleCount"] >= 1 and type(host.get("peakBytes")) is int
             and host.get("peakSource") in {"cgroup-v1", "cgroup-v2"}
             and isinstance(host.get("firstSample"), dict) and isinstance(host.get("lastSample"), dict)
             and isinstance(host.get("maximumSample"), dict), "host cgroup periodic observation missing")
        need(type(raw.get("peakBytes")) is int and type(host.get("peakBytes")) is int, "worker/host peak types")
        need(raw.get("resourceStatus") == "PASS" and observed.get("workerPeakBytes") == raw.get("peakBytes")
             and observed.get("hostPeakBytes") == host.get("peakBytes")
             and peak == max(raw.get("peakBytes"), host.get("peakBytes"))
             and observed.get("peakSource") == "host-cgroup-periodic+worker-terminal", "worker/host peak merge mismatch")
        need(cleanup.get("confirmedStopped") is True and cleanup.get("removed") is True, "container cleanup unconfirmed")
        args = entry.get("command")
        need(isinstance(args, list) and all(isinstance(x, str) for x in args) and args[:2] == ["docker", "create"], "command argv")
        prefix = ["docker", "create", "--name", stage.get("containerName"), "--network", "none",
                  "--hostname", "service-v1-b1", "--add-host", "service-v1-b1:127.0.0.1", "-e", "SPARK_LOCAL_IP=127.0.0.1",
                  "--cpus", "4", "--memory", "12g", "--memory-swap", "12g"]
        need(args[:len(prefix)] == prefix and args.count("feelm-rec046-spark:local") == 1, "exact Docker argv prefix/image required")
        image_index = args.index("feelm-rec046-spark:local")
        mount_args = args[len(prefix):image_index]
        need(len(mount_args) % 2 == 0 and all(mount_args[i] == "--mount" for i in range(0, len(mount_args), 2)),
             "unknown Docker flag or alternate mount syntax")
        spark_prefix = ["/opt/spark/bin/spark-submit", "--master", "local[4]", "--driver-memory", "8g",
                        "--conf", "spark.sql.shuffle.partitions=8", "--conf", "spark.sql.adaptive.enabled=false",
                        "--conf", "spark.ui.enabled=false", "--conf", "spark.sql.debug.maxToStringFields=1000",
                        "--conf", "spark.local.dir=/scratch/spark-local", "/app/service_v1_b1_spark_worker.py"]
        need(args[image_index+1:image_index+1+len(spark_prefix)] == spark_prefix, "exact Spark argv required")
        for flag, value in (("--name", stage.get("containerName")), ("--network", "none"), ("--cpus", "4"),
                            ("--memory", "12g"), ("--memory-swap", "12g"), ("--master", "local[4]"), ("--driver-memory", "8g")):
            need(args.count(flag) == 1 and args[args.index(flag)+1] == value, "command flag drift: " + flag)
        for required in ("spark.sql.shuffle.partitions=8", "spark.sql.adaptive.enabled=false", "spark.ui.enabled=false", "spark.local.dir=/scratch/spark-local"):
            need(required in args, "Spark configuration missing: " + required)
        worker_index = args.index("/app/service_v1_b1_spark_worker.py")
        need(args[worker_index+1] == action and args.count("feelm-rec046-spark:local") == 1, "worker invocation drift")
        if phase == "score":
            tail = ["score", "--score-input", "/input/natural-score.parquet", "--model-dir", "/fit/model/native", "--output", "/output", "--scratch", "/scratch"]
        else:
            tail = [action, "--natural", "/input/natural-train.parquet", "--masked", "/masked/tmdb-masked-rh230.parquet",
                    "--masked-manifest", "/masked/manifest.json", "--views-manifest", "/masked/views-manifest.json",
                    "--masked-review", "/review/masked-result-review.json", "--scratch", "/scratch"]
            if action != "dry-run-first-row-group":
                tail += ["--training-recipe", "/contract/training-recipe.v1.json", "--output", "/output"]
        need(args[worker_index+1:] == tail, "worker input/output argv drift")
        conf = [args[i+1] for i, x in enumerate(args) if x == "--conf"]
        need(len({x.split("=", 1)[0] for x in conf}) == len(conf), "duplicate Spark config override")
        seen_ro, seen_targets = set(), set()
        for i in range(0, len(mount_args), 2):
            pieces = mount_args[i+1].split(",")
            mount = dict(piece.split("=", 1) for piece in pieces if "=" in piece)
            need(mount.get("type") == "bind" and set(mount) == {"type", "source", "target"}, "mount specification")
            need(pieces == ["type=bind", "source=" + mount["source"], "target=" + mount["target"]] + (["readonly"] if "readonly" in pieces else []),
                 "unknown or duplicate mount options")
            target = mount["target"]
            need(target not in seen_targets, "duplicate mount target")
            seen_targets.add(target)
            path = Path(mount["source"])
            need(path.name.casefold() not in FORBIDDEN and "text339" not in [p.casefold() for p in path.parts], "evaluation mount forbidden")
            if "readonly" in pieces:
                allowed = score_allowed if phase == "score" else train_allowed
                need(path.resolve() in allowed, "read mount outside exact allowlist")
                need(expected_mounts.get(target) == path.resolve(), "read mount source/target swapped")
                seen_ro.add(path.resolve())
            else:
                need(target in ("/scratch", "/output") and path.parent.resolve() == bundle.parent.resolve()
                     and path.name.startswith("." + bundle.name + "."), "write mount outside phase staging")
                need(not os.path.lexists(path), "scratch/staging cleanup incomplete")
        need(seen_ro == (score_allowed if phase == "score" else train_allowed), "missing read-only mount")
        need("/scratch" in seen_targets and ("/output" in seen_targets) == (action != "dry-run-first-row-group"), "dry run must not publish output")
        if action == "dry-run-first-row-group":
            need(worker.get("modelFitPerformed") is False and worker.get("scorePerformed") is False, "dry-run performed model work")
            if limits.canonical:
                need(worker.get("logicalRows") == 16384, "canonical RG0 dry-run census")
        peaks.append(peak)
    if phase == "preflight":
        need(command.get("dryRunPrecedesFullMaterialization") is True and manifest.get("dryRunPrecedesFullMaterialization") is True, "outer dry-run gate missing")
        if limits.canonical:
            need(manifest.get("dryRunLogicalRows") == 16384, "manifest RG0 census")
    need(not os.path.lexists(bundle.with_name(bundle.name + "-failure.json")), "phase failure record exists")
    stale = [path for path in bundle.parent.iterdir() if path.name.startswith(("." + bundle.name + ".tmp-",
             "." + roots.review(phase).name + ".tmp-", "." + bundle.with_name(bundle.name + "-failure.json").name + ".tmp-"))
             or (path.name.startswith("." + bundle.name + ".") and "-scratch-" in path.name)]
    need(not stale, "stale phase staging/scratch/review temp")
    return {"stages": len(stages), "peakBytes": max(peaks), "cleanupConfirmed": True, "labelMountAbsent": True,
            "measurementIndependentlyObserved": False, "measurementCheckedAgainstPinnedWorkerLog": True}


def target_records(roots: Roots, phase: str) -> dict[str, dict[str, Any]]:
    bundle = roots.bundle(phase)
    files = {"manifest": bundle / "manifest.json", **{k: bundle / v for k, v in TARGETS[phase].items()},
             "outer_runner": roots.standalone / "scripts/run_service_v1_b1_gbt_r3.py",
             "spark_worker": roots.standalone / "scripts/service_v1_b1_spark_worker.py"}
    result = {key: pin(path, roots.logical(path)) for key, path in files.items()}
    name = "score_input_lock" if phase == "score" else "input_lock"
    lock = json_object(files[name])
    result[name]["inputSetSha256"] = lock["inputSetSha256"]
    for key in ("modelInputSetSha256", "workerRuntimeSetSha256", "executionSetSha256",
                "controlReferenceSetSha256"):
        result[name][key] = lock[key]
    if phase == "score":
        result[name]["scoreInputSetSha256"] = lock["scoreInputSetSha256"]
    return result


def audit_review_shape(review: Mapping[str, Any], phase: str, auditor_pin: Mapping[str, Any]) -> None:
    need(set(review) == {"schemaVersion", "runId", "profileId", "phase", "status", "createdAt", "target",
         "reviewer", "dependencyFingerprint", "checks", "evaluationTargetsRead", "modelFitPerformed",
         "readyForService", "scope"}, "review exact field set")
    need(review.get("schemaVersion") == "feelm-service-v1-b1-r3-result-review/1"
         and review.get("runId") == RUN_ID and review.get("profileId") == PROFILE_ID
         and review.get("status") == "PASS" and review.get("phase") == phase
         and review.get("evaluationTargetsRead") is False and review.get("modelFitPerformed") is False
         and review.get("readyForService") is False, "parent independent PASS review missing")
    timestamp(review.get("createdAt"))
    need(review.get("scope") == "Local immutable bundle integrity and specified numerical parity; no service acceptance or model quality verdict.",
         "review scope drift")
    need(review.get("reviewer") == {"implementation": dict(auditor_pin), "independentFromRunner": True},
         "parent review auditor identity")
    checks = review.get("checks")
    expected_keys = {"preflight": {"resource", "recovery", "identity"},
                     "fit": {"resource", "recovery", "parentChain", "identity", "fit"},
                     "score": {"resource", "recovery", "parentChain", "predictions"}}[phase]
    need(isinstance(checks, dict) and set(checks) == expected_keys, "review check set drift")
    resource = checks.get("resource")
    need(isinstance(resource, dict) and set(resource) == {"stages", "peakBytes", "cleanupConfirmed",
         "labelMountAbsent", "measurementIndependentlyObserved", "measurementCheckedAgainstPinnedWorkerLog"}
         and resource.get("stages") == (2 if phase == "preflight" else 1)
         and type(resource.get("peakBytes")) is int and 0 < resource["peakBytes"] < MAX_MEMORY_BYTES
         and resource.get("cleanupConfirmed") is True and resource.get("labelMountAbsent") is True
         and resource.get("measurementIndependentlyObserved") is False
         and resource.get("measurementCheckedAgainstPinnedWorkerLog") is True,
         "review resource/cleanup check drift")
    need(isinstance(checks.get("recovery"), dict), "review recovery check missing")
    if phase != "preflight":
        need(checks.get("parentChain") == "PASS", "review parent chain did not pass")
    if phase in ("preflight", "fit"):
        need(isinstance(checks.get("identity"), dict), "review identity check missing")
    if phase == "fit":
        need(isinstance(checks.get("fit"), dict), "fit review model audit missing")
    if phase == "score":
        need(isinstance(checks.get("predictions"), dict), "score review prediction audit missing")


def audit_sibling_review(roots: Roots, phase: str, limits: Limits) -> dict[str, Any]:
    review = json_object(roots.review(phase))
    auditor_pin = pin(Path(__file__).resolve())
    audit_review_shape(review, phase, auditor_pin)
    need(review.get("target") == target_records(roots, phase), "parent review target pins changed/incomplete")
    need(review.get("dependencyFingerprint") == dependency_fingerprint(roots, phase, limits),
         "parent review dependency closure drift")
    return review


def audit_parent(roots: Roots, phase: str, expected_outer: str, expected_worker: str, limits: Limits) -> dict[str, Any]:
    need(phase in ("preflight", "fit"), "invalid parent phase")
    envelope = audit_inventory(roots.bundle(phase), phase, limits)
    review = audit_sibling_review(roots, phase, limits)
    lock = json_object(roots.bundle(phase) / "input-lock.json")
    pins = audit_lock(roots, phase, lock, envelope["manifest"], expected_outer, expected_worker, limits)
    audit_resource_and_commands(roots, phase, envelope["manifest"], limits)
    audit_recovery_reference(roots, phase, envelope["manifest"], pins, limits)
    if phase == "fit":
        earlier = audit_parent(roots, "preflight", expected_outer, expected_worker, limits)
        audit_reference(roots, "fit", envelope["manifest"], lock, pins, earlier, limits)
        audit_model_inventory(roots.bundle("fit"), envelope["manifest"])
    return {**envelope, "review": review, "lock": lock,
            "manifestPin": pin(roots.bundle(phase) / "manifest.json", roots.logical(roots.bundle(phase) / "manifest.json")),
            "reviewPin": pin(roots.review(phase), roots.logical(roots.review(phase)))}

def audit_model_inventory(bundle: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    value = json_object(bundle / "model-file-inventory.json")
    need(value.get("schemaVersion") == "feelm-service-v1-b1-model-inventory/1", "model inventory schema")
    native = inventory(bundle / "model/native")
    need(bool(native), "empty native model")
    records = [{"path": key, **p} for key, p in sorted(native.items())]
    same_records(value.get("files"), records, "native inventory file drift")
    need(value.get("inventorySha256") == manifest.get("modelFileSetSha256") == set_digest(records), "model file set digest")
    need(manifest.get("modelInventorySha256") == digest(bundle / "model-file-inventory.json"), "model inventory file pin")
    return value


def embedded_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = (json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "payload": dict(value)}


def audit_reference(roots: Roots, phase: str, manifest: dict[str, Any], lock: dict[str, Any],
                    pins: dict[str, Any], parent: dict[str, Any], limits: Limits) -> None:
    previous = "preflight" if phase == "fit" else "fit"
    path = roots.bundle(phase) / (previous + "-reference.json")
    reference = json_object(path)
    profile_pin = pin(roots.profile_contract(), "execution/local4c12g-t14400-profile.json")
    recovery = json_object(roots.bundle(phase) / "recovery-reference.json")
    expected = {"schemaVersion": f"feelm-service-v1-b1-{previous}-reference/2",
                "runId": RUN_ID, "profileId": PROFILE_ID,
                previous + "Manifest": parent["manifestPin"], previous + "Review": parent["reviewPin"],
                "outerRunner": pins["outer"], "sparkWorker": pins["worker"],
                "executionProfile": profile_pin, "reviewedArtifacts": pins["controls"]}
    expected["recoveryReference"] = embedded_payload(recovery)
    for key in (previous + "ManifestSha256", previous + "ReviewSha256"):
        reference_key = "manifestPin" if "Manifest" in key else "reviewPin"
        need(manifest.get(key) == parent[reference_key]["sha256"], "parent manifest/review pin")
    if phase == "fit":
        expected.update({"implementationSetSha256": pins["implementation"],
                         "modelInputSetSha256": lock["modelInputSetSha256"],
                         "workerRuntimeSetSha256": lock["workerRuntimeSetSha256"],
                         "executionSetSha256": lock["executionSetSha256"],
                         "fitInputSetSha256": lock["inputSetSha256"]})
        for key in ("modelInputSetSha256", "workerRuntimeSetSha256", "executionSetSha256"):
            need(lock[key] == parent["lock"][key], "preflight/fit invariant group mismatch: " + key)
        need(lock["inputSetSha256"] != parent["lock"]["inputSetSha256"], "phase-specific input sets collapsed")
        need(json_object(roots.bundle("fit") / "partition-identity.json") == json_object(roots.bundle("preflight") / "partition-identity.json"),
             "fit/preflight identity mismatch")
    else:
        model_inventory = audit_model_inventory(roots.bundle("fit"), parent["manifest"])
        model_path = roots.bundle("fit") / "model-file-inventory.json"
        expected.update({"modelInventory": pin(model_path, roots.logical(model_path)),
                         "modelFileSetSha256": model_inventory["inventorySha256"],
                         "modelFiles": model_inventory["files"],
                         "modelInputSetSha256": lock["modelInputSetSha256"],
                         "workerRuntimeSetSha256": lock["workerRuntimeSetSha256"],
                         "executionSetSha256": lock["executionSetSha256"],
                         "scoreInputSetSha256": lock["scoreInputSetSha256"],
                         "scorePhaseInputSetSha256": lock["inputSetSha256"]})
        need(lock["workerRuntimeSetSha256"] == parent["lock"]["workerRuntimeSetSha256"]
             and lock["executionSetSha256"] == parent["lock"]["executionSetSha256"],
             "fit/score runtime or execution drift")
        need(manifest.get("modelInventorySha256") == digest(model_path)
             and manifest.get("modelFileSetSha256") == model_inventory["inventorySha256"], "score/fit native model link")
    need(reference == expected, "audited parent reference drift")

def audit_fit(bundle: Path, roots: Roots, manifest: dict[str, Any], limits: Limits) -> dict[str, Any]:
    audit_model_inventory(bundle, manifest)
    model = NativeTrees(bundle / "model/native", limits.trees)
    resolved = json_object(bundle / "resolved-estimator.json")
    recipe = json_object(roots.sources()["contract/training-recipe.v1.json"])
    need(resolved == recipe.get("models", {}).get("GBT", {}).get("parameters"), "resolved estimator differs from pinned recipe")
    model_params = model.metadata.get("paramMap", {})
    for key, value in resolved.items():
        need(model_params.get(key) == value, "native model parameter differs: " + key)
    if "stepSize" in resolved:
        need([w for w, _ in model.trees] == [1.] + [float(resolved["stepSize"])]*(limits.trees-1), "GBT tree weight/step size drift")
    parity = audit_thresholds(bundle / "threshold-fixtures.npz", model)
    metrics = json_object(bundle / "fit-metrics.json")
    need(metrics.get("schemaVersion") == "feelm-service-v1-b1-fit-metrics/1" and metrics.get("model") == "GBT120_s339_B1", "fit metrics schema/model")
    for key, expected in (("sourceRows", limits.source_rows), ("logicalRows", limits.source_rows*4), ("viewWeight", .25),
                          ("treeCount", limits.trees), ("nonLeafSplitCount", parity["nonLeafSplitCount"]), ("thresholdFixtureRows", parity["rows"])):
        need(metrics.get(key) == expected, "fit metric census: " + key)
    for key in ("weightedRawRmse", "fitSeconds", "workerSeconds"):
        need(type(metrics.get(key)) in (int, float) and math.isfinite(metrics[key]) and metrics[key] >= 0, "invalid fit metric: " + key)
    reported = metrics.get("portableParity", {})
    need(isinstance(reported, dict) and reported.get("status") == "PASS" and reported.get("tolerance") == 1e-6
         and reported.get("fixtureRows") == parity["rows"] and reported.get("nonLeafSplits") == parity["nonLeafSplitCount"], "reported parity envelope")
    error = reported.get("maximumAbsoluteError")
    need(type(error) in (int, float) and math.isfinite(error) and 0 <= error <= 1e-6, "reported parity error")
    need(manifest.get("portableParity") == reported and manifest.get("thresholdFixtureSha256") == digest(bundle / "threshold-fixtures.npz"), "manifest threshold parity")
    return {"treeCount": len(model.trees), "thresholdParity": parity, "weightedTrainRmseIndependentlyRecomputed": False,
            "trainingQualityNotAnAcceptanceMetric": True}


def dependency_fingerprint(
    roots: Roots,
    phase: str,
    limits: Limits,
    *,
    ignored_temporary: Path | None = None,
) -> dict[str, Any]:
    """Re-hash the complete r3 phase closure and immutable r2 ancestry."""
    need(phase in TARGETS, "unknown dependency phase")
    phases = ["preflight"] if phase == "preflight" else (["preflight", "fit"] if phase == "fit" else ["preflight", "fit", "score"])
    source_names = MODEL_INPUT_NAMES | WORKER_RUNTIME_NAMES | EXECUTION_NAMES
    if phase == "score":
        source_names |= SCORE_INPUT_NAMES
    sources = roots.sources()
    files = {roots.logical(sources[name]): pin(sources[name]) for name in sorted(source_names)
             if name != "runtime/docker-image-id"}
    ancestry = audit_r2_ancestry(roots, limits)
    for record in r2_control_records(ancestry):
        logical = record["path"]
        path = roots.resolve_logical(logical)
        if path.parent.resolve() != roots.r2_preflight().resolve():
            files[logical] = pin(path)
    bundles = {roots.logical(roots.r2_preflight()): inventory(roots.r2_preflight())}
    for current in phases:
        bundle = roots.bundle(current)
        bundles[roots.logical(bundle)] = inventory(bundle)
        need(not os.path.lexists(bundle.with_name(bundle.name + "-failure.json")), "dependency phase has failure record")
        stale = [path for path in bundle.parent.iterdir() if path != ignored_temporary and path.name.startswith(("." + bundle.name + ".tmp-",
                 "." + roots.review(current).name + ".tmp-", "." + bundle.with_name(bundle.name + "-failure.json").name + ".tmp-"))
                 or (path.name.startswith("." + bundle.name + ".") and "-scratch-" in path.name)]
        need(not stale, "dependency phase has stale temp/scratch")
        if current != phase:
            files[roots.logical(roots.review(current))] = pin(roots.review(current))
    return {"files": files, "bundleInventories": bundles,
            "auditorImplementation": pin(Path(__file__).resolve()), "dockerImageId": IMAGE_ID,
            "runId": RUN_ID, "profileId": PROFILE_ID}

def audit_phase(roots: Roots, phase: str, expected_outer: str, expected_worker: str,
                *, _limits: Limits | None = None) -> dict[str, Any]:
    """Audit without writing. `_limits` is exclusively for synthetic unit fixtures."""
    limits = _limits or Limits()
    for value in (expected_outer, expected_worker):
        need(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), "explicit lowercase implementation SHA256 required")
    bundle = roots.bundle(phase)
    need(bundle.is_dir() and not bundle.is_symlink()
         and not (hasattr(bundle, "is_junction") and bundle.is_junction()), "canonical immutable bundle missing/linked")
    dependencies = dependency_fingerprint(roots, phase, limits)
    envelope = audit_inventory(bundle, phase, limits)
    manifest = envelope["manifest"]
    lock_name = "score-input-lock.json" if phase == "score" else "input-lock.json"
    lock = json_object(bundle / lock_name)
    pins = audit_lock(roots, phase, lock, manifest, expected_outer, expected_worker, limits)
    checks = {"resource": audit_resource_and_commands(roots, phase, manifest, limits),
              "recovery": audit_recovery_reference(roots, phase, manifest, pins, limits)}
    if phase != "preflight":
        parent = audit_parent(roots, "preflight" if phase == "fit" else "fit", expected_outer, expected_worker, limits)
        audit_reference(roots, phase, manifest, lock, pins, parent, limits)
        checks["parentChain"] = "PASS"
    if phase in ("preflight", "fit"):
        sources = roots.sources()
        identity = recalculate_identity(sources["source/natural-train.parquet"], sources["source/tmdb-masked-train.parquet"], limits)
        need(json_object(bundle / "partition-identity.json") == identity, "independently recalculated partition identity mismatch")
        checks["identity"] = {"sourceRows": identity["sourceRows"], "logicalRows": identity["logicalRows"], "partitions": 8,
                              "allSourceRowsRead": True, "maskedFormulaRecomputed": False, "maskedArtifactPinsChecked": True}
        if phase == "fit":
            checks["fit"] = audit_fit(bundle, roots, manifest, limits)
    else:
        model = NativeTrees(roots.bundle("fit") / "model/native", limits.trees)
        plan = (bundle / "score/analyzed-plan.txt").read_text(encoding="utf-8")
        need("label" not in plan.casefold(), "score analyzed plan contains label")
        names = set(re.findall(r"\b(?:row_id|uid|x\d{3})\b", plan))
        need(names == {"row_id", "uid", *FEATURES} and "features" in plan, "score analyzed plan must expose complete 232-column projection")
        checks["predictions"] = audit_predictions(roots.sources()["source/natural-score.parquet"], bundle / "score/predictions.parquet", model, limits)
        census = manifest.get("scoreCensus", {})
        for key, value in (("rows", limits.score_rows), ("rowIdMinimum", 0), ("rowIdMaximum", limits.score_rows-1),
                           ("rowIdsUnique", True), ("uidAxisEqual", True), ("predictionsFinite", True), ("singlePhysicalParquetFile", True)):
            need(census.get(key) == value, "score manifest census: " + key)
    need(inventory(bundle) == envelope["inventory"], "bundle mutated during audit")
    audit_lock(roots, phase, lock, manifest, expected_outer, expected_worker, limits)
    audit_recovery_reference(roots, phase, manifest, pins, limits)
    need(dependency_fingerprint(roots, phase, limits) == dependencies, "audited dependency changed during audit")
    return {"schemaVersion": "feelm-service-v1-b1-r3-result-review/1", "runId": RUN_ID,
            "profileId": PROFILE_ID, "phase": phase, "status": "PASS",
            "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(), "target": target_records(roots, phase),
            "reviewer": {"implementation": pin(Path(__file__).resolve()), "independentFromRunner": True},
            "dependencyFingerprint": dependencies, "checks": checks, "evaluationTargetsRead": False,
            "modelFitPerformed": False, "readyForService": False,
            "scope": "Local immutable bundle integrity and specified numerical parity; no service acceptance or model quality verdict."}

def rename_no_replace(source: Path, target: Path) -> None:
    """Atomic no-clobber rename; refuse an unsupported platform instead of replacing."""
    if os.name == "nt":
        os.rename(source, target)  # Windows MoveFile semantics reject an existing target.
        return
    import ctypes
    library = ctypes.CDLL(None, use_errno=True)
    function = getattr(library, "renameat2", None)
    need(function is not None, "atomic no-replace rename unavailable on this platform")
    result = function(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def publish_review(roots: Roots, phase: str, review: dict[str, Any]) -> Path:
    limits = Limits()
    audit_review_shape(review, phase, pin(Path(__file__).resolve()))
    need(review.get("target") == target_records(roots, phase), "bundle changed before review publication")
    need(review.get("dependencyFingerprint") == dependency_fingerprint(roots, phase, limits), "audited dependency changed before review publication")
    destination = roots.review(phase)
    need(not os.path.lexists(destination), "immutable result review already exists")
    temporary = destination.with_name("." + destination.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write((json.dumps(review, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        need(json_object(temporary) == review, "serialized review validation failed")
        # Check the entire closure again immediately before the no-clobber
        # rename; writing the temporary review must not open a mutation window.
        need(
            review["dependencyFingerprint"]
            == dependency_fingerprint(roots, phase, limits, ignored_temporary=temporary),
            "audited dependency changed immediately before publication",
        )
        rename_no_replace(temporary, destination)
        if os.name != "nt":
            descriptor = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()
    return destination


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--standalone-root", type=Path, required=True)
    result.add_argument("--team-repo", type=Path, required=True)
    subcommands = result.add_subparsers(dest="phase", required=True)
    for phase in TARGETS:
        command = subcommands.add_parser(phase, help="Independently audit the existing immutable " + phase + " bundle")
        command.add_argument("--expected-outer-runner-sha256", required=True)
        command.add_argument("--expected-spark-worker-sha256", required=True)
        command.add_argument("--publish-review", action="store_true", help="Atomically create the canonical sibling review after PASS")
    return result


def main() -> None:
    args = parser().parse_args()
    roots = Roots(args.standalone_root.resolve(), args.team_repo.resolve())
    try:
        review = audit_phase(roots, args.phase, args.expected_outer_runner_sha256, args.expected_spark_worker_sha256)
        path = publish_review(roots, args.phase, review) if args.publish_review else None
        print(json.dumps({"status": "PASS", "phase": args.phase, "reviewPublished": path is not None,
                          "review": str(path) if path else None}, sort_keys=True))
    except Exception as error:
        print(json.dumps({"status": "BLOCK", "phase": args.phase, "error": str(error)}, sort_keys=True))
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
