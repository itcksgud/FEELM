# FEELM 추천 오프라인 평가 프로토콜 vNext

> 문서 상태: `APPROVED` — REC-EV-019 이후 오프라인 구현·판정 기준이다.
> 개정일: 2026-08-30
> 제품 영향: 없음. 이 문서만으로 현재 C2 기본 정책을 교체하지 않는다.
> 입력 기준: [추천 입력 신호 계약 vNext](./00-input-signal-contract-vnext.md)

> 후속 판정: 이 v2 문서는 REC-EV-019A/019B 실행 계약으로 보존한다. 개인 추천 champion의 차기 판정은
> [Top-2 위험 회피 추천 설계](./02-top2-risk-aware-evaluation-design.md)와
> [`rec-eval-top2-v4.json`](./protocols/rec-eval-top2-v4.json)을 preflight한 뒤 적용한다. 현재 v4는
> `PROPOSED_PROTOCOL_VALIDATION_PREFLIGHT_REQUIRED`이며 Locked Test 실행 계약이 아니다.

기계 판독 가능한 초깃값은
[`protocols/rec-eval-vnext.json`](./protocols/rec-eval-vnext.json)에 고정한다. 두 파일이 다르면 사람이
읽는 이 문서를 기준으로 JSON을 수정하고 새 protocol version을 부여한다.

## 1. 검증 목표와 금지된 주장

이 프로토콜은 다음 네 제품 기능을 서로 다른 문제로 평가한다.

| Head | 검증 질문 | 오프라인으로 주장 가능한 범위 |
| --- | --- | --- |
| 개인 맞춤 | 제한된 binary/rating 입력이 미래 관측 선호의 순위를 개선하는가? | MovieLens 신규 사용자 proxy에서의 ranking 개선 |
| 유사 영화 | TMDB 정보만으로 독립 관계·사람 판단과 가까운 영화를 찾는가? | 정해진 유사성 기준에 대한 검색 품질 |
| 취향 발견 | relevance 손실 한도 안에서 미경험 feature를 늘리는가? | relevance–novelty Pareto 개선 |
| 파티 | 고정 집계식이 구성원 평균·최저 효용을 어떻게 바꾸는가? | 합성 그룹 stress test; 실제 그룹 만족도 아님 |

OTT availability와 XAI는 별도 품질 gate다. MovieLens에서 평가하지 않은 영화의 만족도, FEELM 실제
사용자 만족도, MovieLens rating timestamp를 관람 순서로 해석하는 주장은 금지한다.

## 2. 고정 원천

| 원천 | 고정 값 |
| --- | --- |
| MovieLens archive | `ml-32m.zip` |
| archive SHA-256 | `e4a68655d7386b8f95f2f2424b2ff975dfdd15ffd59e0d864a14dca43e99d6ee` |
| 기존 전역 시간 manifest | `evidence/manifests/global-time-v1.json` |
| Train boundary | `1538551305` (`2018-10-03T07:21:45Z`) |
| Validation boundary | `1604605535` (`2020-11-05T19:45:35Z`) |
| 기본 seed 목록 | `17, 42, 73, 101, 211` |
| raw user ID 보존 | 금지; salted hash 또는 집계만 저장 |

현재 TMDB 수집물은 과거 시점 snapshot이 아니다. 따라서 현재 metadata로 과거 cutoff를 평가하는 실험은
`CURRENT_METADATA_RETROSPECTIVE`라고 표시한다. release/catalog 시점의 metadata snapshot을 확보하기
전에는 “출시 당시 알 수 있던 정보만 사용했다”고 주장하지 않는다.

## 3. 사용자 분리

### 3.1 고정 user-disjoint assignment

각 MovieLens `userId`에 다음 digest를 적용한다.

```text
bucket = uint64_be(
  SHA256("feelm-rec-vnext-user-split-v1|" + userId)[0:8]
) mod 100
```

| bucket | 역할 | 비율 |
| --- | --- | ---: |
| `0..39` | Base model Train | 40% |
| `40..49` | Router/stacker Train | 10% |
| `50..59` | Validation | 10% |
| `60..99` | Locked Test | 40% |

- Router/Validation/Test 사용자의 rating은 base ALS, BPR, EASE, ItemKNN 학습에 한 건도 넣지 않는다.
- assignment는 모든 K와 모든 cutoff에서 유지한다. `REC-EV-019P v1`의 30% Test는 K10·미래 10개만
  검사해 5,267명을 보고했지만, v2의 positive 3개와 candidate-positive까지 적용한 같은 30% bucket
  subset은 4,112명뿐이었다.
  모델 결과를 실행하기 전에 40% Test로 교정했고, 동일한 엄격 조건에서 5,476명을 확보했다.
