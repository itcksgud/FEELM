# REC-EV-019P — Binary onboarding 실행 가능성 preflight

> 상태: `COMPLETED_REPRODUCIBLE_FEASIBILITY_PASS`
> 실행일: 2026-08-30
> protocol: `rec-ev-019p-binary-onboarding-preflight-v2`
> 주장 경계: 추천 성능 결과가 아니라 REC-EV-019 실행 cohort의 크기와 split 가능성만 검증한다.

## 1. 질문

> MovieLens `global-time-v1` Test 구간에서 user-disjoint split을 유지하면서, 승인 프로토콜의 K10·
> 미래 10개·positive 3개·candidate-positive 조건을 모두 만족하는 Locked Test 사용자 5,000명 이상을
> 확보할 수 있는가?

## 2. 사전 문제

v1은 K10 binary proxy와 이후 rating 10개만 검사해 30% Test에서 5,267명을 보고했다. 그러나 승인
프로토콜이 요구하는 positive 3개와 candidate-positive를 적용하지 않아 잘못된 구현 GO였다. 수정된
Base-Train 전역 분포와 provisional candidate까지 적용한 같은 30% bucket subset은 4,112명으로 5,000명 Gate에
미달한다.

추천 모델·Test 성능은 아직 실행되지 않았으므로 cohort 실행 가능성만 사용해 Test를 40%로 교정했다.
Base Train에는 68,161명·10,254,572 rating이 남으며, 엄격 K10 Test cohort는 5,476명이다.

```text
Base Train   0..39   40%
Router      40..49   10%
Validation  50..59   10%
Locked Test 60..99   40%
```

## 3. binary proxy

- 각 사용자의 rating을 timestamp·movieId 안정 순서로 처리한다.
- 각 시점까지의 사용자 prefix mid-rank ECDF와 Base Train 전역 ECDF를 `lambda=10`으로 수축한다.
- 상대 효용 `>= +0.15`는 LIKE, `<= -0.15`는 DISLIKE다.
- 중립은 선택하지 않은 것으로 두며 DISLIKE로 만들지 않는다.
- K번째 binary label 뒤 최초 rating 10개의 window mid-rank utility `>= 0.65`를 positive로 둔다.
- positive가 3개 이상이고, 그중 하나 이상이 provisional core candidate에 있어야 eligible이다.
- provisional core는 Base Train에 등장했고 `links.csv`에 TMDB ID가 있는 영화 42,123편이다.
- raw MovieLens user ID는 결과에 저장하지 않는다.

MovieLens rating timestamp는 관람 시각이 아니고, 이 변환은 실제 포스터 노출 로그가 없는 상황의
`FIRST_OBSERVED_BINARY_PROXY`다.

## 4. 결과

| 항목 | 값 |
| --- | ---: |
| Test 기간 전체 사용자 | 22,252 |
| Base Train bucket 사용자 | 8,918 |
| Router bucket 사용자 | 2,295 |
| Validation bucket 사용자 | 2,244 |
| Locked Test bucket 사용자 | 8,795 |
| Locked Test 중 rating 20개 이상 | 7,577 |
| Locked Test 중 rating 30개 이상 | 6,549 |
| K5 입력 + 미래 10개 | 7,632 |
| K5 + positive 3개 | 6,040 |
| K5 strict eligible | 5,923 |
| K10 입력 + 미래 10개 | 6,990 |
| K10 + positive 3개 | 5,577 |
| K10 strict eligible | **5,476** |
| K10 strict eligible 중 LIKE/DISLIKE 모두 존재 | 5,030 |
| 수정된 정의로 재계산한 30% Test K10 strict eligible | 4,112 |
| 최소 Gate | 5,000 |

관측 rating `1,262,091`건 중 LIKE proxy는 `391,043`, DISLIKE proxy는 `454,935`, 중립은
`416,113`건이었다. 미평가·중립을 부정 신호로 바꾸지 않았다.

## 5. 판정

`PASS` — `40/10/10/40` user-disjoint split에서 REC-EV-019의 provisional K10 primary Test cohort가
승인 프로토콜의 전체 preflight 조건으로 최소 5,000명 Gate를 충족한다.

이 결과로 승인되는 것:

- `TASK-REC-EV-019A` cohort artifact 구현
- `TASK-REC-EV-019B` TMDB identity·feature artifact 구현
- `40/10/10/40` split lock

승인되지 않는 것:

- binary 개인화 모델 champion
- 예상 별점
- 실제 FEELM 만족도
- 현재 popularity-only 제품 정책 교체

`links.csv` 존재만으로 TMDB API identity 검증이 끝난 것은 아니다. 019B의 최종 identity allowlist를
019C가 공통 candidate에 적용한 뒤 K10 strict eligible을 다시 계산한다. 5,000명 미만이면 019C는
`INCONCLUSIVE`로 중단하며, Test 비율이나 positive 기준을 다시 낮추지 않는다.

> 후속 결과(2026-09-02): REC-EV-019A 전체 생성에서 identity allowlist를 적용해도 K10 5,476명이
> 유지됐다. 최종 후보는 41,625편이다. 이 문단의 preflight 조건은 충족됐다. 019C 실행 계약도 후속
> 검사를 통과했으며 다음 단계는 runner·합성 preflight다. 실제 Validation과 Locked Test는 아직 금지된다.

## 6. 재현

```powershell
$env:PYTHONPATH='scripts'
py -3 scripts/recommendation_binary_onboarding_preflight.py
py -3 -m unittest scripts/tests/test_recommendation_binary_onboarding_preflight.py
```

결과 원본:

- [preflight JSON](./results/rec-ev-019p-binary-onboarding-preflight.json)
- [preflight manifest](./manifests/rec-ev-019p.json)
- [vNext protocol](../protocols/rec-eval-vnext.json)
