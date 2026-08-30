# C1 제품 결정 기록과 후속 결정 항목

> 상태: `APPROVED`  
> 승인일: 2026-08-29  
> 승인 근거: 프로젝트 오너가 자율 완주를 위임하며 지정한 P0 정책  
> 원칙: 구현을 실제로 갈라놓는 결정과 승인된 안전 경계를 함께 추적한다.

## P0 — 승인 완료

| ID | 상태 | 승인 정책 | 계약 영향 |
| --- | --- | --- | --- |
| `DN-C1-001` | `APPROVED` | `confirmationDueAt=최초 active clickedAt+48h`, `expiresAt=최초 active clickedAt+7d`. 같은 user/movie에 `LINK_CLICKED` 또는 `CONFIRMATION_PENDING` intent가 있으면 다른 Idempotency-Key의 재클릭도 그 intent를 재사용하며 시각과 revision을 갱신하지 않는다. 실제 새 클릭에는 `OTT_LINK_CLICKED` event를 정확히 한 건 기록한다. `CONFIRMED_NOT_WATCHED`/`EXPIRED`이고 ViewingRecord가 없으면 새 intent를 허용한다. ViewingRecord가 있으면 intent 없이 클릭 event만 기록하고 `ALREADY_WATCHED`를 반환한다. 같은 key replay는 event도 추가하지 않는다. | scheduler 경계, active intent unique/locking, create 응답 outcome, event 멱등성 |
| `DN-C1-002` | `APPROVED` | Rating 삭제 후 ViewingRecord는 `WATCHED_CONFIRMED`로 유지한다. Rating은 `DELETED` soft-delete와 감사 metadata를 유지하고 active 조회·추천 입력에서 제외한다. Frame·Popcorn 공개 projection과 Rating contribution을 제거하고 Flavor/Taste aggregate를 같은 transaction에서 역산한다. `RATING_DELETED` behavior event와 outbox를 같은 transaction에 기록한다. | DELETE 응답, Film/Popcorn count, aggregate delta, rollback, audit |
| `DN-C1-003` | `APPROVED` | C1 rating-eligible 영화는 active projection의 `visibilityStatus=UI_READY`여야 하며, 이는 C0의 catalog-visible 최소 요건을 상위 충족한다. 별도의 동시 visibility 상태를 요구하지 않는다. active mapping version에서 `MovieFlavorAssignment`가 정확히 하나여야 한다. v1은 TMDB `genres[displayOrder=0]`의 genre ID를 다음 안정 코드로 매핑한다: `ADRENALINE={28,12}`, `WONDER={16,14,878}`, `JOY={35,10751}`, `HEART={18,10749}`, `SHADOW={80,27,9648,53}`, `REAL={99}`, `LEGACY={36,10752,37}`, `RHYTHM={10402,10770}`. 표시명은 각각 `짜릿함/상상/유쾌함/여운/긴장/현실/시대/리듬`이다. unknown·장르 0개·assignment 0개 또는 복수는 quality Gate 실패다. v1은 최적 모델 주장이 아닌 versioned 개선 기준이다. | Catalog publish Gate, Rating eligibility, seed/reference data, Popcorn 표시 |

P0 승인값은 C1 구현 기준이다. 정책 변경은 같은 Decision ID의 승인 이력과 mapping version을 올려
계약·migration·fixture·AC를 함께 갱신한다.

## P1 — `DRAFT_NON_BLOCKING` 후속 결정

| ID | 상태 | 결정 질문 | 승인된 C1 안전 경계 |
| --- | --- | --- | --- |
| `DN-C1-004` | `DRAFT_NON_BLOCKING` | WatchIntent 없이 영화 상세에서 수동 감상·평가를 허용하는가? | confirmed ViewingRecord가 있는 영화만 Rating 생성 허용 |
| `DN-C1-005` | `DRAFT_NON_BLOCKING` | `안 봤어요` 응답을 추천 부정 신호로 쓰는가? | 사실 event만 기록하고 선호·추천 신호에는 사용하지 않음 |
| `DN-C1-006` | `DRAFT_NON_BLOCKING` | 행동 event와 삭제된 Rating 감사 자료의 보존 기간은 얼마인가? | 외부 노출·학습 사용 없이 접근 통제된 저장소에 두며 운영 배포 전 retention 승인 필요 |
| `DN-C1-007` | `DRAFT_NON_BLOCKING` | 시대 구간, 취향 점수, 키워드 변환식은 무엇인가? | 장르·국가·감독 등 raw count/sum/average만 제공 |

P1은 현재 승인 범위를 좁히는 경계이며 C1 핵심 구현을 차단하지 않는다. 해당 기능을 넓히기 전 별도
제품 결정을 승인한다.

## 결정 기록 양식

```text
Decision ID:
선택:
근거:
기준 시각·경계값:
API 영향:
DB/migration 영향:
기존 데이터 backfill:
승인자·일자:
```