- 사용자 특성 층은 각 cutoff 이전 데이터로만 계산하고, split 균형 감사에 사용한다.
- Test raw user ID는 결과 파일에 저장하지 않는다.

### 3.2 평가 eligible 조건

개인 ranking 실험 사용자는 다음을 모두 만족해야 한다.

1. 요구한 `K_b` 또는 `K_r` 입력을 cutoff 이전에서 만들 수 있다.
2. cutoff 이후 관측 rating이 10개 이상이다.
3. 그중 사용자 상대 효용 positive가 3개 이상이다.
4. positive 중 하나 이상이 아래 core candidate universe에 존재한다.

`REC-EV-019P`에서는 `links.csv`의 parse 가능한 TMDB ID를 provisional identity로 사용한다. `019B`가
`ML_TMDB_VERIFIED`/`RECOVERED_BY_IMDB`를 생성한 뒤 `019C` protocol lock에서 최종 identity를 다시
적용한다. 그 결과 K10 Test가 5,000명 미만이면 모델 실행을 강행하지 않고 `INCONCLUSIVE`로 남긴다.

모델별로 사용자를 따로 제거하지 않는다. 한 모델이 계산 불가능하면 그 모델의 fallback을 실행하고
coverage/fallback rate로 기록한다.

## 4. 시간과 cohort

### 4.1 primary holdout

기존 `global-time-v1`을 첫 프로토콜 lock으로 재사용한다.

```text
Base Train:  timestamp < 1538551305, Base Train users만
Validation: 1538551305 <= timestamp < 1604605535, Validation users
Test:       timestamp >= 1604605535, Locked Test users
```

모델·가중치·threshold는 Validation까지만 보고 고정한다. Test를 한 번 연 뒤에는 동일 protocol version의
재튜닝을 금지하고 새 version을 만든다.

### 4.2 rolling robustness

추가 cutoff는 raw ratings 누적 행의 `60%`, `70%`, `80%` 지점 timestamp를 `higher` 방식으로 구한다.
같은 timestamp의 행은 전부 뒤 구간으로 보낸다. 실제 timestamp와 row count는 dry-run manifest에
기록한 뒤 모델 실행 전에 lock한다.

rolling cutoff는 primary Test를 대체하지 않고 시간 민감도와 분산 추정에만 사용한다. 동일 사용자가
여러 cutoff에 등장할 수 있으므로 통계 단위는 `user → cutoff` 계층을 보존한다.

## 5. 입력 생성

### 5.1 binary onboarding track

Primary K는 `K_b = 0, 5, 10`이다. `1, 3`은 학습 곡선 진단용이다.

- `FIRST_OBSERVED_BINARY_PROXY`와 `CURATED_POOL_BINARY_PROXY`를 별도 결과로 낸다.
- LIKE/DISLIKE 변환과 중립 제외는 입력 신호 계약 7장을 따른다.
- 같은 사용자의 K5는 K10의 prefix다.
- 두 class 존재율, pool intersection, eligible 탈락률을 저장한다.

### 5.2 explicit rating track

`K_r = 0, 1, 3, 5, 10, 20, 30, 50`을 평가한다.

- `K_r=0..10`은 cold-start 비교다.
- `K_r=20..50`은 장기 성숙도 진단이며 onboarding 성능과 분리한다.
- 사용자 상대 효용의 shrinkage `lambda ∈ {5,10,20}`은 Validation에서 선택한다.
- 같은 사용자의 K는 timestamp 정렬 prefix로 중첩한다.

## 6. 정답과 candidate universe

### 6.1 관측 정답

각 사용자·cutoff의 이후 rating 중 최초 10개를 evaluation window로 사용한다. evaluation label은 그
window 안에서 계산한다.

```text
positive: held-out mid-rank utility >= 0.65
negative diagnostic: held-out mid-rank utility <= 0.35
neutral: ranking relevance에서 제외
```

민감도 표에는 raw `rating >= 4.0`, `>= 4.5` 결과도 함께 낸다. primary 판정은 사용자 상대 label을
사용한다. label 계산은 평가에만 쓰며 모델 feature로 전달하지 않는다.

### 6.2 full catalog

