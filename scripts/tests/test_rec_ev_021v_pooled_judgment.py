from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from scripts.rec_ev_021v_pooled_judgment import (
    PreflightError,
    analyze_judgments,
    budget_guard,
    build_blind_pool,
    build_catalog,
    expected_frozen_system_provenance,
    import_judgments,
    load_contract,
    sha256_file,
    synthetic_fixture,
    synthetic_judgments,
    validate_frozen_ranking_manifest,
    validate_contract,
    validate_source_manifest,
)


class RecEv021vPooledJudgmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract()
        cls.fixture = synthetic_fixture(cls.contract)
        cls.source_manifest = {
            "schema_version": 1,
            "source_id": "SYNTHETIC",
            "snapshot_version": "v1",
            "retrieved_at_utc": "2026-09-05T00:00:00+00:00",
            "catalog_as_of_date": "2026-09-05",
            "license": {
                "license_id": "SYNTHETIC",
                "license_url": "https://example.invalid",
                "research_use_status": "APPROVED",
                "redistribution_status": "ALLOWED",
                "attribution": "Synthetic fixture",
            },
            "local_artifact": {"path": None, "bytes": None, "sha256": None},
            "popularity_rule": {
                "field": "popularity_value",
                "low_pop_max_inclusive": 25.0,
                "popular_min_inclusive": 75.0,
                "frozen_before_judgments": True,
            },
            "synthetic_fixture": True,
        }

    def test_contract_locks_requested_scope(self) -> None:
        validate_contract(copy.deepcopy(self.contract))
        mutated = copy.deepcopy(self.contract)
        mutated["completion_gates"]["valid_users_min"] = 99
        with self.assertRaisesRegex(PreflightError, "completion gate"):
            validate_contract(mutated)

    def test_catalog_builds_all_four_strata(self) -> None:
        catalog, audit = build_catalog(self.fixture["catalog_rows"], self.source_manifest, self.contract)
        self.assertEqual(80, len(catalog))
        self.assertEqual({stratum: 20 for stratum in self.contract["catalog"]["strata"]}, audit["stratum_counts"])
        self.assertEqual(1.0, audit["mapping_and_dedup_rate"])

    def test_blind_pool_is_deterministic_balanced_and_resumable(self) -> None:
        catalog, _ = build_catalog(self.fixture["catalog_rows"], self.source_manifest, self.contract)
        with tempfile.TemporaryDirectory() as temporary:
            checkpoints = Path(temporary)
            pool, sealed, _ = build_blind_pool(
                catalog, self.fixture["onboarding"], self.fixture["rankings"], self.contract,
                checkpoint_root=checkpoints,
            )
            resumed_pool, resumed_sealed, audit = build_blind_pool(
                catalog, self.fixture["onboarding"], self.fixture["rankings"], self.contract,
                checkpoint_root=checkpoints, resume=True,
            )
        self.assertEqual(pool, resumed_pool)
        self.assertEqual(sealed, resumed_sealed)
        self.assertEqual(4, audit["resumed_participants"])
        self.assertEqual(192, len(pool))
        self.assertFalse(any("selection_source_model" in row or "model_ranks" in row for row in pool))
        for participant in {row["participant_id"] for row in pool}:
            rows = [row for row in pool if row["participant_id"] == participant]
            self.assertEqual(48, len(rows))
            self.assertEqual({stratum: 12 for stratum in self.contract["catalog"]["strata"]}, {
                stratum: sum(row["stratum"] == stratum for row in rows)
                for stratum in self.contract["catalog"]["strata"]
            })

    def test_k10_anchor_and_pii_firewalls_fail_closed(self) -> None:
        catalog, _ = build_catalog(self.fixture["catalog_rows"], self.source_manifest, self.contract)
        pool, _, _ = build_blind_pool(catalog, self.fixture["onboarding"], self.fixture["rankings"], self.contract)
        judgments = synthetic_judgments(pool)
        weak = copy.deepcopy(self.fixture["onboarding"])
        for item in weak[0]["items"]:
            item["mapped_label"] = "POSITIVE"
        _, weak_summary = import_judgments(self.fixture["participants"], weak, judgments, pool, self.contract)
        self.assertEqual(3, weak_summary["valid_users"])
        self.assertEqual(144, weak_summary["accepted_unique_judgments"])
        self.assertIn("K10 lacks minimum mapped positive/negative anchors", weak_summary["invalid_reason_counts"])
        pii = copy.deepcopy(self.fixture["participants"])
        pii[0]["email"] = "person@example.com"
        with self.assertRaisesRegex(PreflightError, "PII firewall"):
            import_judgments(pii, self.fixture["onboarding"], judgments, pool, self.contract)

    def test_fixture_import_and_analysis_remain_insufficient_evidence(self) -> None:
        catalog, _ = build_catalog(self.fixture["catalog_rows"], self.source_manifest, self.contract)
        pool, sealed, _ = build_blind_pool(catalog, self.fixture["onboarding"], self.fixture["rankings"], self.contract)
        judgments = synthetic_judgments(pool)
        normalized, summary = import_judgments(
            self.fixture["participants"], self.fixture["onboarding"], judgments, pool, self.contract
        )
        result = analyze_judgments(normalized, sealed, summary, self.contract, evidence_mode="SYNTHETIC_FIXTURE")
        self.assertEqual(4, summary["valid_users"])
        self.assertEqual(192, summary["accepted_unique_judgments"])
        self.assertEqual("INSUFFICIENT_TARGET_DOMAIN_EVIDENCE", result["status"])
        self.assertFalse(result["actual_target_domain_evidence"])
        self.assertFalse(result["actual_watch_14d_used_in_primary"])
        self.assertIsNone(result["champion"])

    def test_secondary_actual_watch_cannot_change_primary(self) -> None:
        catalog, _ = build_catalog(self.fixture["catalog_rows"], self.source_manifest, self.contract)
        pool, sealed, _ = build_blind_pool(catalog, self.fixture["onboarding"], self.fixture["rankings"], self.contract)
        judgments = synthetic_judgments(pool)
        normalized, summary = import_judgments(
            self.fixture["participants"], self.fixture["onboarding"], judgments, pool, self.contract
        )
        first = analyze_judgments(normalized, sealed, summary, self.contract, evidence_mode="SYNTHETIC_FIXTURE")
        changed = copy.deepcopy(normalized)
        for row in changed:
            row["actual_watch_14d"] = "YES"
        second = analyze_judgments(changed, sealed, summary, self.contract, evidence_mode="SYNTHETIC_FIXTURE")
        self.assertEqual(first, second)

    def test_budget_and_missing_source_fail_closed(self) -> None:
        self.assertEqual("PASS_ZERO_COST", budget_guard(self.contract, fixture_mode=True, participant_count=4)["status"])
        with self.assertRaisesRegex(PreflightError, "explicit approved budget"):
            budget_guard(self.contract, fixture_mode=False, participant_count=100)
        with self.assertRaisesRegex(PreflightError, "local catalog source is absent"):
            validate_source_manifest(self.source_manifest, self.contract, fixture_mode=True)

    def test_approved_external_local_source_is_accepted_without_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            catalog_path = Path(temporary) / "approved-catalog.csv"
            catalog_path.write_text("movie_key,display_title,release_date,origin_country_codes,popularity_value,mapping_status\n", encoding="utf-8")
            manifest = copy.deepcopy(self.source_manifest)
            manifest["synthetic_fixture"] = False
            manifest["license"]["license_id"] = "OWNER_APPROVED_TEST_LICENSE"
            manifest["local_artifact"] = {
                "path": str(catalog_path),
                "bytes": catalog_path.stat().st_size,
                "sha256": sha256_file(catalog_path),
            }
            self.assertEqual(catalog_path.resolve(), validate_source_manifest(manifest, self.contract, fixture_mode=False))

            ranking_path = Path(temporary) / "rankings.jsonl"
            ranking_path.write_text("{}\n", encoding="utf-8")
            ranking_manifest = {
                "schema_version": 1,
                "created_at_utc": "2026-09-05T00:00:00+00:00",
                "catalog_source_sha256": manifest["local_artifact"]["sha256"],
                "rankings_artifact": {
                    "path": str(ranking_path),
                    "bytes": ranking_path.stat().st_size,
                    "sha256": sha256_file(ranking_path),
                },
                "systems": expected_frozen_system_provenance(self.contract),
                "selected_before_judgments": True,
                "fit_or_refit_performed": False,
                "synthetic_fixture": False,
            }
            self.assertEqual(
                ranking_path.resolve(),
                validate_frozen_ranking_manifest(ranking_manifest, manifest, self.contract, fixture_mode=False),
            )
            ranking_manifest["systems"]["B8"]["seed"] = 42
            with self.assertRaisesRegex(PreflightError, "provenance"):
                validate_frozen_ranking_manifest(ranking_manifest, manifest, self.contract, fixture_mode=False)


if __name__ == "__main__":
    unittest.main()
