import json
from pathlib import Path
import tempfile
import unittest

from measure_ai_efficiency import aggregate_rollouts, read_rollout


def event(i, o, cache, reasoning):
    return {"type": "event_msg", "payload": {"type": "token_count", "info": {
        "total_token_usage": {"input_tokens": i, "cached_input_tokens": cache,
                              "output_tokens": o, "reasoning_output_tokens": reasoning,
                              "total_tokens": i + o}}}}


class UsageTests(unittest.TestCase):
    def fixture(self, records):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "rollout.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in records), encoding="utf-8")
        return path

    def test_duplicate_counters_and_subsets_are_not_double_counted(self):
        path = self.fixture([event(100, 30, 60, 20), event(100, 30, 60, 20), event(180, 50, 120, 35)])
        result = read_rollout(path)
        self.assertEqual(result["usage"]["total_tokens"], 230)
        self.assertEqual(result["usage"]["reasoning_output_tokens"], 35)
        self.assertEqual(result["duplicate_snapshots_skipped"], 1)

    def test_counter_reset_requires_review(self):
        with self.assertRaisesRegex(ValueError, "decreased"):
            read_rollout(self.fixture([event(100, 30, 60, 20), event(20, 10, 0, 0)]))

    def test_inconsistent_reasoning_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Reasoning"):
            read_rollout(self.fixture([event(100, 10, 0, 11)]))

    def test_explicit_child_usage_is_included_and_duplicate_paths_rejected(self):
        root = self.fixture([event(100, 30, 60, 20)])
        child = self.fixture([event(20, 10, 0, 0)])
        self.assertEqual(aggregate_rollouts([root, child])["usage"]["total_tokens"], 160)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            aggregate_rollouts([root, root])

    def test_missing_breakdown_is_not_reported_as_zero(self):
        record = event(100, 30, 60, 20)
        del record["payload"]["info"]["total_token_usage"]["reasoning_output_tokens"]
        with self.assertRaisesRegex(ValueError, "unavailable"):
            read_rollout(self.fixture([record]))

    def test_partial_live_log_is_rejected(self):
        path = self.fixture([event(100, 30, 60, 20)])
        with path.open("a", encoding="utf-8") as stream:
            stream.write('\n{"type":')
        with self.assertRaisesRegex(ValueError, "malformed"):
            read_rollout(path)


if __name__ == "__main__":
    unittest.main()
