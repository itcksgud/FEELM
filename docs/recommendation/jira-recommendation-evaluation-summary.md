# Jira 기록용 — 추천 평가 재설계와 데이터 분석

> 이 문서는 Jira에 그대로 복사할 수 있는 내용이다. 실제 Jira 이슈를 생성·수정한 것은 아니다.

## 권장 Epic

**제목**
`[추천] MovieLens·TMDB 기반 Top-2 평가 재설계 및 실행 준비`

**목표**
사용자가 거의 없는 초기 서비스에서도 미평가를 싫어요로 오해하지 않고, FEELM이 실제로 보여 주는 상위
2편의 위험과 놓침을 재현 가능하게 비교한다. 한국·신작 영화는 MovieLens 행동과 TMDB 영화 정보를 분리해
별도 cold-item 실험으로 검증한다.

**배경**

- 기존 sampled Recall/NDCG는 상위 2편의 실패를 직접 설명하지 못함
- MovieLens timestamp는 감상 시점이 아닐 수 있음
- 미평가 영화의 만족도는 UNKNOWN임
- MovieLens 한국-origin 평점 비중은 0.28%로 희소함
- 예상 별점 개선과 추천 순위 개선이 기존 실험에서 다르게 움직임

**완료 조건**

- 사용자·영화 역할 split, seed, 후보, label 정의가 versioned protocol로 고정됨
- Validation preflight가 원본 SHA-256과 함께 재현됨
- Locked Test 결과를 보기 전에 기준선·후보 artifact가 잠김
- Top-2 Harm/Miss와 segment Gate가 계산됨
- TMDB 특징과 cold-item firewall이 검증됨
- 결과 보고서에 가능한 주장과 금지 주장이 구분됨

## 시작부터 현재까지 요약 댓글

아래를 Epic 설명 또는 최신 댓글에 붙인다.

---

### 진행 요약

1. **문제 제기**
   MovieLens에 없는 영화와 한국 영화가 적고, 미평가 영화의 만족 정답이 없는데 기존 Top-500 중심 지표로
   추천 성능을 설명하기 어렵다는 문제를 정의했다.

2. **기존 증거 재검토**
   REC-EV-003B에서 K가 늘수록 예상 별점 MAE는 개선됐지만, 추천 ranking은 모든 K에서 인기도 기준을
   선택했다. 예상 별점과 추천 순위를 분리해야 한다고 결정했다.

3. **평가 목표 변경**
   FEELM이 한 번에 2편을 보여 주므로 primary를 Top-2 Harm(싫어한 영화 노출)과 Miss(좋아한 영화 놓침)로
   변경했다. 미평가는 UNKNOWN으로 유지했다.

4. **사용자 평가판 설계**
   사용자의 관측 평점 중 20편을 label과 무관한 고정 hash로 뽑고, 나머지 중 K편만 모델 입력으로 사용했다.
   사용자별 평점 성향을 반영한 상대 utility로 GOOD/BAD를 만들었다.

5. **독립 검증 반복**
   여러 새 대화 세션에서 protocol을 공격 검토해 bootstrap 단위, human agreement, NDCG gain, power 정의를
   수정했다. 수식은 golden fixture로 고정했다.

6. **전체 데이터 사전검사 실행**
   MovieLens 32,000,204개 평점을 스캔했다. Validation 사용자 20,271명, K10 구조 적격 16,795명,
   Miss 채점 가능 16,516명(98.34%)을 확인했다. 원본 사용자 ID는 출력하지 않았다.

7. **cold-item firewall 결함 발견·수정**
   strict split과 density split이 6,964편에서 충돌했다. density 역할을 strict ITEM_TRAIN 안에서만
   나누도록 수정해 재검사 충돌을 0편으로 만들었다.

8. **TMDB 전체 특징 실행**
   100편 사전검사 후 Base 역할 사용자의 넓은 특성 집합 69,603편 전체를 실행했다. 링크 99.86%, identity 98.80%, 구조 특징
   99.31%, 텍스트 특징 99.80%로 Gate를 통과했다. 키워드 결측 24.42%는 missing mask와 fallback으로
   보존했다.

9. **시간 안전 cohort 전체 생성**
   cutoff 이전 Base Train 10,254,572개 평점에서 42,123편 1차 후보를 만들고 TMDB 신원 확인과 합쳐
   최종 후보 41,625편을 고정했다. K10 Locked Test 적격은 5,476명으로 5,000명 Gate를 통과했다.
   69,603편 feature 집합은 후보가 아니라는 경계도 계약과 검증기에 반영했다.

