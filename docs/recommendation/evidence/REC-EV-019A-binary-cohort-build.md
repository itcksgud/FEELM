# REC-EV-019A — 사용자 분리 온보딩 평가 집합 생성 결과

> 상태: `DONE / PASS_COHORT_GATES`
> 실행일: 2026-09-02
> 범위: 데이터와 정답 집합 생성만 수행
> 제품 반영: 없음 — `APPROVED_C2A_INTERNAL_POPULARITY_ONLY` 유지
> Locked Test 모델 성능 사용: 없음

## 한 줄 결론

MovieLens 사용자를 학습·조정·검증·최종 시험으로 서로 겹치지 않게 나누고, 사용자가 처음 입력한
0·5·10개 선호만 모델에 제공하는 평가 집합을 만들었다. TMDB 신원 확인까지 적용한 뒤에도 엄격 K10 최종
시험 대상은 5,476명으로 최소 기준 5,000명을 통과했다. 아직 어떤 추천 모델도 실행하거나 선택하지 않았다.

## 무엇을 고정했나

| 역할 | 사용자 bucket | 읽는 파일 | 용도 |
| --- | ---: | --- | --- |
| Base Train | 0~39 | 시간 cutoff 이전 Train | 모델 학습과 사용자 평점 분포 기준 |
| Router Train | 40~49 | Validation | 나중의 K별 모델 선택 규칙 학습 |
| Validation | 50~59 | Validation | 모델·파라미터 비교 |
| Locked Test | 60~99 | Test | 모든 선택을 잠근 뒤 한 번만 성능 평가 |

사용자 ID는 결과에 저장하지 않고 고정 salt SHA-256 키로 바꿨다. K5 입력은 항상 K10 입력의 앞 5개다.
중립 평점과 미평가는 싫어요로 만들지 않았다.

## 실제 생성 결과

![후보 경계와 K10 평가 대상](../figures/rec-ev-019a-cohort-funnel.png)

### 학습 데이터와 후보 영화

| 항목 | 결과 |
| --- | ---: |
| Base Train 사용자 | 68,161명 |
| Base Train 평점 | 10,254,572개 |
| cutoff 이전 등장 영화 | 42,203편 |
| MovieLens→TMDB 링크가 있는 1차 후보 | 42,123편 |
| TMDB 신원 확인까지 통과한 최종 후보 | 41,625편 |

중요한 경계가 하나 있다. 별도로 만들어 둔 TMDB 특징 69,603편은 Base 역할 사용자들이 전체 기간에 한 번이라도
평가한 **넓은 콘텐츠 특성 집합**이다. 미래 시점 영화까지 포함하므로 추천 후보군이 아니다. 실제 019C가
점수를 계산할 수 있는 영화는 `019A cutoff-safe 42,123편 ∩ 019B identity allowlist = 41,625편`뿐이다.

### 엄격 K별 평가 가능 사용자

엄격 적격 사용자는 다음을 모두 만족한다.

1. 모델 입력 K개와 그 다음 실제 평점 10개가 있다.
2. 다음 10개 안에 사용자 기준 상위 선호가 3개 이상 있다.
3. 그 상위 선호 중 적어도 1개가 공통 후보 영화에 있다.

| 역할 | K0 | K5 | K10 |
| --- | ---: | ---: | ---: |
| Router Train | 1,721명 | 1,651명 | 1,530명 |
| Validation | 1,674명 | 1,614명 | 1,479명 |
| Locked Test | 6,201명 | 5,923명 | 5,476명 |

K10 Locked Test 5,476명 중 TMDB 신원 격리 때문에 추가 탈락한 사용자는 0명이었다. 이는 각 사용자에게
좋아한 후보가 적어도 하나 남았다는 뜻이지, 격리된 영화가 0편이라는 뜻은 아니다.

## 검증한 실패 방지 조건

- Base Train은 시간 cutoff 이전 행만 포함한다.
- 네 사용자 역할은 서로 겹치지 않는다.
- K5는 K10에 완전히 포함되고 평가 창은 사용자·K마다 정확히 10행이다.
- 평가 창의 좋은 영화를 후보에 강제로 넣지 않는다.
- 미평가와 중립을 싫어요로 바꾸지 않는다.
- 추적 결과에는 MovieLens 원본 사용자 ID가 없다.
- 모델 예측·지표·champion·제품 정책을 만들지 않았다.
- 계약과 여섯 결과 파일의 크기·SHA-256을 manifest로 확인한다.

## 지금 열린 것과 아직 닫힌 것

| 열린 작업 | 아직 금지된 주장·작업 |
| --- | --- |
| REC-EV-019C 실행 계약 작성 | 특정 모델이 인기도보다 좋음 |
| Validation용 기준선·후보 구현 준비 | Locked Test 모델 성능 열람 |
| 동일한 41,625편 후보에서 모델 비교 설계 | 개인화 champion·제품 정책 변경 |

019C는 아이디어 목록만 있고 아직 산출물 schema, 파라미터 탐색 순서, 자원 상한, 중간 실패 복구 방식이
실행 계약으로 충분히 고정되지 않았다. 따라서 다음 GO는 **019C 계약 작성**까지이며 모델 실행 GO는 아니다.

## 재현 명령

```powershell
py -3 -m unittest scripts/tests/test_build_rec_ev_019a_cohorts.py
py -3 scripts/build_rec_ev_019a_cohorts.py --contract docs/recommendation/contracts/rec-ev-019a-artifacts.json
py -3 scripts/verify_rec_ev_019a_cohorts.py --manifest docs/recommendation/evidence/manifests/rec-ev-019a.json
```

대용량 Parquet은 Git에 넣지 않는다. 추적하는
`docs/recommendation/evidence/manifests/rec-ev-019a.json`이 파일 경로·크기·SHA-256을 보존한다.
