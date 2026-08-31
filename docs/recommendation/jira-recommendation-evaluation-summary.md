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

8. **현재 판정**
   cohort·slate preflight는 PASS. 기준선·후보 paired power는 예측 artifact 부재로 BLOCKED. cold-item
   firewall은 PASS AFTER FIX지만 REC-EV-019B TMDB feature manifest가 없어 모델 실행은 BLOCKED.
   현재 제품 추천은 popularity-only를 유지한다.

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

### 결정

- 개인화 champion 미선정
- 예상 별점 공개 미승인
- 현재 popularity-only 유지
- Locked Test 성능 미실행
- 다음 선행 작업은 REC-EV-019B TMDB feature build

### 근거 문서

- `docs/recommendation/FEELM-recommendation-evaluation-final-report.md`
- `docs/recommendation/02-top2-risk-aware-evaluation-design.md`
- `docs/recommendation/03-content-cold-item-evaluation-design.md`
- `docs/recommendation/evidence/REC-EV-020P-top2-v4-validation-preflight.md`
- `docs/recommendation/evidence/REC-EV-021P-content-cold-v2-preflight.md`
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
**막힘:** 비교 prediction artifact 없음, REC-EV-019B 선행 필요

### 5. cold-item firewall·density panel preflight

**제목**
`[추천] REC-EV-021P item firewall과 density panel 표본 검증`

**완료 조건**

- strict item 역할과 density 역할 교차표
- protected collision 0
- 3개 panel·5 fold 표본 수
- compute plan과 golden fixture

**상태:** DONE / FIREWALL PASS / MODEL BLOCKED

### 6. TMDB feature artifact

**제목**
`[데이터] REC-EV-019B TMDB identity·structured·text feature build`

**완료 조건**

- identity coverage ≥99.8% link present, verified/recovered ≥98%
- structured feature eligible ≥95%
- text feature eligible ≥95%
- TMDB popularity·vote·provider를 취향 feature에서 제외
- cache·resume·checksum·quarantine

**상태:** TODO / NEXT

## Jira 최종 댓글 템플릿

```text
[결과]
- Validation cohort/slate preflight: PASS
- K10 structural users: 16,795
- K10 Miss non-null users: 16,516 (98.34%)
- cold protected role collision: 6,964 → 0
- personal champion: NOT SELECTED
- product policy: popularity-only 유지

[막힘]
- REC-EV-019B TMDB feature manifest 없음
- pre-endpoint locked baseline/challenger prediction artifact 없음

[다음]
1) REC-EV-019B build/verify
2) K10 Validation baseline vs one challenger prediction lock
3) paired power 계산
4) Gate 통과 시에만 Locked Test 1회

[근거]
docs/recommendation/FEELM-recommendation-evaluation-final-report.md
```
