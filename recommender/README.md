# FEELM recommender core

This directory contains the framework-neutral recommendation core plus the approved C2A internal
FastAPI Popularity-only adapter. It does not define a Spring/public API and does not declare a champion model.

Implemented evidence boundary:

- regularized Bias prediction and Bayesian Popularity ranking fallback;
- explicit ALS-WR user Fold-in from trained item factors;
- persisted isotonic-threshold application in a head-aware bundle;
- separate `star_blend` and raw-Popularity `ranking` heads from `REC-EV-003B`;
- a versioned MovieLens item ID to FEELM service UUID mapping artifact;
- deterministic offline inference with missing/conflicting mappings quarantined;
- mandatory metadata, payload checksum, ID-space, dependency-checksum and compatibility-family checks.

The recorded `REC-EV-003B` result is a candidate, not a production adoption. Ranking can run as the validated
Popularity baseline without enabling that candidate. Star estimation requires explicit opt-in both when the
`RecommendationCore` is constructed and when the offline pipeline is invoked. Ranking always remains
Popularity-only because the validated Fold-in ranking weight is zero for every tested K.

## C2A internal Popularity service

The HTTP success path remains `BAYESIAN_POPULARITY_ONLY` with ranking alpha `0.0`.
`starPolicy=DISABLED` returns `NOT_COMPUTED`. `REC_EV_003B_CANDIDATE` deliberately
fails closed as `PARTIAL` with `STAR_SCALE_INCOMPATIBLE`; it does not compute, clamp,
round, or display a star value.

Install the hash-locked runtime and test dependencies, then install only the local package:

```powershell
py -3.12 -m pip install --require-hashes -r recommender\requirements-test.lock
py -3.12 -m pip install --no-deps --no-build-isolation -e recommender
```

Export and validate a deterministic contract-only set:

```powershell
$env:PYTHONPATH='recommender\src'
py -3.12 -m feelm_recommender export-serving-fixture `
  --output-dir outputs\c2-serving-fixture
py -3.12 -m feelm_recommender validate-serving-set `
  --manifest outputs\c2-serving-fixture\artifact-set.json
```

To exercise the actual Catalog smoke mapping with fixture model values, add:

```powershell
  --mapping outputs\catalog-smoke\recommender-mapping.json `
  --mapping-metadata outputs\catalog-smoke\recommender-mapping.metadata.json
```

That output is marked `CATALOG_MAPPING_FIXTURE` and `NOT_PRODUCTION_COVERAGE`.
Only the input-scoped mapping is real; Bias, factor, and calibration values are fixtures.
`assemble-serving-set` instead accepts four already-produced evidence payload/sidecar
pairs, copies them into one set, checks the complete compatibility graph, and performs
a Popularity dry-run. Assembly never promotes a candidate to champion.

Run the service only after explicitly enabling the local fake adapter:

```powershell
$env:C2_AUTH_MODE='fake'
$env:C2_ARTIFACT_SET_MANIFEST=(Resolve-Path outputs\c2-serving-fixture\artifact-set.json).Path
uvicorn feelm_recommender.api:app --app-dir recommender\src --host 127.0.0.1 --port 8000
```

`test-c2-service-token` is the authorized local fixture and
`test-c2-forbidden-token` is the authenticated-but-forbidden fixture. When
`C2_AUTH_MODE` is missing or not `fake`, both fail closed. Neither is an operational
credential. Issuance and rotation remain blocked by `DN-C2-004`.

## REC-EV-007 serving benchmark

The benchmark keeps the actual C2A HTTP path and the inactive Fold-in core diagnostic separate.
The HTTP path executes Popularity ranking with alpha `0.0`; the factor calculation is not silently
reported as HTTP Fold-in serving. Install the pinned test extra so a clean CI does not depend on an
ambient `httpx` installation:

```powershell
py -3.12 -m pip install --require-hashes -r recommender\requirements-test.lock
py -3.12 -m pip install --no-deps --no-build-isolation -e recommender
py -3.12 scripts/recommendation_serving_benchmark.py `
  --factor-artifact outputs/recommendation-evidence/rec-ev-003/cohort_excluded_item_factors.npz `
  --factor-manifest docs/recommendation/evidence/manifests/rec-ev-003.json `
  --result docs/recommendation/evidence/results/rec-ev-007-local-20260829.json `
  --manifest docs/recommendation/evidence/manifests/rec-ev-007.json
```

The result and manifest never retain raw IDs, credentials, or the factor host path. The full result
checksum intentionally changes with `generated_at`, environment, and observed timings. Compare the
separate protocol checksum when checking whether two runs used the same pre-measurement configuration.
The selected 750 ms timeout and 3000 ms freshness are local provisional values, not a production SLA.

The internal endpoints are `/internal/health/live`, `/internal/health/ready`, and
`/internal/v1/recommendations/rank`. Reasons stay empty pending the reason UI gate.

## C2A batch candidate artifact

`export-batch-candidates` joins Catalog JSONL v1, its checksum-bound mapping, and a
ready serving artifact set. It accepts only `MOVIE`, `IDENTITY_VERIFIED`, `UI_READY`,
and `deleted=false` projections that map without conflict to a Bias item with positive
training count. The canonical payload contains service UUIDs only.

```powershell
$env:PYTHONPATH='recommender\src'
py -3.12 -m feelm_recommender export-batch-candidates `
  --catalog outputs\catalog-smoke\catalog.jsonl `
  --mapping outputs\catalog-smoke\recommender-mapping.json `
  --mapping-metadata outputs\catalog-smoke\recommender-mapping.metadata.json `
  --serving-manifest outputs\c2-serving-smoke\artifact-set.json `
  --candidate outputs\c2-candidate-smoke\candidate-set.json `
  --quarantine outputs\c2-candidate-smoke\quarantine.json `
  --store-dir outputs\c2-candidate-smoke\store

py -3.12 -m feelm_recommender inspect-candidate-store `
  --store-dir outputs\c2-candidate-smoke\store
