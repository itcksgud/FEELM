from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .artifact_set import export_fixture_artifact_set
from .candidate_export import LocalCandidateStore, export_candidate_artifacts
from .catalog_mapping_export import export_catalog_mapping


LOCAL_CATALOG_VERSION = "catalog-fixture-20260829-01"
LOCAL_COMPATIBILITY_ID = "c2-local-stack-fixture-family-v1"
LOCAL_MOVIES = (
    ("6b226903-0ca4-4f5a-9bf0-50d6cedd224c", "Now You See Me", "UI_READY"),
    ("19406c31-213f-4fe1-93f6-109f8570ec20", "The English Fallback", "UI_READY"),
    ("97204ea5-e6e5-4417-a13f-bc8197660705", "No Poster Movie", "CATALOG_VISIBLE"),
    ("e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490", "Nothing Listed", "UI_READY"),
    ("1958ba3a-3d8c-4a4f-8845-124c0b12373e", "OTT Unknown", "UI_READY"),
    ("0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89", "Stale OTT", "UI_READY"),
    ("e67778c9-7b2e-42d4-9d3e-a3026b2efea3", "Inside Man", "UI_READY"),
    ("cc3ddb45-0511-46ea-bf28-95b67c9fd20f", "The Prestige", "UI_READY"),
)


def validate_v100_fixture_sql(sql_text: str) -> dict[str, Any]:
    """Fail safely when the generated C2 fixture drifts from the local V100 DB fixture."""
    catalog_match = re.search(
        r"INSERT INTO catalog_version.*?VALUES\s*\(\s*'[^']+',\s*'([^']+)'",
        sql_text,
        flags=re.DOTALL,
    )
    identity_match = re.search(
        r"INSERT INTO movie_identity.*?VALUES(.*?);",
        sql_text,
        flags=re.DOTALL,
    )
    projection_match = re.search(
        r"INSERT INTO movie_catalog_projection.*?VALUES(.*?);",
        sql_text,
        flags=re.DOTALL,
    )
    if catalog_match is None or identity_match is None or projection_match is None:
        raise ValueError("LOCAL_V100_FIXTURE_SHAPE_INVALID")
    identity_ids = set(re.findall(
        r"'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'",
        identity_match.group(1),
    ))
    projections = dict(re.findall(
        r"\(\s*'[^']+',\s*'([0-9a-f-]{36})',\s*'MOVIE',\s*'IDENTITY_VERIFIED',\s*'([^']+)'",
        projection_match.group(1),
        flags=re.DOTALL,
    ))
    expected = {movie_id: visibility for movie_id, _, visibility in LOCAL_MOVIES}
    if catalog_match.group(1) != LOCAL_CATALOG_VERSION:
        raise ValueError("LOCAL_V100_CATALOG_VERSION_DRIFT")
    if identity_ids != set(expected):
        raise ValueError("LOCAL_V100_MOVIE_ID_DRIFT")
    if projections != expected:
        raise ValueError("LOCAL_V100_VISIBILITY_DRIFT")
    return {
        "catalogVersion": LOCAL_CATALOG_VERSION,
        "movieCount": len(identity_ids),
        "uiReadyCount": sum(value == "UI_READY" for value in projections.values()),
    }


