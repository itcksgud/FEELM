# C3 Product Scope

> 상태: `APPROVED` — `LOCAL_MVP_ONLY`

## 목표 흐름

```text
X-Local-Actor-Id allowlist
  → Party 생성(provider 2~4) → fake actor 초대 → recipient 수락(max 4)
  → 동일 COMPLETE KR FLATRATE catalog
  → 실제 영화 deterministic baseline + explanation

provider 2~4 선택 → immutable catalog comparison
  → provider별 count/overlap → 실제 영화 전체 stable cursor list
```

## 포함

- `SCR-C3-001` OTT provider 선택
- `SCR-C3-002` OTT catalog comparison summary
- `SCR-C3-003` provider별 실제 영화 전체 목록
- `SCR-C3-004` 내 Party 목록
- `SCR-C3-005` Party 생성·fake actor 초대
- `SCR-C3-006` 받은 초대·수락
- `SCR-C3-008` 설명 가능한 deterministic Party baseline
- owner 포함 Party 최대 4명, PENDING invitation unique, capacity race 원자성
- `KR` `FLATRATE`, C0 `UI_READY`, seed COMPLETE materialization, stable cursor

## 제외·차단

- `SCR-C3-007` Party taste analysis
- 실제 OAuth/JWT/email/nickname directory와 외부 초대 발송
- decline/cancel/leave/close/kick/transfer/expiry/retention
- Rating·노출·상세·OTT click attribution과 가중 취향 점수
- 예상 별점, 만족도, 공정성, 구성원 utility, Average/Balanced/ALS 개인화
- OTT 카탈로그 기준일 신뢰 상태의 사용자 기능, 재감상 기록, OTT 가격 대비 가치, 기록 가져오기
- 대표 영화만 보여주는 축약
- live availability refresh SLA와 production deployment

## Scenario

| ID | 결과 |
| --- | --- |
| `SCN-C3-001` | owner가 fake actor로 Party를 만들고 provider 2개를 선택한다. |
| `SCN-C3-002` | owner가 fake actor를 초대하고 recipient header로 수락해 ACTIVE 2/4가 된다. |
| `SCN-C3-003` | 동시에 두 명이 마지막 자리를 수락하면 한 명만 성공하고 memberCount=4다. |
| `SCN-C3-004` | Party baseline은 provider coverage·catalog popularity·title·movieId 순으로 실제 영화를 반환한다. |
| `SCN-C3-005` | 동일 provider/catalogVersion의 다른 Party도 동일한 순서와 설명을 받는다. |
| `SCN-C3-006` | Netflix/Watcha 비교 summary count와 각 provider 전체 traversal count가 일치한다. |
| `SCN-C3-007` | overlap 영화는 양쪽 provider 목록에 실제 영화로 한 번씩 나타난다. |
| `SCN-C3-008` | unknown local actor와 cross-member private resource는 각각 401/404다. |

## 품질 Gate

| Gate | 완료 기준 |
| --- | --- |
| local safety | non-loopback bind 시작 실패, fixture allowlist 밖 actor 401 |
| capacity | owner 포함 1~4, concurrent accept가 4를 넘기지 않음 |
| determinism | 같은 provider/materialization/policy 입력의 byte-stable order |
| explainability | provider coverage·popularity rank 외 추정 field 0건 |
| actual catalog | 모든 item이 C0 movieId/title/poster/year를 갖고 전체 traversal 가능 |
| comparison | summary count = provider cursor distinct movie count |
| privacy | body/query actor 신뢰 0건, cross-member payload 0건 |
| authority | main OpenAPI는 승인된 11 operation만 포함; backend/production config 변경 0건 |
