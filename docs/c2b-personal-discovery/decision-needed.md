# C2B 결정 matrix

> 상태: `APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS`
> 결정 준비 상태: `READY_FOR_PRODUCT_DECISION`  
> P0 승인 현황: `1/6` — `DN-C2B-002` 승인, 나머지 5건 `REQUIRES_APPROVAL`  
> 로컬 baseline 구현 권위: `YES` — production 및 미승인 확장 구현 권위: `NO`

| ID | 결정 | 현재 evidence | 권장 token·보수적 현재값 | 상태 |
| --- | --- | --- | --- | --- |
| `DN-C2B-001` | K10 Fold-in alpha0.2를 public 개인화로 채택할지, K>10 입력 policy | REC-EV-011 K10 CI 양수; offline candidate, champion 아님 | `KEEP_PUBLIC_ALPHA0_SHADOW_K10`; 공개 alpha0, K10 alpha0.2 shadow만 | `REQUIRES_APPROVAL` |
| `DN-C2B-002` | 최초 3편·추가 3편과 기존 추천 유지·완료/명시적 제외 정책 | REC-EV-013 v1은 2+1만 기각. pagination 자체를 기각한 evidence는 없음 | `BASELINE_THREE_CUMULATIVE_LOAD_MORE_RATED_OR_EXPLICIT_DISMISS`; 최초 3편, 추가 요청마다 최대 3편 누적, 평가 완료 또는 `관심 없음`이면 목록·향후 후보 제외 | `APPROVED_BY_PRODUCT_OWNER_2026-08-30` |
| `DN-C2B-003` | 공개 reason 개수·문구 | REC-EV-006 40,000 position, Popularity 99.9825% emittable; UI 미승인 | `MAX_ONE_FAITHFUL_REASON`; 승인 전 reason 0개 | `REQUIRES_APPROVAL` |
| `DN-C2B-004` | 예상 별점 공개 | REC-EV-003C fail-closed | `STAR_DISABLED_FAIL_CLOSED`; `NOT_COMPUTED` | `REQUIRES_APPROVAL` |
| `DN-C2B-005` | outcome utility normalization/confidence | stage/linkage 원칙만 승인; exact 공식 없음 | `EXACT_STAGE_ONLY_C1_EVENT_AMENDMENT`; utility `NOT_COMPUTED` | `REQUIRES_APPROVAL` |
| `DN-C2B-006` | delivery/action retention, cache freshness, operational SLA | REC-EV-007 local 후보뿐; typed mapping/Catalog+C1 version, 원자성·결정론적 reconcile은 안전 전제 | `NO_STALE_VERSIONED_RETENTION_CANDIDATE`; 10m/24h/90d/24h 후보, stale 없음; domain-payload idempotency 201/200 mapper와 C0/C1/C2B lock 선형화 유지 | `REQUIRES_APPROVAL` |

## 기각된 옵션

- `EXPLORE_05_ON_POPULARITY`: REC-EV-004B full-catalog Test NDCG@10 0.009382→0.005113,
  상대 손실 약 45.5%, paired CI 전체 음수. REC-EV-013의 기준선이나 fallback으로 재사용하지 않는다.
- 예상 별점 clamp/round: REC-EV-003C에서 비가역·척도 불일치로 기각.
- 클릭 없음/미평가를 negative로 간주: outcome inference contract 위반.

## 승인 순서

1. REC-EV-013 v1 checksum과 `selected:null`, product_approved=false를 검증한다.
2. DN-C2B-002는 `BASELINE_THREE_CUMULATIVE_LOAD_MORE_RATED_OR_EXPLICIT_DISMISS`로 승인되었다. 2+1 discovery는 계속 기각하고, 나머지 DN-C2B-001/003/004/005/006을 독립적으로 결정한다.
3. DN-C2B-005를 선택하려면 먼저 `TASK-C2B-011` C1 current click behaviorEvent 계약을 승인한다.
4. 계약 status를 승인 값으로 바꾸고 main OpenAPI merge task를 연다.
5. 그 이후에만 backend/frontend를 구현한다.