10. **현재 판정**
   cohort·slate preflight와 TMDB feature build는 PASS. cold-item preflight도 재실행해 Validation pilot
   READY가 됐다. REC-EV-019A·019B는 DONE이고 019C 실행 계약·합성 검사·Linux 의존성 검사도 PASS다. Validation과
   Locked Test가 한 파일에 섞인 물리 경계를 발견해 역할별 파일로 분리했다. 기준선·후보 paired power는
   prediction artifact 부재로 계속 BLOCKED이며, 다음은 실제 Validation runner 구현만 GO다. 현재 제품 추천은
   popularity-only를 유지한다.

### 핵심 숫자

- MovieLens 평점: 32,000,204개
- Validation 사용자: 20,271명
- K10 평가 가능: 16,795명
- K10 Miss 채점 가능: 16,516명 / 98.34%
- TMDB 한국-origin proxy: 11,680편
- MovieLens 연결 한국-origin: 1,056편
- 한국-origin 평점: 90,885개 / 전체 0.28%
- density panel: q≥5 3,662편 / q≥20 1,963편 / q≥100 994편
- 발견한 protected role 충돌: 6,964편 → 0편
- TMDB 넓은 특성 집합: 69,603편 / 링크 69,508편(99.86%)
- cutoff-safe 1차 후보: 42,123편 / 신원 확인 최종 후보: 41,625편
- K10 Locked Test 엄격 적격: 5,476명 / 최소 5,000명 PASS
- identity 확인·복구: 68,674편(98.80%)
- 구조 특징: 68,201편(99.31%) / 텍스트 특징: 68,534편(99.80%)
- 가장 큰 필드 결측: 키워드 16,772편(24.42%)

### 결정

- 개인화 champion 미선정
- 예상 별점 공개 미승인
- 현재 popularity-only 유지
- Locked Test 성능 미실행
- REC-EV-019A·019B DONE, REC-EV-019C 계약 PASS
- REC-EV-019C 합성 runner 15개·Linux dependency 9개 검사 PASS
- 다음 작업은 실제 Validation runner 구현·검토; 실제 실행은 아직 금지

### 근거 문서

- `docs/recommendation/FEELM-recommendation-evaluation-final-report.md`
- `docs/recommendation/02-top2-risk-aware-evaluation-design.md`
- `docs/recommendation/03-content-cold-item-evaluation-design.md`
- `docs/recommendation/evidence/REC-EV-020P-top2-v4-validation-preflight.md`
- `docs/recommendation/evidence/REC-EV-021P-content-cold-v2-preflight.md`
- `docs/recommendation/evidence/REC-EV-019C-contract-readiness.md`
- `docs/recommendation/evidence/REC-EV-019C-runner-and-dependency-preflight.md`
- `docs/recommendation/data-insights-summary.md`

---

## 권장 하위 이슈

### 1. 데이터 역할·한국 영화 coverage 감사

**제목**
`[분석] MovieLens 사용자 신호와 TMDB 영화 정보 역할 분리`

**완료 조건**

- MovieLens는 평점 행동, TMDB는 영화 특징으로 역할 명시
- 한국-origin coverage와 한계 기록
- 미평가 UNKNOWN 규칙 반영
- 결과: `REC-DATA-002`, `data-insights-summary.md`

**상태:** DONE

### 2. Top-2 v4 protocol·계약 구현

**제목**
`[추천] Top-2 Harm/Miss 평가 protocol과 artifact contract 구현`

**완료 조건**

- 20개 seed, 10/20/30 slate 민감도
- user-disjoint split
- 상대 utility GOOD/BAD
- schema·contract·unit test·verifier
- Locked Test 미사용

**상태:** DONE

### 3. Top-2 Validation cohort preflight

**제목**
`[분석] REC-EV-020P-A Validation cohort·slate 실행`

**완료 조건**

- 32M 원본 checksum 검증
- K별 구조 적격·opportunity 집계
- 원본 user ID 비저장
- manifest checksum 검증

**상태:** DONE / PASS

### 4. 기준선·후보 paired power

**제목**
`[추천] REC-EV-020P-B 인기도 vs 단일 후보 paired power`

**완료 조건**

- 기준선 artifact URI·SHA-256
- 후보 하나와 선택 규칙을 endpoint 계산 전 lock
- K별 Harm/Miss paired variance와 필요 Test n
- 불충분 시 margin을 넓히지 않고 INCONCLUSIVE

**상태:** BLOCKED
**막힘:** 비교 prediction artifact 없음. REC-EV-019C 실행 계약과 Validation prediction 선행 필요

### 5. cold-item firewall·density panel preflight

**제목**
`[추천] REC-EV-021P item firewall과 density panel 표본 검증`

**완료 조건**

- strict item 역할과 density 역할 교차표
- protected collision 0
- 3개 panel·5 fold 표본 수
- compute plan과 golden fixture

**상태:** DONE / PASS / VALIDATION PILOT READY

### 6. TMDB feature artifact

**제목**
`[데이터] REC-EV-019B TMDB identity·structured·text feature build`

