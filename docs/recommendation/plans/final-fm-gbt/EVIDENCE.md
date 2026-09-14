# 최종 FM·GBT 설계의 근거

상태: DRAFT — 과거 결과의 회고와 문헌 적용 판단. 새로운 실험 결과가 아니다. 2026-09-11.

## 1. 전체 연구에서 무엇을 이어받는가

실험별 사용자·시점·학습량·입력량·목표가 달라 원점수를 한 순위표로 합치지 않는다.
아래 결과는 각 실험의 범위에서만 해석한다. REC036~043의 분류·설명·정책 연구는 완료 이력으로
보존하되, 현재 보류된 K-means/8분류/맞춤·발견을 다시 실험의 전제로 가져오지 않는다.

| 기존 연구 | 확인한 것 / 남은 한계 | 이번 설계에 반영 |
| --- | --- | --- |
| [REC032 ALS 단독](../../experiments/rec-ev-032/als-only/RESULT.md),[사용자 재분할](../../experiments/rec-ev-032/user-resplits/RESULT.md) | 당시 관측 후보에서 ALS가 단순 동일가중 콘텐츠 결합보다 유리. 전체 카탈로그 만족도는 아님 | ALS는 강한 참고선. 새 영화에 없는 ALS 점수를 기본값으로 채우지 않음 |
| [REC035 별점 표현](../../experiments/rec-ev-035/RESULT.md) | 원별점/특정 상대값/이진 대리값 비교. 모든 상대값 변환의 가능성을 소진한 것이 아님 | 학습 정답과 실제 별점의0.5단위 유지. 개인 반응 특징·계산 보정과 구분 |
| [REC044 관계](../../experiments/rec-ev-044/RESULT.md),[REC045 다중 속성](../../experiments/rec-ev-045/RESULT.md) | 별점 습관과 대중 경향 뒤에도 장르 등 반응의 작은 추가 설명력. REC045의 주요 대중 집계는 MovieLens 학습 집계 | 장르·반응·근거량을 유지하되 인과적 개인 영향도나 TMDB 효과라고 확대하지 않음 |
| [REC046](../../experiments/rec-ev-046/RESULT.md),[구성 감사](../../experiments/rec-ev-046/data-audit/RESULT.md) | 초기5종에서 사용자별 허용 이력을 크게 잘랐고 활동량 편중을 확인 | 작은 학습량 실험의 원수치를 새 모델 성능과 직접 비교하지 않음 |
| [REC047](../../experiments/rec-ev-047/RESULT.md) | 과거 학습 확대 후 입력30편에서 ALS의 순서 지표가 강함. 모든 입력·MSE 대조가 유의한 우위는 아님 | 전체 목표 행과 입력량별 비교 유지. ALS 미지원 기본값은 이후 직접점수 N/A 계약으로 구분 |
| [Cold-item-content](../../experiments/cold-item-content/RESULT.md) | 일부 영화 평점 차단과 개인 대중성 반응 추가. 일부 이득은 있으나 작은 표본·일부 입력에 한정 | C와 자연 미지원 구분,현재230특징 유지. C를 학습한 참고ALS와 정보 비대칭 명시 |
| [Text339](../../experiments/text339/README.md),[의미 정제](../../experiments/text339-clean/README.md) | 줄거리·Wiki·현재 정제의 고정 구성에서 개선 채택 기준 미통과 | 마지막 두 모델 비교에서 텍스트 조합을 다시 늘리지 않음. 텍스트 일반의 무용성 주장은 금지 |
| [Foundation340](../../experiments/foundation340/EXECUTION.md) | 같은 목표에서 B/R/H/RH 비교. RH가 대중평점 극단값과 입력 이력 구성을 함께 수정했으나 당시 주 순위 기준은 미통과 | RH는 검증된 공통 레시피로 선택. 최적성이 확인됐다는 이유가 아님 |
| [Combination340](../../experiments/combination340/EXECUTION.md) | 동일 B 입력의 FM↔GBT와 ALS 보조 비교. 사전 개선 자격 미통과 | 모든 모델·혼합 가중치를 다시 학습하지 않음. 고정 혼합을 자동 서비스 채택하지 않음 |
| [Diagnostic342](../../experiments/diagnostic342/RESULT.md) | 관측 평점 성적이 전체 후보의 극소수 투표·10점 노출 쏠림을 충분히 드러내지 못함 | 전체 후보 진단 필수. 노출 감소를 만족도 개선으로 계산하지 않음 |
| [Research343](../../experiments/research343/RESULT.md) | FM_RH는 평균 오차,GBT_B는 관측 첫2편·분산 노출에 장점. GBT_R와 순위 학습의 전반적 우위는 미확인 | 서로 다른 레시피인 FM_RH↔GBT_B만으로 모델 가족 우위를 말하지 않음. 빠진 GBT_RH를 비교 |

