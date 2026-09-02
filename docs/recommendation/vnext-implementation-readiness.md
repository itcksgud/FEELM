# FEELM 추천 vNext 구현 준비도

> 상태: `APPROVED` — 오프라인 추천 evidence 구현 착수 기준
> 판정일: 2026-09-02
> 최종 판정: **GO — REC-EV-019A·019B 완료, 다음 범위는 REC-EV-019C 실행 계약 작성**
> 제품 경계: 현재 C2 popularity-only 교체와 개인화 champion 승인은 이 GO에 포함하지 않는다.
> 후속 경계: Top-2 v4와 cold-item v2는 `PROPOSED_PROTOCOL_VALIDATION_PREFLIGHT_REQUIRED`다. 별도
> Schema·artifact contract·runner 구현은 시작할 수 있지만 현재 019C 계약 작성 GO나
> Locked Test 실행 GO에 포함되지 않는다.

## 1. GO의 정확한 의미

이 판정은 새 LLM 세션이 이전 대화 없이 저장소만 읽고 다음 작업을 선택·구현·검증할 수 있다는 뜻이다.

- binary 온보딩 cohort artifact는 `PASS_COHORT_GATES`로 완료
- 전체 TMDB feature artifact는 `PASS_FULL_GATES`로 완료
- 강한 기준 모델과 full-catalog evaluator는 019C 실행 계약을 먼저 고정한 뒤 시작
- REC-EV-019~026 evidence는 backlog dependency Gate를 순서대로 통과

모델이 아직 실험 Gate를 통과하지 않았다는 이유로 구현 자체를 막지 않는다. 반대로 구현 준비가 됐다는
이유로 성능 검증 전 모델을 제품 기본값으로 연결하지 않는다.

| 판정 대상 | 상태 |
| --- | --- |
| 추천 vNext 오프라인 구현 착수 | **GO** |
| REC-EV-019A cohort 전체 생성 | **DONE — 최종 후보 41,625편, strict K10 Test 5,476명** |
| REC-EV-019B 전체 특징 | **DONE — 69,603편 feature superset coverage Gate PASS** |
| REC-EV-019C 실행 계약 작성 | **GO** |
| REC-EV-019C 모델 실행 | `NO-GO`, artifact schema·탐색 순서·checkpoint·복구 계약 필요 |
| REC-EV-020P-A/B 설계→계약 구현 | `GO`, v4 Schema·artifact contract·runner 구현 가능 |
| REC-EV-020P-A/B 실행 완료 판정 | `NO-GO`, runner·verifier·Validation power 결과 필요 |
| REC-EV-021P 사전검사 | **DONE — firewall PASS, Validation pilot READY** |
| REC-EV-021 전체 grid·Locked Test | `NO-GO`, 소규모 Validation 실행 시간·적용 모델 Gate 필요 |
| binary 개인화 champion | `null`, 실험 결과 대기 |
| 예상 별점 public 노출 | `NO` |
| C2 기본 정책 교체 | `NO`, 별도 vNext 승인 필요 |

## 2. 이전 NO-GO 차단 항목 해소

| 기존 차단 | 해소 근거 |
| --- | --- |
| 온보딩과 Rating 혼합 | [입력 신호 계약](./00-input-signal-contract-vnext.md)에서 `K_b`/`K_r` 분리 |
| 승인 C2와 충돌 | [Serving 계약](./serving-contract.md)에서 현재 경로와 vNext 격리 |
| split·candidate 미고정 | [평가 프로토콜](./01-offline-evaluation-protocol-vnext.md)과 JSON protocol 고정 |
| 약한 preflight의 false-GO | positive 3개·candidate-positive를 구현하고 [REC-EV-019P v2](./evidence/REC-EV-019P-binary-onboarding-preflight.md) strict 5,476명으로 교정 |
| candidate 결측 정책 충돌 | identity quarantine만 공통 제외하고 모델 feature 누락은 B0 fallback으로 통일 |
| 기준 모델 부족 | Bias·ItemKNN·ALS·BPR·EASE·TMDB Content·LightFM·RRF 고정 |
| score 척도 혼합 | raw score 합산 금지, RRF 우선 |
| 유사 영화 순환 평가 | feature 제거 ablation + 독립 사람 pair gate |
| 발견 실패 미반영 | REC-EV-013 null을 baseline으로 고정 |
| 합성 파티 과장 | stress test와 실제 그룹 만족 champion 분리 |
| OTT deep link 오해 | TMDB watch URL·attribution과 provider link 구분 |
| 통계 기준 미정 | SESOI·non-inferiority·Holm·계층 bootstrap 고정 |
| 실행 task 부재 | [추천 evidence backlog](../tasks/recommendation-evidence-backlog.yaml)에 의존성·산출물·검증 명령 고정 |
| serving 문서 부재 | [Serving 계약](./serving-contract.md) 생성 |
| 기존 evidence checksum 실패 | REC-EV-017 manifest 교정 후 전체 evidence 검증 통과 |