개인 ranking의 core universe는 다음 교집합이다.

```text
cutoff 이전 Base Train에 등장한 movieId
∩ MovieLens links.csv에서 TMDB ID가 유효한 영화
- 목표 사용자의 입력·과거 관측 영화
```

- held-out positive를 후보에 강제로 추가하지 않는다.
- 모델마다 다른 universe를 쓰지 않는다.
- `links.csv`의 TMDB ID 존재는 preflight identity이고, 최종 universe는 019B의
  `ML_TMDB_VERIFIED`/`RECOVERED_BY_IMDB`만 허용한다. `TYPE_MISMATCH_TV`, `TMDB_NOT_FOUND`,
  `IDENTITY_REVIEW_REQUIRED`는 **모델 실행 전 공통으로** 격리한다.
- TMDB structured/text, ALS factor 등 **모델별 feature artifact 누락은 core universe 제외 조건이
  아니다.** 해당 모델은 B0 Bayesian popularity fallback을 실행하고 fallback reason을 기록한다.
- 따라서 “identity quarantine”과 “model feature missing”을 같은 결측으로 취급하지 않는다.
- `candidate_recall@500`과 최종 `NDCG@10`을 모두 보고한다.
- sampled-negative 결과는 탐색용일 뿐 채택 근거가 아니다.

## 7. 필수 기준 모델

| ID | 모델 | 주 목적 | 최소 탐색 공간 |
| --- | --- | --- | --- |
| `B0` | MovieLens Bayesian rating | 비개인화 fallback | prior strength `{25,50,100}` |
| `B1` | Global/User/Item Bias | 예상 별점 기준선 | regularization `{5,10,25}` |
| `B2` | ItemKNN | 해석 가능한 CF | neighbors `{50,100,200}`, shrink `{10,50,100}` |
| `B3` | Explicit ALS fold-in | rating CF | rank `{32,64,128}`, reg `{0.01,0.05,0.1}` |
| `B4` | BPR-MF | implicit top-N | factors `{64,128}`, reg `{1e-4,1e-3}` |
| `B5` | EASE | 강한 선형 item-item | lambda `{50,100,300,500}` |
| `B6` | TMDB Structured | cold-item/content | feature group ablation |
| `B7` | TMDB Text | semantic content | embedding version 고정 |
| `B8` | LightFM | binary+metadata hybrid | loss `{bpr,warp}`, dim `{64,128}` |
| `B9` | Rank Fusion | 독립 head 결합 | RRF c `{10,30,60}` |

각 후보는 같은 Validation 사용자·candidate·primary metric을 사용한다. 모델별 최대 trial 수는 30으로
제한하고 wall-clock, peak memory, artifact size도 저장한다. LightGCN은 위 기준선 중 하나 이상을
통계·실질 효과 모두에서 이긴 뒤 별도 evidence로만 추가한다. SASRec/BERT4Rec은 rating timestamp를
관람 sequence로 사용할 수 없으므로 현재 비교군에서 제외한다.

## 8. 공통 지표와 사용자 분포

### 8.1 primary와 secondary

| 구분 | 지표 |
| --- | --- |
| 개인 ranking primary | 사용자 macro `NDCG@10` |
| 개인 ranking secondary | `Recall@10`, `MRR@10`, positive mean rank percentile, candidate recall@500 |
| 예상 별점 | 사용자 macro `MAE`, `RMSE`, calibration by predicted bin |
| 안전성 | coverage, fallback rate, user-level Benefit/Tie/Harm |
| 분포 | ΔNDCG/Δrank p10·p25·p50·p75·p90 |

사용자별 practical tie band는 다음과 같다.

```text
NDCG Benefit: delta >= +0.001
NDCG Tie:    -0.001 < delta < +0.001
NDCG Harm:    delta <= -0.001

Rank Benefit: positive mean rank percentile delta >= +0.01
Rank Tie:    absolute delta < 0.01
Rank Harm:   delta <= -0.01
```

### 8.2 필수 segment

- `K_b`, `K_r`
- pre-cutoff history count
- rating mean/std와 relative positive rate
- TMDB metadata coverage
- 취향 feature entropy
- input/positive 영화의 train popularity quintile
- original language와 release decade
- user cold × item cold 교차 구간

segment는 Test 성능을 본 뒤 새로 잘라 champion을 주장할 수 없다. 탐색 segment는 다음 protocol
version의 사전 정의 후보로만 사용한다.

## 9. 통계·채택 gate

