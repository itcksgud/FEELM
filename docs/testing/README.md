# FEELM 승인 검증 계약 Index

> 상태: `APPROVED` — C0 Catalog base + C1 Rating·Film extension  
> Canonical registry: `docs/spec/approved-slices.json`

- C0 acceptance·fixture·자동 검증: `docs/testing/acceptance-tests.md`,
  `docs/testing/fixtures.md`, `docs/testing/automated-tests.md`
- C1 acceptance·fixture: `docs/c1-draft/testing/acceptance-tests.md`,
  `docs/c1-draft/testing/fixtures.md`
- C1 자동 검증 현황과 완전한 AC mapping: `docs/testing/c1-automated-tests.md`,
  `docs/testing/c1-ac-test-map.csv`
- C2A 실제 Compose 경계·결과·한계: `docs/testing/c2a-compose-integration.md`

`PASS`는 해당 자동 테스트 산출물의 실행 상태이고 제품 계약 상태를 대체하지 않는다. 제품 계약 승인은
registry가 결정한다. `GAP` mapping은 AC가 누락된 것이 아니라 자동 증거가 아직 충분하지 않다는 명시적
상태이며, handoff 판정에서 PASS로 계산하지 않는다.
