# REC-EV-021P — 신작·희소 영화 실험 사전검사

> 상태: `PASS / READY_FOR_VALIDATION_PILOT`
> 범위: MovieLens Validation 역할과 사전 정의한 영화 분리
> 제품 반영: 없음
> Locked Test 성능 확인: 하지 않음

## 한 줄 결론

영화를 숨기는 실험과 평점 수를 줄이는 실험 사이에서 6,964편이 충돌하는 문제를 실제 실행으로 발견했고,
밀도 실험을 strict Train 영화 안으로 제한해 충돌을 0편으로 만들었다. REC-EV-019B 전체 TMDB 특징
manifest까지 확인돼, Locked Test를 열지 않는 소규모 Validation 파일럿을 시작할 수 있다.

## 발견하고 고친 문제

처음에는 두 영화 분리가 서로 다른 hash를 사용했다. 그 결과 Density Validation 영화 중 다음이 겹쳤다.

| 상태 | 겹친 영화 |
| --- | ---: |
| strict Validation 또는 Locked Test인데 density Validation이기도 함 | 6,964편 |

이 영화의 Base Train 평점 수를 density 실험에서 읽으면, strict cold 실험에서는 감춰야 할 영화 정보를 먼저
보게 된다. 그래서 다음처럼 바꿨다.

```text
strict ITEM_TRAIN
  ├─ DENSITY_TRAIN
  ├─ DENSITY_VALIDATION
  └─ DENSITY_LOCKED_TEST

strict ITEM_VALIDATION / ITEM_LOCKED_TEST
  └─ DENSITY_OUT_OF_SCOPE
```

재검사 결과 protected collision은 `0편`이다.

## 실제 실험 가능 표본

![평점 밀도 panel별 안전한 영화 수](../figures/cold-density-panels.png)

| Panel | 원래 Base Train 평점 수 | 비교할 q | 안전한 영화 수 | 5개 fold 최소~최대 |
| --- | ---: | --- | ---: | ---: |
| PANEL_5P | 5개 이상 | 0 / 1 / 5 | 3,662편 | 702~762편 |
| PANEL_20P | 20개 이상 | 0 / 1 / 5 / 20 | 1,963편 | 372~414편 |
| PANEL_100P | 100개 이상 | 0 / 1 / 5 / 20 / 100 / ALL | 994편 | 191~204편 |

3개 mask seed와 5개 fold를 모두 쓰면 협업 모델 하나당 최대 225회 학습이 필요하다. 협업 모델 5개를
한꺼번에 돌리면 예시상 1,125회가 되므로, 먼저 `PANEL_5P × seed 1개 × ALS 1개`로 시간과 메모리를 재는
것이 안전하다.

## TMDB 선행 조건 해소

`REC-EV-019B` 전체 실행에서 아래 파일과 checksum을 만들고 검증했다.

- MovieLens↔TMDB 영화 ID 검증 파일
- 장르·감독·배우·키워드·언어·연도 등 구조화 특징
- 줄거리 등을 담은 텍스트 embedding

MovieLens 제목·장르로 대체하지 않고 실제 TMDB identity 68,674편, 구조 특징 68,201편, 텍스트 특징
68,534편을 사용한다. 사전검사 재실행 결과 blocker는 0개, `model_run_status`는
`READY_FOR_VALIDATION_PILOT`다.

이는 콘텐츠 모델 채택이나 전체 grid 실행 승인과 다르다. compute plan에 적힌 대로 먼저 panel 하나,
mask seed 하나, ALS와 content baseline만 실행해 시간·메모리·결측 fallback을 측정해야 한다.

## 재현 명령

```powershell
py -3 scripts/recommendation_content_cold_v2_preflight.py --protocol docs/recommendation/protocols/rec-eval-content-cold-v2.json --role validation
py -3 scripts/validate_recommendation_content_cold_v2_preflight.py --manifest docs/recommendation/evidence/manifests/rec-ev-021p.json
```

검증기는 영화 역할 누출뿐 아니라 사람 유사성 평가용 agreement 수식과 선형 NDCG 예제도 같이 확인한다.
