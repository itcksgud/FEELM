import csv
import hashlib
import json
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import gbt_zero_n_build_input as base
from fm_v4_build_input import (
    Event,
    build,
    choose_targets,
    episode_row,
    file_pin,
    load_prior_validation_users,
    recent_n_values,
    user_digest,
)
from fm_v4_isolate_input import isolate


def find_uid(role: str, used: set[int]) -> int:
    for uid in range(1, 100000):
        if uid not in used and base.role_for_user(4623, uid) == role:
            used.add(uid)
            return uid
    raise AssertionError(f"could not find uid for {role}")


class FMV4InputTest(unittest.TestCase):
    def write_prior(self, root: Path, seed: int, users: list[int]) -> None:
        root.mkdir(exist_ok=True)
        manifest = {
            "seed": seed,
            "users": {"VALIDATION": len(users)},
            "user_partition": {"role_user_digests": {"VALIDATION": user_digest(users)}},
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        rows = [json.dumps({"role": "VALIDATION", "uid": uid}) for uid in users]
        (root / "episodes.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    def make_fixture(self, root: Path) -> tuple[Namespace, Path, dict]:
        ml = root / "ml"
        ml.mkdir()
        with (ml / "movies.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["movieId", "title", "genres"])
            writer.writeheader()
            for movie_id in (1, 2, 3):
                writer.writerow({"movieId": movie_id, "title": f"M{movie_id} (2010)", "genres": "Drama"})
        (ml / "tags.csv").write_text("userId,movieId,tag,timestamp\n", encoding="utf-8")

        used: set[int] = set()
        train_uid = find_uid("TRAIN", used)
        fresh_validation_uid = find_uid("VALIDATION", used)
        excluded_validation_uid = find_uid("VALIDATION", used)
        final_uid = find_uid("FINAL_TEST", used)
        rows = [
            (train_uid, 1, 4.0, 1480000000),
            (train_uid, 2, 3.5, 1500000000),
            (train_uid, 3, 5.0, 1500000000),
            (fresh_validation_uid, 1, 2.0, 1570000000),
            (fresh_validation_uid, 2, 4.5, 1600000000),
            (excluded_validation_uid, 1, 4.0, 1570000000),
            (excluded_validation_uid, 2, 1.0, 1601000000),
            (final_uid, 2, 3.0, 1650000000),
        ]
        rows.sort(key=lambda value: (value[0], value[3], value[1]))
        with (ml / "ratings.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["userId", "movieId", "rating", "timestamp"])
            writer.writeheader()
            for uid, movie_id, rating, timestamp in rows:
                writer.writerow({"userId": uid, "movieId": movie_id, "rating": rating, "timestamp": timestamp})

        prior_622 = root / "prior-622"
        prior_3623 = root / "prior-3623"
        self.write_prior(prior_622, 622, [excluded_validation_uid])
        self.write_prior(prior_3623, 3623, [999999])
        config = {
            "experiment": "S15P21E106-623-fm-v4-rolling-signed-content-v1",
            "status": "TEST",
            "split": {
                "seed": 4623,
                "target_limit_per_user": 4,
                "n_values": [0, 1, 2, 4, 7, 15, 25, 40, 50],
                "maximum_history": 50,
                "windows": {
                    role: {
                        "target_start_exclusive": base.role_window(role)[0],
                        "target_end_inclusive": base.role_window(role)[1],
                    }
                    for role in ("TRAIN", "VALIDATION", "FINAL_TEST")
                },
            },
            "expected_census": {
                "TRAIN": {"eligible_users": 1, "active_users": 1, "active_targets": 2, "n0_targets": 0},
                "VALIDATION": {"eligible_users": 1, "active_users": 1, "active_targets": 1, "n0_targets": 0},
                "FINAL_TEST": {"eligible_users": 1},
            },
            "source": {
                "ratings": file_pin(ml / "ratings.csv"),
                "movies": file_pin(ml / "movies.csv"),
                "tags": file_pin(ml / "tags.csv"),
                "prior_validation": {
                    "seed_622": {"users": 1, "user_digest": user_digest([excluded_validation_uid])},
                    "seed_3623": {"users": 1, "user_digest": user_digest([999999])},
                    "expected_intersection_users": 0,
                    "expected_union_users": 2,
                },
            },
            "pins": {"source_manifest": None},
        }
        config_path = root / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        args = Namespace(
            movielens_root=ml,
            prior_seed_622_input_root=prior_622,
            prior_seed_3623_input_root=prior_3623,
            config=config_path,
            output=root / "source",
        )
        ids = {
            "train": train_uid,
            "validation": fresh_validation_uid,
            "excluded_validation": excluded_validation_uid,
            "final": final_uid,
        }
        return args, config_path, ids

    def freeze_source_manifest_pin(self, config_path: Path, source_root: Path) -> None:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["pins"]["source_manifest"] = file_pin(source_root / "manifest.json")
        config_path.write_text(json.dumps(config), encoding="utf-8")

    def repin_source_file(self, source_root: Path, config_path: Path, key: str, path: Path, rows: int) -> None:
        manifest_path = source_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][key] = file_pin(path) | {"rows": rows}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.freeze_source_manifest_pin(config_path, source_root)

    def build_source_fixture(self, root: Path) -> tuple[Namespace, Path]:
        args, config_path, _ = self.make_fixture(root)
        with redirect_stdout(StringIO()):
            build(args)
        return args, config_path

    def rewrite_validation_rows(self, args: Namespace, config_path: Path, rows: list[dict]) -> None:
        validation_path = args.output / "validation-episodes.jsonl"
        validation_path.write_text(
            "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
            encoding="utf-8",
        )
        self.repin_source_file(args.output, config_path, "validation_episodes", validation_path, len(rows))

    def test_prior_loader_skips_final_before_json_parse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_prior(root, 622, [2, 4])
            with (root / "episodes.jsonl").open("a", encoding="utf-8") as stream:
                stream.write('{"role":"FINAL_TEST",BROKEN}\n')
            actual, report = load_prior_validation_users(root, 622)
            self.assertEqual(actual, {2, 4})
            self.assertEqual(report["user_digest"], user_digest([2, 4]))

    def test_target_selection_is_label_blind_and_recent_history_is_capped(self):
        events_a = [Event(movie_id, 1500000000 + movie_id, float(movie_id)) for movie_id in range(1, 7)]
        events_b = [Event(row.movie_id, row.timestamp, 10.0 - float(row.movie_id)) for row in events_a]
        selected_a = [(row.timestamp, row.movie_id) for row in choose_targets(4623, 7, events_a)]
        selected_b = [(row.timestamp, row.movie_id) for row in choose_targets(4623, 7, events_b)]
        self.assertEqual(selected_a, selected_b)

        history = [Event(movie_id, movie_id, 3.0) for movie_id in range(1, 61)]
        target = Event(100, 100, 4.0)
        self.assertEqual(recent_n_values(60), [0, 1, 2, 4, 7, 15, 25, 40, 50])
        row = episode_row("TRAIN", 7, target, history, 50)
        self.assertEqual([event["movie_id"] for event in row["history"]], list(range(11, 61)))

    def test_build_separates_labels_and_uses_rolling_recent_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, _, ids = self.make_fixture(root)
            with redirect_stdout(StringIO()):
                manifest = build(args)

            train_rows = [json.loads(line) for line in (args.output / "train-episodes.jsonl").read_text().splitlines()]
            validation_rows = [
                json.loads(line) for line in (args.output / "validation-episodes.jsonl").read_text().splitlines()
            ]
            labels = [json.loads(line) for line in (args.output / "validation-labels.jsonl").read_text().splitlines()]
            target_three_n2 = next(row for row in train_rows if row["target_movie_id"] == 3 and row["n"] == 2)
            self.assertEqual([event["movie_id"] for event in target_three_n2["history"]], [1, 2])
            self.assertEqual(target_three_n2["prediction_at"], 1500000000)
            self.assertEqual(target_three_n2["episode_id"], f"{target_three_n2['target_key']}:2")
            self.assertTrue(all("target_rating" not in row for row in validation_rows))
            self.assertEqual(len(labels), 1)
            self.assertEqual(set(labels[0]), {"target_key", "target_rating"})
            self.assertEqual(labels[0]["target_key"], validation_rows[0]["target_key"])
            self.assertNotIn(ids["excluded_validation"], {row["uid"] for row in validation_rows})
            self.assertEqual(manifest["users"], {"TRAIN": 1, "VALIDATION": 1, "FINAL_TEST": 1})
            self.assertEqual(manifest["prior_validation_exclusion"]["new_validation_intersection_users"], 0)
            self.assertFalse(any("final" in path.name.lower() for path in args.output.iterdir()))
            self.assertFalse((args.output / "candidates.jsonl").exists())

    def test_build_fails_closed_on_raw_source_pin_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, _, _ = self.make_fixture(root)
            with (args.movielens_root / "tags.csv").open("a", encoding="utf-8") as stream:
                stream.write("1,1,changed,1\n")
            with self.assertRaisesRegex(RuntimeError, "tags source pin mismatch"):
                build(args)
            self.assertFalse(args.output.exists())

    def test_isolator_never_opens_or_copies_sealed_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, config_path, _ = self.make_fixture(root)
            with redirect_stdout(StringIO()):
                build(args)
            self.freeze_source_manifest_pin(config_path, args.output)
            (args.output / "validation-labels.jsonl").write_text("NOT JSON AND NOT THE PINNED LABEL BYTES\n", encoding="utf-8")
            isolated = root / "isolated"
            with redirect_stdout(StringIO()):
                report = isolate(Namespace(source_root=args.output, output_root=isolated, config=config_path))
            self.assertEqual(report["validation_labels_opened"], False)
            self.assertEqual(
                {path.name for path in isolated.iterdir()},
                {"train-episodes.jsonl", "validation-episodes.jsonl", "manifest.json"},
            )

    def test_isolator_rejects_validation_label_leak_before_json_parse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, config_path, _ = self.make_fixture(root)
            with redirect_stdout(StringIO()):
                build(args)
            validation_path = args.output / "validation-episodes.jsonl"
            validation_path.write_text('{"role":"VALIDATION","target_rating":4.0,BROKEN}\n', encoding="utf-8")
            self.repin_source_file(args.output, config_path, "validation_episodes", validation_path, 1)
            with self.assertRaisesRegex(RuntimeError, "exposes target_rating before JSON parse"):
                isolate(Namespace(source_root=args.output, output_root=root / "isolated", config=config_path))

    def test_isolator_rejects_final_role_before_json_parse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, config_path, _ = self.make_fixture(root)
            with redirect_stdout(StringIO()):
                build(args)
            train_path = args.output / "train-episodes.jsonl"
            train_path.write_text('{"role":"FINAL_TEST",BROKEN}\n', encoding="utf-8")
            self.repin_source_file(args.output, config_path, "train_episodes", train_path, 1)
            with self.assertRaisesRegex(RuntimeError, "contains role FINAL_TEST before JSON parse"):
                isolate(Namespace(source_root=args.output, output_root=root / "isolated", config=config_path))

    def test_isolator_rejects_duplicate_n_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, config_path = self.build_source_fixture(root)
            rows = [json.loads(line) for line in (args.output / "validation-episodes.jsonl").read_text().splitlines()]
            rows.append(dict(rows[0]))
            self.rewrite_validation_rows(args, config_path, rows)
            with self.assertRaisesRegex(RuntimeError, "duplicate N variant"):
                isolate(Namespace(source_root=args.output, output_root=root / "isolated", config=config_path))

    def test_isolator_rejects_missing_n_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, config_path = self.build_source_fixture(root)
            rows = [json.loads(line) for line in (args.output / "validation-episodes.jsonl").read_text().splitlines()]
            rows = [row for row in rows if int(row["n"]) == 0]
            self.rewrite_validation_rows(args, config_path, rows)
            with self.assertRaisesRegex(RuntimeError, "target N variant set mismatch"):
                isolate(Namespace(source_root=args.output, output_root=root / "isolated", config=config_path))

    def test_isolator_rejects_n_above_capped_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, config_path = self.build_source_fixture(root)
            rows = [json.loads(line) for line in (args.output / "validation-episodes.jsonl").read_text().splitlines()]
            extra = dict(rows[-1])
            extra["n"] = 2
            extra["episode_id"] = f"{extra['target_key']}:2"
            rows.append(extra)
            self.rewrite_validation_rows(args, config_path, rows)
            with self.assertRaisesRegex(RuntimeError, "N exceeds the capped available target history"):
                isolate(Namespace(source_root=args.output, output_root=root / "isolated", config=config_path))

    def test_isolator_rejects_incomplete_history_policy_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, config_path = self.build_source_fixture(root)
            manifest_path = args.output / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["history_policy"] = {
                "n_values": [0, 1, 2, 4, 7, 15, 25, 40, 50],
                "full_history_cap": 50,
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.freeze_source_manifest_pin(config_path, args.output)
            with self.assertRaisesRegex(RuntimeError, "rolling recent-N history policy mismatch"):
                isolate(Namespace(source_root=args.output, output_root=root / "isolated", config=config_path))


if __name__ == "__main__":
    unittest.main()