```

The quarantine sidecar contains allowlisted reason counts, never MovieLens IDs,
service UUIDs, tokens, or host paths. The local store keeps immutable versions, one
active pointer, and the immediately previous rollback pointer. It defines no TTL or
retention duration and deletes no referenced version. Checksum mismatch or an empty
accepted set fails before pointer replacement. The two-film smoke result is input-only
coverage, not production Catalog coverage.

```powershell
$env:PYTHONPATH='recommender\src'
py -3.12 -m unittest discover -s recommender\tests -p 'test_*.py'
```

Existing experiment `.npz` files are intentionally not loaded without a metadata sidecar. Promotion must
first produce a serving bundle with a checksum and a shared `compatibility_id`. The blend-specific isotonic
calibrators also still need to be exported from the experiment before a real bundle can be assembled.

## Serving artifact boundary

`RecommendationCore.from_artifacts` requires four payload/metadata pairs: Bias, ALS item factors, head-aware
calibration, and item mapping. Calibration metadata binds the exact SHA-256 values of the other three payloads.
Changing any payload without exporting a compatible calibration bundle is rejected.

The calibration payload is schema v2 and explicitly keeps the heads separate:

```json
{
  "schema_version": 2,
  "policy_version": "cold-start-dual-head-blend-v1",
  "heads": {
    "star_blend": {"mode": "ISOTONIC_BY_K", "calibrators": {}},
    "ranking": {"mode": "NONE_POPULARITY_RAW", "alpha": 0.0}
  }
}
```

The mapping payload is schema v1. `movielens_item_id` is a positive external MovieLens ID; it is never treated
as a FEELM resource ID. `service_movie_id` is a UUID. A source ID mapped to multiple UUIDs, or a UUID mapped to
multiple source IDs, is excluded and reported in the mapping quarantine.

```json
{
  "schema_version": 1,
  "mapping_version": "catalog-to-movielens-v1",
  "source_id_space": "movielens-int-v1",
  "target_id_space": "feelm-movie-uuid-v1",
  "records": [
    {"movielens_item_id": 1, "service_movie_id": "00000000-0000-0000-0000-000000000001"}
  ]
}
```

`OfflineInferencePipeline` accepts service UUIDs, resolves them through that mapping, ranks accepted candidates
with deterministic Bayesian Popularity, and optionally produces the non-champion star candidate. It returns
request and artifact quarantine records rather than silently casting a UUID or missing mapping to a model ID.
This is an internal offline boundary; no FastAPI or HTTP contract is defined here.

Expected-star values in the current REC-EV-003B evidence remain on the MovieLens `0.5..5.0` model scale.
They are not a C1 integer `1..5` product adapter. REC-EV-003C rejects clamp/round and leaves product stars
disabled until prediction-before-rating rows paired with held-out C1 integer Ratings exist. The exporter below
creates that deterministic, de-identified evidence artifact from allowlisted joined rows; it rejects user/movie
IDs and requires CALIBRATION outcomes to precede every VALIDATION prediction.

```powershell
py -3 -m feelm_recommender export-product-scale-validation `
  --source outputs\c2\joined-product-scale-source.json `
  --payload outputs\c2\c1-product-scale-pairs.json `
  --metadata outputs\c2\c1-product-scale-pairs.metadata.json `
  --dataset-version c1-product-scale-v1
```

## Catalog JSONL mapping export

`export-catalog-mapping` reads Catalog artifact JSONL schema v1 without calling an external service. It accepts
only `movieIdentity` records whose identity is verified and whose `MOVIELENS` external ID is `VERIFIED` or
`RECOVERED`. Invalid, unverified, duplicate, and conflicting rows are excluded into a safe quarantine report.
The mapping, metadata sidecar, and report contain no generation timestamp and are byte-identical when the
catalog bytes, `catalogVersion`, and compatibility ID are unchanged. Reported coverage is limited to the input
catalog artifact and is not production-catalog coverage.

```powershell
py -3 -m feelm_recommender export-catalog-mapping `
  --catalog outputs\catalog-smoke\catalog.jsonl `
  --mapping outputs\catalog-smoke\recommender-mapping.json `
  --metadata outputs\catalog-smoke\recommender-mapping.metadata.json `
  --quarantine outputs\catalog-smoke\recommender-mapping.quarantine.json `
  --compatibility-id catalog-smoke-recommender-family-v1
```

Run tests:

```powershell
$env:PYTHONPATH='recommender\src;recommender\tests'
py -3 -m unittest discover -s recommender\tests -p 'test_*.py' -v
```

Inspect a serving artifact pair:

```powershell
py -3 -m feelm_recommender inspect --metadata path\metadata.json --payload path\parameters.npz
```
