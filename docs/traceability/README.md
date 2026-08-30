# FEELM 승인 추적성 Index

> 상태: `APPROVED` — C0 Catalog base + C1 Rating·Film extension  
> Canonical registry: `docs/spec/approved-slices.json`

현재 공개 제품 추적성은 두 CSV의 합집합이다.

- C0: `docs/traceability/requirements.csv`
- C1: `docs/c1-draft/traceability/requirements.csv`

공통 OpenAPI operation은 반드시 두 CSV 중 하나 이상에 연결되어야 한다. C1 acceptance ID의 자동
증거는 `docs/testing/c1-ac-test-map.csv`에서 완전한 `AC-C1-NNN` 형식으로 추적한다. C2A는
`docs/c2-recommendation/traceability/requirements.csv`의 내부 상태를 유지하며 이 공개 제품
합집합에 포함하지 않는다.