## 3. 단일 기준 문서

새 작업은 다음 순서로 읽는다.

1. `AGENTS.md`
2. [이 준비도 문서](./vnext-implementation-readiness.md)
3. [입력 신호 계약](./00-input-signal-contract-vnext.md)
4. [평가 프로토콜](./01-offline-evaluation-protocol-vnext.md)
5. [기계 판독 protocol](./protocols/rec-eval-vnext.json)
6. [추천 Serving 계약](./serving-contract.md)
7. [구현 backlog](../tasks/recommendation-evidence-backlog.yaml)
8. 선택한 task가 직접 참조한 문서와 기존 evidence

연구 설명이 위 계약과 다르면 위 순서의 계약을 따른다. Top-2 v4와 cold-item v2는 다음 protocol
후보이지만 현재 019A/019B 구현 계약을 바꾸지 않는다. 현재 `APPROVED` C2 제품 계약은 vNext가 별도
승격되기 전까지 계속 더 높은 구현 권위를 가진다.

- [Top-2 v4 proposed protocol](./02-top2-risk-aware-evaluation-design.md)
- [cold-item v2 proposed protocol](./03-content-cold-item-evaluation-design.md)

## 4. 첫 두 작업

### 4.1 `TASK-REC-EV-019A` — 완료

목적은 full model 실행 전에 user-disjoint binary cohort를 artifact로 고정하는 것이다.

기계 판독 계약: [`contracts/rec-ev-019a-artifacts.json`](./contracts/rec-ev-019a-artifacts.json)

```text
입력
  global-time-v1 Train/Validation/Test parquet
  rec-eval-vnext.json

출력
  Base Train interactions
  Router/Validation/Test K5·K10 nested binary prefixes
  K0·K5·K10 future rating labels와 strict eligibility
  split/candidate protocol lock

금지
  raw user ID tracked 저장
  미평가·중립을 DISLIKE로 변환
  Test 결과를 본 threshold 변경
```

전체 실행 결과 Base Train은 68,161명·10,254,572개 평점이고, cutoff-safe 링크 후보는 42,123편이다.
019B 신원 확인을 적용한 최종 후보는 41,625편, strict K10 Locked Test는 5,476명으로 최소 5,000명 Gate를
통과했다. 자세한 결과는 [`REC-EV-019A`](./evidence/REC-EV-019A-binary-cohort-build.md)에 있다.

### 4.2 `TASK-REC-EV-019B` — 완료

목적은 Base 역할 사용자가 전체 기간에 평가한 넓은 영화 집합의 TMDB structured/text feature를 만드는
것이다. API token은 `.env.local`에서만 읽고 문서·로그·artifact에 기록하지 않는다. 이 집합은 시간 cutoff
뒤 영화도 포함하므로 recommender candidate 권한은 없다.

기계 판독 계약: [`contracts/rec-ev-019b-artifacts.json`](./contracts/rec-ev-019b-artifacts.json)

- text model: `intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3`
- embedding: 384차원 float32, mean pooling, L2 normalization
- identity allowlist: `ML_TMDB_VERIFIED`, `RECOVERED_BY_IMDB`
- identity quarantine은 공통 candidate에서 제외하지만, structured/text feature 누락은 candidate를
  제거하지 않고 B0 fallback을 기록한다.
- 캐시·429/5xx retry·100편 checkpoint·resume·coverage Gate는 artifact 계약 값을 따른다.

전체 69,603편 feature superset 실행에서 링크 99.8635%, identity 98.8001%, 구조 특징 99.3112%, 텍스트 특징
99.7961%로 Gate를 통과했다. 결과와 실패·복구 과정은
[`REC-EV-019B 전체 결과`](./evidence/REC-EV-019B-tmdb-feature-build.md)에 기록했다. 019A와의 교집합과 K10
5,000명 Gate도 이미 확인했다. 다음은 이를 변경하지 못하도록 019C 실행 계약을 작성하는 단계다.

### 4.3 `TASK-REC-EV-019C` — 계약 작성만 시작 가능