**완료 조건**

- identity coverage ≥99.8% link present, verified/recovered ≥98%
- structured feature eligible ≥95%
- text feature eligible ≥95%
- TMDB popularity·vote·provider를 취향 feature에서 제외
- cache·resume·checksum·quarantine

**상태:** DONE / PASS_FULL_GATES

**사전검사 결과**

- identity 99/100 (99.0%)
- structured 99/99 (100%)
- text embedding 99/99 (100%)
- IMDb 불일치 1편은 자동 격리
- cache resume, credential 비노출, 고정 ONNX SHA-256 검증 PASS

**전체 결과**

- 넓은 특성 집합 69,603편 / TMDB 링크 69,508편(99.8635%)
- identity 확인·복구 68,674편(98.8001%)
- structured 68,201편(99.3112%)
- text embedding 68,534편(99.7961%)
- 키워드 결측 16,772편(24.42%), 배우 결측 2,336편(3.40%)
- 전체 TMDB 수집 약 2시간 6분, cache 112,580개
- embedding checkpoint·resume, artifact checksum, secret scan PASS

### 7. Binary onboarding cohort artifact

**제목**
`[추천] REC-EV-019A 사용자 분리 cohort와 시간 안전 후보 생성`

**완료 조건**

- Base/Router/Validation/Locked Test 사용자 역할이 겹치지 않음
- K5가 K10에 포함되고 미래 평가 창이 정확히 10개
- 미평가·중립을 DISLIKE로 변환하지 않음
- cutoff-safe 후보와 TMDB 신원 확인 후보의 교집합 고정
- 원본 사용자 ID 비저장, checksum verifier PASS

**상태:** DONE / PASS_COHORT_GATES

**결과**

- Base Train 68,161명 / 평점 10,254,572개
- cutoff-safe 영화 42,203편 / TMDB 링크 1차 후보 42,123편
- TMDB 신원 확인 최종 후보 41,625편
- Locked Test K10 엄격 적격 5,476명 / Gate 5,000명 PASS
- Locked Test 모델 예측·성능 미사용, 제품 정책 변경 없음

### 8. 모델 비교 실행 계약과 Test 파일 firewall

**제목**
`[추천] REC-EV-019C Validation 실행 계약·입력 격리`

**완료 조건**

- Validation runner가 역할별 Validation 파일만 읽도록 allowlist 고정
- 혼합 역할·Router·Locked Test·원본 Test 파일은 open 전에 거부
- 7개 모델의 trial 수·score 정규화·B0 fallback·RRF rank-only 고정
- checkpoint·resume·실패 보존·선택 lock schema 고정
- 실제 Validation·Locked Test·제품 champion은 승인하지 않음

**상태:** SYNTHETIC_AND_DEPENDENCY_PREFLIGHT_PASS / REAL_RUNNER_IMPLEMENTATION_ONLY

**결과**

- 019A에 Router/Validation/Locked Test 역할별 prefix·window 파일 추가
- 최종 후보 41,625편과 K10 Locked Test 5,476명 불변
- 계약 validator와 9개 공격 변이 테스트 PASS
- runner 합성 15개 검사와 Linux dependency 9개 검사 PASS
- LightFM BPR/WARP 계약 충돌을 발견해 B8을 관측 ±1 logistic+frozen-item fold-in으로 교정
- 실제 Validation 실행은 실제 runner 구현·검토 전까지 BLOCKED

## Jira 최종 댓글 템플릿

```text
[결과]
- Validation cohort/slate preflight: PASS
- K10 structural users: 16,795
- K10 Miss non-null users: 16,516 (98.34%)
- cold protected role collision: 6,964 → 0
- REC-EV-019A cohort gates: PASS (final candidates 41,625 / K10 users 5,476)
- REC-EV-019B full feature-superset gates: PASS (69,603 movies)
- identity/structured/text: 98.80% / 99.31% / 99.80%
- REC-EV-021P: PASS / READY_FOR_VALIDATION_PILOT
- REC-EV-019C preflight: PASS (synthetic 15 / Linux dependency 9)
- personal champion: NOT SELECTED
- product policy: popularity-only 유지

[막힘]
- pre-endpoint locked baseline/challenger prediction artifact 없음
- REC-EV-019C 실제 runner와 Validation prediction 미생성

[다음]
1) REC-EV-019C 실제 입력 adapter·모델·block scorer·checkpoint·verifier 구현
2) 실제 데이터 dry-run과 코드 검토 후 Validation 실행 승인 재판단
3) Validation baseline vs one challenger prediction lock
4) 동일한 최종 후보 41,625편에서 paired power 계산
5) Gate 통과 시에만 Locked Test 1회

[근거]
docs/recommendation/FEELM-recommendation-evaluation-final-report.md
```