같은 R 입력의 FM_R↔GBT_R도 저장 결과에 있다. B/R/RH 전체 교차표를 처음부터 재학습할 필요는 없다.
이번 추가 범위는 공통 RH 안의 두 모델과 제한된 반복 예산이며, 기존 모델은 봉인된 참고 결과로 남긴다.
위 실행 명세의 실제 결과와 판정은 [foundation340 결과 검토](../../experiments/foundation340/result-review.json),
[combination340 결과 검토](../../experiments/combination340/result-review.json) 및
[전체 완료 결과](../../experiments/final341/README.md)에서 함께 확인한다.

## 2. 실행 코드와 역할을 다시 보며 확인한 문제

**입력이 없던 학습 행의 의미.** 기존4,997,069개 목표에서 cap0 배정은998,592개(19.98%),
양수cap인데 첫180일 구간 이전 이력이 없어 h0가 된 것은3,116,145개(62.36%)다.
합쳐4,114,737개(82.34%)였다. ‘180일보다 오래된 평점만 사용했다’는 설명이 아니라,
같은 구간 안에서 먼저 평가한 영화도 입력에서 제외하는 구성이다. 이것 자체가 목표 별점 누수는 아니다.
검증된 H/RH는 목표 시각 직전의 허용 이력으로 바꿔 h0를1,051,625개(21.04%)로 줄였다.
[구간 생성](../../../../scripts/text339_prepare.py) · [RH 특징/이력](../../../../scripts/foundation340_features.py)

**미사용 사용자에 대한 이름과 사실.** 현재270명은 이미 여러 번 개발에 쓰였고,
그 안의90/180 분리는 새 test가 아니다. 기존 REC047 후보271명 전원은 과거 사용 이력이 있으며
261명은 과거 학습에도 포함됐다. FINAL_RESERVE9,670명 중8,992명의 사용 이력도 확인됐다.
나머지678명을 자동으로 깨끗한 test라고 해석할 수 없다. 따라서 독립 최종 표본은 이번 설계의
확정 자원이 아니라, 실행 전 UID 사용 이력 감사가 통과할 때만 가능한 경로다.
[역할 검토](../../experiments/text339/cohort-history-review.json)