backlog에는 비교할 모델과 큰 Gate가 있지만, 아직 모델별 입력·출력 schema, 최대 30회 탐색의 정확한 순서,
중간 checkpoint, 실패 후 재개, Validation 선택 결과 lock 형식이 없다. 따라서 현재 준비도는 다음과 같다.

- 계약·runner·test·verifier 골격 작성: `GO`
- Validation 모델 실행: `NO-GO` until contract validation passes
- Locked Test 모델 성능: `NO-GO`
- 제품 champion·기본 정책 변경: `NO-GO`

## 5. 완료 명령

환경 설치:

```powershell
py -3 -m pip install -r requirements-data.txt
py -3 -m pip install --require-hashes -r requirements-ml.lock
npm ci
```

준비도와 기존 evidence:

```powershell
npm run recommendation:vnext:readiness:check
npm run recommendation:evidence:check
```

REC-EV-019A 로컬 대용량 artifact 검증:

```powershell
py -3 -m unittest scripts/tests/test_build_rec_ev_019a_cohorts.py
py -3 scripts/verify_rec_ev_019a_cohorts.py --manifest docs/recommendation/evidence/manifests/rec-ev-019a.json
```

REC-EV-019 preflight 재생성:

```powershell
$env:PYTHONPATH='scripts'
py -3 scripts/recommendation_binary_onboarding_preflight.py
py -3 scripts/verify_recommendation_binary_onboarding_preflight.py
Remove-Item Env:PYTHONPATH
```

준비도 검증은 실제 secret 없이 추적된 019B manifest와 contract checksum을 검사한다. 로컬 artifact
checksum·schema·coverage는 019B verifier가 검사한다. TMDB 전수 재수집만 실제 token과 네트워크가
필요하며, 기존 cache가 있으면 network request 없이 재검증할 수 있다.

## 6. LLM이 임의로 결정하면 안 되는 것

다음 값은 이미 고정됐으므로 질문하거나 바꾸지 않는다.

- 온보딩은 최대 10개의 LIKE/DISLIKE
- binary를 별점으로 변환하지 않음
- split `40/10/10/40`
- K10 Test 최소 5,000명
- full catalog, positive injection 금지
- ranking primary `NDCG@10`
- SESOI 절대 0.002·상대 5%
- 핵심 segment non-inferiority `-0.002`
- RRF를 첫 fusion 기준선으로 사용
- binary-only 예상 별점 `null`
- provider filter가 없으면 OTT join 전후 rank 불변
- 현재 제품 fallback은 popularity-only

다음은 결과가 결정하며 미리 고정하지 않는다.

- 어떤 모델이 champion인가
- K5와 K10에서 같은 모델을 쓸지
- TMDB structured/text의 최종 가중치
- Router가 필요한지
- discovery·party policy가 제품 Gate를 통과하는지

## 7. Definition of Done

각 task는 다음을 모두 만족해야 `DONE`이다.

1. backlog에 적힌 입력·출력·검증을 충족한다.
2. protocol lock을 Test 실행 전에 생성한다.
3. 결과·manifest·보고서와 artifact SHA-256이 일치한다.
4. 전체 평균과 사용자/영화 segment, B/T/H를 함께 기록한다.
5. 실패·null champion도 삭제하지 않는다.
6. 현재 제품 fallback을 바꾸지 않는다.
7. `npm run recommendation:evidence:check`를 통과한다.
8. 019C verifier는 019B identity allowlist 적용 strict K10 Test 5,476명과 최종 후보 41,625편이 바뀌지 않았는지 확인한다.

## 8. Blind handoff용 시작 프롬프트

```text
C:\higher\projects\FEELM-standalone에서 TASK-REC-EV-019C의 실행 계약을 작성해.
AGENTS.md와 docs/recommendation/vnext-implementation-readiness.md를 먼저 전부 읽고,
docs/tasks/recommendation-evidence-backlog.yaml의 해당 task 범위·산출물·검증을 그대로 따라.
REC-EV-019A의 최종 후보 41,625편과 K10 Test 5,476명을 바꾸지 마. 모델별 입력·출력 schema,
Validation 최대 30회 탐색 순서, checkpoint·resume, 실패 보존, parameter lock과 검증 명령을 JSON 계약에
고정하고 계약 validator와 단위 테스트까지만 완료해. 실제 모델 학습·Validation 성능·Locked Test는 실행하지 마.
제품 API·DB·현재 popularity-only 정책은 변경하지 말고 protocol JSON에 고정된 값을 사용해.
commit과 push는 하지 마.
```

이 프롬프트 외의 과거 대화 설명이 없어도 task를 수행할 수 있어야 한다.