### 9.1 개인 ranking primary gate

challenger가 B0 또는 현재 champion을 교체하려면 모두 만족해야 한다.

1. Test eligible 사용자 `5,000`명 이상. 미만이면 `INCONCLUSIVE`.
2. 평균 절대 `ΔNDCG@10 >= 0.002`이면서 상대 개선 `>= 5%`.
3. user-paired hierarchical bootstrap 95% CI의 하한이 `0`보다 큼.
4. primary challenger 비교에 Holm 보정을 적용한 `alpha=0.05` 통과.
5. 사전 정의된 핵심 segment 각각에서 `ΔNDCG@10` CI 하한이 `-0.002` 이상.
6. coverage가 기준선보다 1 percentage point 이상 낮아지지 않음.

`0.002`는 vNext 최초 SESOI다. 제품 온라인 지표가 생기면 별도 ADR로 재설정하며, 현재 Test를 보고
낮추지 않는다.

### 9.2 bootstrap

- 기본 반복: `2,000`회
- 1차 resample: 사용자
- rolling 결과 결합 시 2차 단위: 사용자 안의 cutoff
- 같은 사용자의 모든 K와 모델 결과는 동일 resample에 묶음
- seed별 결과를 평균내기 전에 seed variance를 별도로 저장

### 9.3 예상 별점 gate

- B1 Bias를 primary baseline으로 사용한다.
- MAE 절대 개선 `>= 0.03`과 CI 상한 `< 0`을 모두 만족한다.
- `K_r`별 coverage와 calibration error를 공개한다.
- binary-only 사용자는 예상 별점 대상에서 제외한다.

### 9.4 Router gate

- Router feature와 label은 Router Train 사용자의 out-of-fold prediction으로 만든다.
- Validation에서 single-best-per-K와 Router를 선택하고 Test에서 한 번 비교한다.
- Router는 single-best 대비 개인 ranking primary gate를 동일하게 통과해야 한다.
- Oracle winner는 상한 진단일 뿐 제품 수치로 사용하지 않는다.

## 10. 기능별 독립 프로토콜

### 10.1 REC-EV-019 — binary onboarding bridge

- K: `K_b=0/5/10`
- 모델: B0, B2 binary variant, B4, B6, B7, B8, B9
- 두 proxy 결과가 방향까지 일치해야 제품 후보 유지
- 출력: proxy별 eligible/탈락률, NDCG, rank, B/T/H, 실제 영화 사례

### 10.2 REC-EV-020 — explicit rating maturity

- K: `K_r=0/1/3/5/10/20/30/50`
- 모델: B0~B3, B5~B7, B9
- ranking과 predicted rating을 별도 판정
- 출력: K 곡선, segment heatmap, fallback/coverage

### 10.3 REC-EV-021 — item cold-start

두 cohort를 섞지 않는다.

1. `FIRST_OBSERVED_INTERACTION_COLD`: cutoff 이전 interaction이 0인 영화. 실제 출시 신규 영화라고
   부르지 않는다.
2. `MASKED_COLD`: popularity strata별 기존 영화를 선택해 모든 interaction을 Train에서 제거한다.

현재 TMDB snapshot을 쓰면 결과에 `CURRENT_METADATA_RETROSPECTIVE`를 붙인다. B6/B7/B8이 대상이며,
item/user block bootstrap으로 relevance와 coverage를 판단한다.

### 10.4 REC-EV-022 — fusion과 Router

- raw ALS·BPR·cosine·popularity score 직접 가중합 금지
- RRF를 첫 결합 기준선으로 사용
- single best → fixed RRF → rule router → learned router 순서로 복잡도를 추가
- 앞 단계가 gate를 통과하지 못하면 뒤 단계는 제품 후보가 아님

### 10.5 REC-EV-023 — 유사 영화

모델 feature와 정답을 동일하게 정의하지 않는다.

- 정량 sanity: collection·sequel·동일 감독 등 각 관계를 해당 feature를 제거한 ablation으로 평가
- 독립 제품 gate: 최소 seed 영화 100편, 영화당 후보 pair 5개 이상, 사람 평가자 3명 이상
- relevance scale: `0 전혀 다름 / 1 일부 유사 / 2 핵심적으로 유사`
- 평가자 합치도와 query-level NDCG/MRR/coverage를 보고
- 사람 평가셋이 없으면 `RESEARCH_ONLY`; semantic similarity 품질 승인 금지

### 10.6 REC-EV-024 — 취향 발견

