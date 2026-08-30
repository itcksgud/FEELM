# FEELM 승인 데이터 계약 Index

> 상태: `APPROVED` — C0 Catalog base + C1 Rating·Film extension  
> Canonical registry: `docs/spec/approved-slices.json`

- C0 Catalog source of truth와 projection: `docs/data/data-dictionary.md`,
  `docs/data/logical-erd.md`, `docs/data/catalog-ingestion-contract.md`
- C1 Rating·Film source of truth와 projection: `docs/c1-draft/data/data-dictionary.md`,
  `docs/c1-draft/data/logical-erd.md`
- C2A 실제 추천 노출 snapshot: `docs/c2-recommendation/data/recommendation-exposure-schema.md`

C1 조각은 C0의 `MOVIE_IDENTITY`, active `MOVIE_CATALOG_PROJECTION`, OTT offer를 FK/eligibility
경계로 참조하는 승인 extension이다. 물리 migration은 두 데이터 계약의 키·소유권·transaction
불변식을 함께 보존해야 한다. C2A 내부 artifact·노출 계약은 `docs/c2-recommendation`의 별도 상태를 따른다.
