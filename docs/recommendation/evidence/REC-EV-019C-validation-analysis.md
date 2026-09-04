# REC-EV-019C MovieLens 대리 Validation 독립 감사 교정

> 상태: `PASS_VALIDATION_ANALYSIS_ONLY`
> 실행 범위: Validation only
> `locked_test_used=false`
> `champion=null`
> `product_policy_updated=false`

## 1. 목적과 해석 경계

이 실험은 MovieLens 관측 선호 복원에서 TMDB 구조·E5 콘텐츠 특징을 결합한 모델이 B0 인기도를
넘는지 확인한다. MovieLens 사용자는 FEELM 사용자가 아니며 미평가 영화는 싫어요가 아니다. K5와 K10은
평가 사용자와 미래 평가 구간이 달라 서로의 추천 품질을 직접 비교할 수 없다. 한국 사용자 만족, 한국 영화
품질, 최신 영화 품질도 이 결과로 증명하지 않는다.

## 2. 실행·재현과 방화벽

```powershell
py -3 scripts/analyze_rec_ev_019c_validation.py
py -3 scripts/verify_rec_ev_019c_analysis.py `
  --manifest docs/recommendation/evidence/manifests/rec-ev-019c-analysis.json
```

분석은 후보 41,625편, K5 1,614명, K10 1,479명의 Validation 원자료와 예측 11,662,500행을
재계산한다. 검증기는 paired 통계, 공통 사용자 진단, 후보 anchor fallback, Q4·한국어 원어·출시연도·
base-train-zero slice를 원자료에서 다시 만들고 summary와 비교한다. Locked Test 파일은 읽지 않는다.

대용량 Parquet은 `outputs/` ignore에만 있고 외부 artifact URI가 없다. manifest가 로컬 파일의 경로·크기·
SHA-256을 잠그지만, commit만 받은 제3자는 원자료를 복원할 수 없다. 따라서 현재 상태는 로컬 원자료가 있는
환경의 재검증 가능성이지 commit 단독 재현성이 아니다.

## 3. LightFM T003의 B0 대비 우위

전체 Validation에서 LightFM T003의 B0 대비 평균 NDCG@10 차이는 K5 `+0.0311`, K10
`+0.0434`였다. 이 수치에는 모델 선택에 사용한 K별 256명 tuning panel이 포함되므로 탐색 결과로 둔다.

공식 제한·보조 결과는 tuning panel을 제외한 같은 사용자 paired 비교다.

| 조건 | 사용자 | LightFM − B0 평균 | paired bootstrap 95% CI |
| --- | ---: | ---: | ---: |
| K5 | 1,358 | +0.03331 | [0.02582, 0.04114] |
| K10 | 1,223 | +0.04532 | [0.03681, 0.05462] |

bootstrap 단위는 사용자, 방식은 percentile, 반복은 2,000회다. seed는 K5 `20260910`, K10
`20260915`로 고정했다. 두 구간 모두 0보다 커서 각 조건 안에서 LightFM T003의 B0 대비 우위는
유지된다. Harm@2 개선은 기존처럼 신뢰구간이 0을 포함하므로 안전성 개선으로 확정하지 않는다.

## 4. K5와 K10은 직접 비교하지 않는다

K5와 K10은 적격 사용자와 미래 구간이 다르다. 공통 1,253명만 남겨도 사용자는 같지만 미래 구간은
같지 않다.

| 공통 사용자 1,253명 | K5 NDCG@10 | K10 NDCG@10 |
| --- | ---: | ---: |
| B0 인기도 | 0.038780 | 0.030512 |
| LightFM T003 | 0.075359 | 0.075348 |
| LightFM − B0 | +0.036579 | +0.044836 |

LightFM 절대값은 사실상 같고 K10의 더 큰 B0 대비 차이는 B0가 낮아진 영향이다. 따라서 `K10 우위`나
`K10 우선`을 결론으로 두지 않는다. 다음 유효한 검증은 같은 사용자와 같은 미래 구간을 고정하고 prefix만
K5·K10으로 바꾸는 ablation이다.

## 5. 양방향 신호는 효과가 아니라 적용 전제였다

현재 LightFM fold-in 구현은 최종 후보셋에 매핑된 positive anchor와 negative anchor가 각각 하나 이상
없으면 설계상 B0로 fallback한다. 한쪽 신호 사용자의 동률은 모델 효과의 부재를 검증한 결과가 아니라
LightFM을 적용하지 않은 결과다.

| 조건 | K5 | K10 |
| --- | ---: | ---: |
| 원시 prefix에 양쪽 신호가 있는 사용자 | 1,085 | 1,355 |
| 최종 후보에 양쪽 valid anchor가 있는 사용자 | 988 | 1,309 |
| 원시 양쪽 신호가 있어도 anchor 부족으로 fallback | 97 | 46 |
| 원시 한쪽 신호 사용자, 전원 fallback | 529 | 124 |

따라서 `양방향 신호 효과` 주장을 삭제한다. 이 표는 구현 적용 조건과 후보 매핑 손실을 설명할 뿐
신호 조합의 인과 효과를 검증하지 않는다.

## 6. 영화 slice의 표본과 방향

관측 positive 자체가 K5 `6,086/6,345=95.9%`, K10 `5,729/5,943=96.4%`로 인기도 Q4에
집중됐다. Q1~Q3의 Top-10 적중 0은 저인기 성능 실패 확정이 아니라 positive 표본 259건과 214건의
검정력 부족을 포함한 미측정에 가깝다.

한국어 원어 positive는 K5 21건, K10 23건이고 두 모델의 Top-10 hit는 모두 0이다. Top-500은
LightFM이 B0보다 낮은 방향이었다.

| 조건 | B0 Top-500 | LightFM Top-500 | Top-10 |
| --- | ---: | ---: | ---: |
| K5 한국어 원어 | 10 / 21 | 6 / 21 | 두 모델 0 |
| K10 한국어 원어 | 10 / 23 | 6 / 23 | 두 모델 0 |

표본이 작으므로 LightFM 열등을 확정하지 않는다. 한국어 원어는 TMDB `original_language=ko` proxy이며
한국 사용자 성능을 뜻하지 않는다.

## 7. 최신 영화와 cold item은 측정하지 못했다

| slice | 후보 수 | K5 positive | K10 positive | 판정 |
| --- | ---: | ---: | ---: | --- |
| `release_year >= 2020` | 9 | 0 | 0 | 품질 미측정 |
| base-train rating count = 0 | 0 | 0 | 0 | 최종 후보에 true cold item 없음 |

최종 후보 41,625편 중 2020년 이후 영화는 9편뿐이며 Validation positive가 없다. 최신·포스트-
MovieLens 문제를 해결했다거나 실패했다고 말할 정답 표본이 없다. release-year와 cold-item 0건도 summary와
검증기에 명시적으로 남긴다.

## 8. stability와 공개 보조 후보

5-seed stability는 전체 Validation이 아니라 K별 256명 tuning panel에서 측정했다. B4 BPR의 표준편차는
K5 `0.00520`, K10 `0.00256`, LightFM은 K5 `0.00223`, K10 `0.00168`이었다. 전체 사용자
안정성으로 확대 해석하지 않는다.

목표 도메인·최신성·한국 영화 범위·제품 사용 가능한 라이선스·사용자 행동을 동시에 만족하는 즉시 사용
가능한 대안은 확인되지 않았다. 다음 공개 자료는 출처를 확인한 범위에서 보조 검증 후보일 뿐이다.

- [ML-32M Extension](https://uwaterlooir.github.io/datasets/ml-32m-extension.html)은 51명 연구 참여자의
  watch-interest relevance judgment를 제공하지만 연구자 제한·비공개 조건이 있고 FEELM 목표 도메인이 아니다.
- [MovieLens Beliefs 2024](https://grouplens.org/datasets/movielens/ml_belief_2024/)는 보지 않은 영화의
  예상 평점을 수집했지만 MovieLens 사용자·카탈로그 문맥이다.
- [KMRD](https://github.com/lovit/kmrd)는 Naver 영화 평점 기반 synthetic 공개 자료다. 기존 감사에서
  한국 제작 범위가 있어도 사용자 활동성, 최신성, 라이선스, 제품 행동 Gate를 동시에 통과하지 못했다.
- [Amazon Reviews 2023](https://amazon-reviews-2023.github.io/main.html)은 2023년까지의 최신 상호작용을
  제공하지만 상품 리뷰 도메인이고 FEELM 영화 탐색·시청 행동을 대체하지 않는다.

## 9. 결정

- LightFM T003의 각 K 조건 안 B0 대비 우위는 제한·보조 confirmatory 결과로 유지한다.
- K5와 K10의 직접 품질 비교 및 `K10 우선` 주장을 삭제한다.
- `양방향 신호 효과` 주장을 삭제하고 fallback 적용 전제로 기록한다.
- 다음 실험은 같은 사용자·같은 미래 구간의 prefix ablation이다.
- Q1~Q3, 한국어 원어, 2020년 이후, true cold item은 표본 또는 정답 부재로 미확정이다.
- `locked_test_used=false`, `champion=null`, `product_policy_updated=false`를 유지한다.

## 10. 근거 파일

- Validation manifest: `docs/recommendation/evidence/manifests/rec-ev-019c-validation.json`
- 분석 manifest: `docs/recommendation/evidence/manifests/rec-ev-019c-analysis.json`
- 분석 summary: `outputs/recommendation-evidence/rec-ev-019c/analysis-summary.json`
- 분석 코드: `scripts/analyze_rec_ev_019c_validation.py`
- 분석 검증기: `scripts/verify_rec_ev_019c_analysis.py`
- 결과 장표: `docs/presentation/FEELM-REC-EV-019C-results.pptx`