기존 `REC-EV-013`의 모든 relevance budget 실패를 baseline failure로 등록한다. 단순 가중치 재실행이
아니라 고정 개인 ranking 위의 constrained reranker만 비교한다.

| 후보 | 설명 |
| --- | --- |
| no rerank | 개인 추천 기준선 |
| MMR | relevance와 list diversity |
| calibrated rerank | 사용자 feature 분포와 결과 분포의 괴리 제한 |
| novelty-constrained | relevance floor를 지킨 후보에서 novelty 최대화 |

채택 조건:

- 개인 ranking `ΔNDCG@10` CI 하한 `>= -0.002`
- novelty 또는 intra-list diversity의 CI 하한 `> 0`
- genre/language/decade calibration divergence 증가 `<= 0.02`
- user Harm 비율과 사례 공개

오프라인 통과는 “발견 정책 후보”이며 실제 새 취향 만족을 증명하지 않는다.

### 10.7 REC-EV-025 — 파티

정책식을 Test 전에 고정한다.

```text
AVERAGE(i)      = mean_m percentilePreference(m, i)
LEAST_MISERY(i) = min_m percentilePreference(m, i)
BALANCED(i)     = 0.5 * mean_m + 0.4 * min_m - 0.1 * std_m
```

- 구성원 score는 사용자별 percentile로 맞춘 뒤 집계한다.
- 2·3·4명, 취향 유사/이질 그룹을 분리한다.
- 구성원은 합성 그룹 사이에서 재사용하지 않는다.
- mean/min utility, disparity, member Harm, candidate coverage를 보고한다.
- 합성 결과는 정책 stress test다. 실제 그룹 만족 champion은 사람 연구 전까지 `null`이다.

### 10.8 REC-EV-026 — OTT·XAI·fallback

- provider 미선택 요청에서 OTT join 전후 movie rank가 100% 동일해야 한다.
- 명시적 provider-only filter일 때만 eligibility를 제한한다.
- TMDB watch-provider 결과는 TMDB watch URL과 attribution을 보존하며 provider deep link로 오인하지 않는다.
- XAI 문구는 실제 사용한 model component와 non-zero contribution allowlist에서만 만든다.
- feature ablation 후 이유 문구가 바뀌지 않으면 fidelity 실패다.
- 외부 응답 누락 시 선호 점수를 낮추지 않고 `availability=UNKNOWN`으로 반환한다.

## 11. 실험 산출물 계약

각 REC-EV는 다음 파일을 생성해야 한다.

```text
evidence/manifests/rec-ev-NNN.json
evidence/results/rec-ev-NNN-summary.json
outputs/recommendation-evidence/rec-ev-NNN/user-results.parquet
evidence/REC-EV-NNN-*.md
```

manifest 필수 키:

- `schema_version`, `evidence_id`, `protocol_version`, `status`
- source checksum과 TMDB snapshot/version
- user split salt version, cutoff, seeds, K 목록
- candidate universe와 seen-item exclusion
- model/search space, selected hyperparameters, selection metric
- SESOI, non-inferiority margin, bootstrap, multiple-testing method
- artifact path/bytes/SHA-256
- validation 결과와 champion/fallback 결정

Test 실행 전에 protocol lock을 별도 파일로 저장하고 SHA-256을 manifest에 기록한다.

## 12. 중단 조건

다음 중 하나면 결과를 채택하지 않고 `BLOCKED` 또는 `INCONCLUSIVE`로 기록한다.

- Test 사용자가 base model이나 parameter 선택에 유입됨
- positive injection 또는 모델별 candidate universe 차이
- binary onboarding을 numeric rating으로 변환
- TMDB snapshot 시점을 숨기거나 현재 popularity를 과거 feature로 사용
- artifact checksum 불일치
- eligible 사용자·coverage 미달
- primary metric/SESOI를 Test 확인 후 변경
- segment 하나만 골라 전체 champion으로 발표

## 13. 관련 문서

- [추천 입력 신호 계약 vNext](./00-input-signal-contract-vnext.md)
- [추천 설계 보고서](./personalized-hybrid-design-report.md)
- [추천 serving 계약](./serving-contract.md)
- [기존 MovieLens 평가 설계](../research/movielens-recommendation-evaluation-design.md)
- [기존 evidence index](./evidence/README.md)
- [REC-EV-019P binary onboarding preflight](./evidence/REC-EV-019P-binary-onboarding-preflight.md)
