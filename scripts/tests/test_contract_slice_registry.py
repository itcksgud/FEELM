import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import validate_contracts


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ApprovedSliceRegistryValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        shutil.copytree(PROJECT_ROOT / "docs", self.root / "docs")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def validate_registry(self) -> list[str]:
        errors: list[str] = []
        with patch.object(validate_contracts, "ROOT", self.root):
            validate_contracts.validate_slice_registry(errors)
        return errors

    def test_c0_only_product_scope_regression_is_rejected(self) -> None:
        scope = self.root / "docs/spec/00-product-scope.md"
        text = scope.read_text(encoding="utf-8")
        text = text.replace(
            "승인 공개 제품 Slice: C0 Catalog + C1 Rating·Film",
            "현재 승인된 구현 단위는 첫 단계인 **C0 Catalog**",
        )
        scope.write_text(text, encoding="utf-8")

        errors = self.validate_registry()

        self.assertTrue(any("C0+C1 public approval marker missing" in error for error in errors))
        self.assertTrue(any("stale C0-only marker" in error for error in errors))

    def test_c1_registry_status_conflict_is_rejected(self) -> None:
        registry_path = self.root / "docs/spec/approved-slices.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        c1 = next(
            item
            for item in registry["publicProductSlices"]
            if item["sliceId"] == "C1_RATING_FILM"
        )
        c1["status"] = "DRAFT"
        registry_path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        errors = self.validate_registry()

        self.assertTrue(any("C1_RATING_FILM status/mode/root conflict" in error for error in errors))


class RecommendationTaskIdValidatorTest(unittest.TestCase):
    def test_preflight_subtask_suffix_is_a_known_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "backlog.yaml"
            path.write_text(
                "tasks:\n"
                "  - id: TASK-REC-EV-020P-A\n"
                "    status: DONE\n"
                "    depends_on: []\n"
                "  - id: TASK-REC-EV-020P-B\n"
                "    status: READY\n"
                "    depends_on: [TASK-REC-EV-020P-A]\n",
                encoding="utf-8",
            )
            errors: list[str] = []
            with patch.object(validate_contracts, "ROOT", root):
                tasks = validate_contracts.validate_task_states(
                    "backlog.yaml", "TASK-REC-EV", errors
                )

            self.assertEqual(
                {"TASK-REC-EV-020P-A", "TASK-REC-EV-020P-B"}, tasks
            )
            self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
