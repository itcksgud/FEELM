# C2 Recommendation Serving 계약 세트

> 상태: `APPROVED_C2A_INTERNAL_POPULARITY_ONLY` — 내부 ranking 구현 기준, 공개 API·예상 별점은 미승인  
> 기준일: 2026-08-29

## 1. 목적과 권위

이 디렉터리는 Spring이 FastAPI 추천 코어를 호출하는 C2 내부 계약을 한곳에 모은다. 현재
`recommender/`가 검증하는 artifact schema, service UUID mapping, dual-head 정책과 C1 Rating·outbox
계약을 연결한다. C2A FastAPI와 Spring 내부 adapter는 이 계약 fragment를 기준으로 구현됐지만,
`docs/api/openapi.yaml`의 공개 계약과 public Spring controller는 아직 변경하지 않는다.

충돌 시 다음 순서를 따른다.

1. `AGENTS.md`와 승인된 제품 계약
2. `docs/recommendation/product-decisions-required.md`
3. `docs/recommendation/model-registry.yaml` 및 완료 evidence
4. `recommender/`의 artifact validation과 결정론 테스트
5. 이 디렉터리의 승인된 C2A 규칙

이 승인은 Spring↔FastAPI 내부 Popularity-only 경계와 local fake service credential에만 적용한다.
새 공개 API·UI 의미, expected-star 활성화, 운영 credential은 각 decision Gate가 해제되기 전 구현하지 않는다.

## 2. C2 v0 범위

- Spring → FastAPI 내부 ranking 요청과 응답
- 후보와 Rating 입력의 FEELM service UUID 경계
- batch candidate version과 C1 active Rating input version 연결
- Bias, factor, calibration v2, mapping artifact의 family/checksum Gate
- 모든 K에서 `BAYESIAN_POPULARITY_ONLY`, Fold-in ranking alpha `0.0`
- `starPolicy=DISABLED`의 fail-closed expected-star 상태
- 항목별 부분 제외와 star-head 부분 실패
- 요청·입력·후보·artifact·policy version snapshot
- liveness와 artifact-aware readiness
- C1 outbox 처리와 동기 추천 호출의 transaction 분리

## 2.1 근거 snapshot

| 근거 | 이 계약이 고정한 내용 |
| --- | --- |
| `recommender/src/feelm_recommender/metadata.py` | artifact kind, family·ID space·rating scale, checksum binding |
| `recommender/src/feelm_recommender/calibration.py` | schema v2의 분리된 star/ranking head와 ranking alpha 0 |
| `recommender/src/feelm_recommender/catalog_mapping_export.py` | Catalog JSONL의 verified/recovered MovieLens ID→service UUID mapping |
| `recommender/src/feelm_recommender/inference.py` | service UUID 요청 경계, 항목 quarantine, 결정적 Popularity 순위 |
| `docs/recommendation/evidence/REC-EV-003B-cold-start-blend.md` | K별 star candidate와 모든 K ranking alpha 0 |
| `docs/recommendation/product-decisions-required.md` | 숫자 UI, confidence, reason, exploration, party의 미결정 Gate |
| `docs/c1-draft/02-business-rules.md` | active Rating, 안전한 behavior/outbox, 추천 장애 격리 |
| `backend/src/main/resources/db/migration/V2__c1_rating_film_foundation.sql` | Rating·behavior·domain_outbox 저장 구조 |

## 3. 명시적 비범위와 금지 주장

- REC-EV-003B를 champion 또는 production 채택 모델이라고 부르지 않는다.
- sampled NDCG를 full-catalog 또는 온라인 만족도 증거로 확대하지 않는다.
- 미평가·미클릭·`watched=false`를 싫어요나 부정 선호로 만들지 않는다.
- 예상 별점, TMDB 평점, 실제 Rating, 추천 결과 효용을 같은 값으로 표현하지 않는다.
- 탐험 가중치, 파티 집계, reason 표시 개수, UI 예상 별점 숫자를 정하지 않는다.
- FastAPI가 사용자 bearer, email, raw behavior log 또는 MovieLens user ID를 받지 않는다.

## 4. 산출물

| 파일 | 역할 |
| --- | --- |
| `01-business-rules.md` | 경계, fallback, 오류, 보안, decision Gate |
| `02-sequence-and-data-contract.md` | 동기·비동기 흐름과 version snapshot |
| `03-batch-candidate-contract.md` | 결정적 후보 artifact, quality Gate, 원자 게시·retention 경계 |
| `api/openapi.fragment.yaml` | 독립 lint 가능한 내부 OpenAPI fragment |
| `testing/acceptance-tests.md` | Given/When/Then acceptance와 fixture |
| `tasks/implementation-backlog.yaml` | 선행 관계가 있는 구현 backlog |
| `traceability/requirements.csv` | Requirement→rule→operation→data→AC→task→test |
| `data/recommendation-exposure-schema.md` | 실제 노출 batch/item typed snapshot과 중복 의미 |
| `scripts/validate-contract.mjs` | 문서 집합의 구조·참조·DAG 검증 |

## 5. C2A 승인 범위와 후속 Gate

- C2A는 Popularity-only 내부 service와 `starPolicy=DISABLED`를 구현할 수 있다.
- `C2_AUTH_MODE=fake`인 local test에서만 공개 fixture credential
  `test-c2-service-token`(허용)과 `test-c2-forbidden-token`(인증됐지만 권한 없음)을 사용한다.
  mode 미설정은 fail-closed이며 실제 운영 credential은 금지한다.
- FastAPI artifact serving bundle이 실제로 export되고 네 payload checksum이 고정돼야 readiness를 200으로 올린다.
- C1 `1..5`와 현재 MovieLens artifact `0.5..5.0` 불일치는 REC-EV-003C의 fail-closed 결정을 유지한다.
- batch candidate artifact의 producer/retention 계약이 작성됨
- REC-EV-007 local-loopback에서 timeout 750 ms·healthy freshness 3000 ms 후보가 선택됨. 운영
  topology 재검증 전에는 SLA로 부르지 않음
- fragment lint, C2 validator, contract test가 통과함
- main OpenAPI 병합, public Spring 응답, expected-star, reason UI는 별도 승인 변경으로 수행한다.

## 6. 검증

```powershell
node docs/c2-recommendation/scripts/validate-contract.mjs
npx redocly lint docs/c2-recommendation/api/openapi.fragment.yaml
```