**가중치의 실제 지원.** Spark4.1.3 FM의 상속 API에는 weightCol이 보여도 실제 학습 경로는
label와features만 RDD로 전달한다. GBT에만 사용자 가중치를 주고 동일 학습 목적이라고 쓰면 안 된다.
이번에는 같은 무가중 행을 유지하고 사용자 균등 평가·사용자별 학습 기여 진단을 병기한다.
[FM 소스](https://github.com/apache/spark/blob/v4.1.3/mllib/src/main/scala/org/apache/spark/ml/regression/FMRegressor.scala#L382-L402) ·
[GBT API](https://spark.apache.org/docs/4.1.3/api/python/reference/api/pyspark.ml.regression.GBTRegressor.html)

**같은 반복 확대와 같은 비용은 다르다.** FM maxIter 증가는 최적화,GBT maxIter 증가는
트리 수와 용량도 늘린다. 두 설정씩 제한하되 실제 비용을 적어야 한다. 기존 FM_RH의
약42분·12GiB+4096B 메모리 예외를 숨기지 않으며, 새 FM300의 완료를 보장하지 않는다.
[FM API](https://spark.apache.org/docs/4.1.3/api/python/reference/api/pyspark.ml.regression.FMRegressor.html) ·
[이전 자원표](../../experiments/research343/RESULT.md)

## 3. 다른 연구에서 가져올 인사이트

아래 적용은 FEELM을 위한 설계 판단이다. 논문의 결과가 이 데이터에서 이미 재현됐다는 뜻은 아니다.

| 1차 자료 | 가져올 근거 | 이번 적용 / 가져오지 않을 주장 |
| --- | --- | --- |
| [Rendle, Factorization Machines, ICDM2010](https://www.ismll.uni-hildesheim.de/pub/pdfs/Rendle2010FM.pdf) | 특징 쌍의 상호작용을 공유된 잠재요인으로 학습 | 현재 영화 정보·과거 반응의 조합을 공통 입력으로 유지. 새ID가 자동으로 학습되거나 새 영화 추천 품질이 보장되는 것은 아님 |
| [Covington 등, YouTube Recommendations, RecSys2016 §3.4](https://research.google/pubs/deep-neural-networks-for-youtube-recommendations/) | 과거 이력→다음 목표로 서비스 상황에 맞춘 예제 구성,사용자당 예제 기여 통제의 사례 | 두 모델에 같은 strict-past 입력을 사용하고 기여 분포 공개. 사용자 균등 표집은 이번 모델 비교와 별개로 보류. YouTube 신경망/시청시간/후보 수를 도입하지 않음 |
| [Grinsztajn 등, NeurIPS2022](https://proceedings.neurips.cc/paper_files/paper/2022/hash/0378c7692da36807bdec87ab043cdadc-Abstract-Datasets_and_Benchmarks.html) | 정형 데이터의 강한 트리 기준과 튜닝 비용을 포함한 비교 | GBT를 남기고 같은 자원 상한·제한된 후보를 사용. 이 연구는 FEELM의500만 종속 행에서 SparkGBT가 FM을 이긴다는 증거가 아님 |
| [Dacrema 등, RecSys2019](https://arxiv.org/abs/1907.06902) | 재현 가능한 비교와 충분히 다룬 단순 기준선의 중요성 | 추가 모델보다 두 기존 모델의 제한된 반복 증가와 재현을 확인. 모든 신경망 연구의 열세나 두 설정만으로 충분한 튜닝을 증명하지 않음 |
| [Ji 등, TOIS2023](https://arxiv.org/abs/2010.11060) | 사용자별 마지막 관측 분리만으로 전체 시간축의 미래 정보 유입이 해결되지 않음 | 학습 마감/입력 마감/영화 가용시각/메타데이터 수집시각 구분. 현재 TMDB는 과거 시점 복원이 아님 |
| [Schnabel 등, ICML2016](https://proceedings.mlr.press/v48/schnabel16.html) | 관측 평점의 비무작위성과 관측 확률 보정에 필요한 가정 | 사용자 평균·TMDB 근거량 수축을 노출 편향 제거로 부르지 않음. 투표수를 임의 propensity로 사용하지 않음 |
| [Krichene·Rendle, KDD2020](https://research.google/pubs/on-sampled-metrics-for-item-recommendation/) | 일부 후보만 사용한 순위 지표와 전체 후보의 모델 순서가 다를 수 있음 | 1개 정답＋임의100/500개를 만들지 않음. 관측 J와 전체 후보 진단 분리. 관측 J도 편향 없는 전체 정답이 아님 |

## 4. 이번에는 넣지 않는 유력한 아이디어

- 더 많은 텍스트/희소 인물 ID/추가 임베딩: 가능한 연구지만 같은 입력에서 두 모델을 비교하는 질문이 달라진다.
- 사용자별 균등 학습 표집: 활동 사용자 지배를 줄이는 후보지만 학습 분포·support도 바꾼다. 이번에는
  현재 허용 평점 전체를 보존하고 공통 무가중을 사용한다. 이를 편향을 해결한 설계로 부르지 않는다.
- 신뢰도에 따른 재정렬·최소 투표수 필터·모델 혼합: 새 서비스 정책의 비교다. 모델 결과를 좋게 만들기 위한
  사후 필터로 추가하지 않는다. 필요한 동작 문제는 최종 결과에 별도로 남긴다.
- 또 다른 순위 모델: research343에서 실제 순위 손실을 비교했고 이 구성의 개선은 확인하지 못했다.
  순위학습 일반이 불가능하다는 뜻은 아니지만 이번 FM/GBT 마무리에 추가할 우선순위는 낮다.

최종 선택에는 아직 절충이 남을 수 있다. 그때 MSE와Top2를 임의 가중합으로 합치거나
작은 자연 미지원 셀의 좋은 숫자만으로 승자를 정하지 않는 것이 이번 설계의 종료 원칙이다.