def _canonical_line(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def local_catalog_bytes() -> bytes:
    """Return a deterministic Catalog-shaped fixture matching V100 local database IDs.

    The MovieLens IDs and model values are contract fixtures. They are not a claim that the
    V100 demonstration titles correspond to those MovieLens rows.
    """
    records: list[dict[str, Any]] = [
        {
            "recordType": "artifactHeader",
            "schemaVersion": 1,
            "catalogVersion": LOCAL_CATALOG_VERSION,
            "generatedAt": "2026-08-29T05:00:00Z",
            "sourceChecksums": {"movielensArchiveSha256": "0" * 64},
            "sources": [
                {"name": "LOCAL_CATALOG_FIXTURE", "scope": "CONTRACT_ONLY"},
                {"name": "LOCAL_MODEL_FIXTURE", "scope": "CONTRACT_ONLY"},
            ],
        }
    ]
    for item_id, (movie_id, title, visibility) in enumerate(LOCAL_MOVIES, start=1):
        records.append(
            {
                "recordType": "movieIdentity",
                "payload": {
                    "movieId": movie_id,
                    "createdAt": "2026-08-29T04:00:00Z",
                    "identityStatus": "IDENTITY_VERIFIED",
                    "externalIds": [
                        {
                            "source": "MOVIELENS",
                            "externalId": str(item_id),
                            "verificationStatus": "VERIFIED",
                            "verifiedAt": "2026-08-29T04:00:00Z",
                        }
                    ],
                    "provenance": {
                        "movielensTitle": title,
                        "movielensReleaseYear": None,
                        "resolutionMethod": "LOCAL_CONTRACT_FIXTURE_ONLY",
                        "previousTmdbId": None,
                    },
                },
            }
        )
        records.append(
            {
                "recordType": "movieProjection",
                "payload": {
                    "movieId": movie_id,
                    "mediaType": "MOVIE",
                    "identityStatus": "IDENTITY_VERIFIED",
                    "visibilityStatus": visibility,
                    "originalTitle": title,
                    "originalLanguage": "en",
                    "releaseDate": None,
                    "runtimeMinutes": 100,
                    "posterPath": None,
                    "backdropPath": None,
                    "tmdbVoteAverage": None,
                    "tmdbVoteCount": 0,
                    "metadataFetchedAt": "2026-08-29T05:00:00Z",
                    "deleted": False,
                },
            }
        )
    return b"".join(_canonical_line(record) for record in records)


def export_local_stack_fixture(
    output_dir: str | Path,
    *,
    catalog_source: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(output_dir)
    catalog_dir = root / "catalog"
    mapping_dir = root / "mapping"
    serving_dir = root / "serving"
    candidate_dir = root / "candidates"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    mapping_dir.mkdir(parents=True, exist_ok=True)
    candidate_dir.mkdir(parents=True, exist_ok=True)

    catalog_path = catalog_dir / "catalog.jsonl"
    if catalog_source is None:
        catalog_path.write_bytes(local_catalog_bytes())
        scope = "LOCAL_CONTRACT_FIXTURE_ONLY_NOT_PRODUCTION_COVERAGE"
    else:
        source_path = Path(catalog_source)
        if not source_path.is_file():
            raise ValueError("LOCAL_IMPORTED_CATALOG_NOT_FOUND")
        catalog_path.write_bytes(source_path.read_bytes())
        scope = "LOCAL_IMPORTED_CATALOG_ONLY_NOT_PRODUCTION_COVERAGE"
    mapping_path = mapping_dir / "mapping.json"
    mapping_metadata_path = mapping_dir / "mapping.metadata.json"
    export_catalog_mapping(
        catalog_path=catalog_path,
        mapping_path=mapping_path,
        metadata_path=mapping_metadata_path,
        quarantine_path=mapping_dir / "mapping.quarantine.json",
        compatibility_id=LOCAL_COMPATIBILITY_ID,
    )
    serving_manifest = export_fixture_artifact_set(
        serving_dir,
        mapping_payload=mapping_path,
        mapping_metadata_path=mapping_metadata_path,
    )
    candidate_result = export_candidate_artifacts(
        catalog_path=catalog_path,
        mapping_payload_path=mapping_path,
        mapping_metadata_path=mapping_metadata_path,
        serving_manifest_path=serving_manifest,
        candidate_path=candidate_dir / "candidate-set.json",
        quarantine_path=candidate_dir / "quarantine.json",
        store_dir=candidate_dir / "store",
    )
    active = LocalCandidateStore(candidate_dir / "store").load_active()
    return {
        "status": "PASS",
        "scope": scope,
        "catalogVersion": active["catalogVersion"],
        "artifactSetManifest": "serving/artifact-set.json",
        "candidateStore": "candidates/store",
        "candidateSetVersion": active["candidateSetVersion"],
        "acceptedRecords": candidate_result.accepted_records,
        "quarantinedRecords": candidate_result.quarantined_records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the isolated local-stack C2 fixture")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--catalog",
        type=Path,
        help="Optional imported Catalog JSONL. Mapping and candidates bind to that exact artifact.",
    )
    args = parser.parse_args(argv)
    print(json.dumps(export_local_stack_fixture(args.output_dir, catalog_source=args.catalog), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
