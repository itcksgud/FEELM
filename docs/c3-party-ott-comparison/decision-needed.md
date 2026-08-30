# C3 결정 현황

> 상태: `APPROVED`  
> 구현 권위: `LOCAL_MVP_ONLY` — localhost·seeded fake actor·결정적 fixture에 한정  
> local MVP 승인: `4/5`; production 승인: `0/5`

현재 사용자의 “별도 프로젝트에서 개발 완성” 지시는 되돌리기 쉬운 로컬 수직 기능을 구현할 권위로
해석한다. 이 승인은 배포, OAuth, 실제 사용자 초대, 외부 메시지, 개인화 추천 또는 만족도 추정을
승인하지 않는다.

| ID | local MVP 결정 | 상태 | production 경계 |
| --- | --- | --- | --- |
| `DN-C3-001` | `CATALOG_POPULARITY_KR_FLATRATE_V1`: Party가 선택한 OTT에서 볼 수 있는 실제 영화를 provider coverage DESC, C0 popularity rank ASC, normalized title ASC, movieId ASC로 정렬한다. | `APPROVED_LOCAL_MVP` | Average/Balanced, 예상 별점, 구성원 효용·만족도·개인화는 `DEFERRED` |
| `DN-C3-002` | owner 포함 최대 4명. create → fake actor 초대 → recipient accept만 제공한다. | `APPROVED_LOCAL_MVP` | decline/cancel/leave/close/kick/transfer/expiry/retention은 `DEFERRED` |
| `DN-C3-003` | `X-Local-Actor-Id`가 allowlist fixture UUID와 정확히 일치할 때만 actor로 인정한다. nickname은 표시 snapshot일 뿐 검색 key가 아니다. | `APPROVED_LOCAL_MVP` | 실제 인증/OAuth/email/nickname directory·enumeration 정책은 `DEFERRED` |
| `DN-C3-004` | 2~4개 distinct provider, `KR` `FLATRATE`, seed된 COMPLETE materialization, 실제 영화 전체 cursor 목록과 overlap count를 제공한다. | `APPROVED_LOCAL_MVP` | live TMDB refresh SLA, 24h freshness, 장기 retention은 `DEFERRED` |
| `DN-C3-005` | Rating·노출·상세·OTT click을 비교/추천 입력으로 쓰지 않는다. | `DEFERRED` | 행동 attribution·취향 분석·만족도 측정은 별도 제품 결정과 evidence가 필요 |

## local-only 불변식

- 서버는 `http://127.0.0.1` 또는 `http://localhost`에서만 실행하고 non-loopback bind에서 시작을 거부한다.
- `X-Local-Actor-Id`는 tracked fixture의 fake UUID만 허용한다. body/query actor ID는 신뢰하지 않는다.
- Party 추천은 구성원 Rating·vector·행동을 읽지 않는다. 같은 provider set과 catalog version이면 모든
  Party에 동일한 순서다.
- 추천 설명은 `availableProviderCount`, `selectedProviderCount`, `catalogPopularityRank`, `policyVersion`만
  제공하며 효용·확률·예상 별점 필드는 없다.
- OTT 비교는 실제 C0 `UI_READY` 영화만 반환하고 대표 영화 subset으로 전체 목록을 대체하지 않는다.
- main OpenAPI와 React local vertical은 병합됐다. backend 구현과 production auth 전환은 후속 task다.

## 여전히 필요한 사용자 결정

1. production 인증과 실제 초대 식별자: nickname exact match, 초대 코드, email 중 무엇인지
2. Party lifecycle: 거절·취소·나가기·종료·소유권 이전·초대 만료 및 보존 기간
3. live KR availability의 허용 freshness/SLA와 외부 장애 시 last-known-good 정책
4. 개인화 Party 추천을 할지, 한다면 candidate·집계 정책과 오프라인/온라인 성공 기준
5. Rating·노출·상세·OTT click을 어떤 의미와 기간으로 사용할지
