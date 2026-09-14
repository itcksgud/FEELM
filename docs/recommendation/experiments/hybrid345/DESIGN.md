# Qwen 콘텐츠·ALS 결합·cold-item 전이 비교 설계

상태: **DRAFT — 실행 전 독립 검토 대상**  
범위: **DEVELOPMENT_ONLY**. 이 문서는 제품 추천 정책이나 배포 계약이 아니다.

## 판단할 질문

현재 서비스 설계의 `ALS + 콘텐츠 벡터`가 실제로 도움이 되는지, 그리고 ALS item factor가 없는
영화에는 어떤 점수기를 써야 하는지 같은 사용자·영화·관측 별점 축에서 판단한다.

1. ALS가 직접 점수화할 수 있는 영화에서 Qwen 콘텐츠를 섞으면 ALS보다 좋아지는가?
2. ALS가 직접 점수화할 수 없는 영화에서 Qwen 직접 점수와 Qwen→ALS factor 전이(ALS-C2F)가
   구조 콘텐츠, FM, GBT보다 좋아지는가?
3. 위 결과로 support-aware router를 고정했을 때 전체 관측 후보에서 FM·GBT보다 좋아지는가?

K-means, 8개 취향 분류, TASTE/DISCOVERY 비율은 이번 실험 축이 아니다. 추천 노출은 서비스 구상에
맞춰 첫 2편과 이후 2편 단위를 확인하지만, 군집 정책은 점수 모델을 정한 뒤 별도로 판단한다.

## 비교 대상과 적용 가능 구간

| 이름 | 정의 | W_DIRECT | C | NATURAL_ZERO |
|---|---|---:|---:|---:|
| `ALS` | C를 제외하고 학습한 ACTUAL_ALS rank32 + 원별점 fold-in | O | N/A | N/A |
| `STRUCTURED_DIRECT` | 장르·키워드·인물 등 sparse unit vector의 상대별점 profile cosine | O | O | O |
| `E5_DIRECT` | 기존 E5-small 384D의 같은 상대별점 profile cosine. encoder 비교용 진단선 | O | O | O |
| `QWEN_DIRECT` | 같은 영화 본문을 Qwen3로 임베딩한 상대별점 profile cosine | O | O | O |
| `FM150_s339/344/345` | final344의 고정 RH230 FM150 세 checkpoint | O | O | O |
| `GBT120_s339/344/345` | final344의 고정 RH230 GBT120 세 checkpoint | O | O | O |
| `ALS_QWEN` | 보정한 ALS와 QWEN_DIRECT의 convex blend | O | Qwen과 같아 제외 | Qwen과 같아 제외 |
| `ALS_C2F` | Qwen 벡터로 ACTUAL_ALS item factor를 추정해 cold item을 채점 | 진단만 | O | O |

`ALS_QWEN`은 별도 기반 모델이 아니라 warm-item 결합 정책이다. `ROUTER_s339`도 학습 모델이 아니라
지원 여부에 따라 고정한 warm/cold 점수기를 선택하는 정책이다. ALS가 없는 칸을 0, 평균, 인기점수로
채우지 않는다. C 평점을 포함해 학습한 `REFERENCE_ALS`는 누수된 oracle-like 진단선일 뿐 후보 선정과
승자표에는 넣지 않는다.

FM·GBT의 모델군 수치 `FM150_SEED_MEAN`, `GBT120_SEED_MEAN`은 prediction ensemble이 아니다. 각
checkpoint를 따로 보정·평가한 뒤 같은 사용자의 지표를 세 seed에 대해 평균해 final344와 같은 축을
만든다. 실제 router의 fallback/cold checkpoint는 사전에 고정한 seed339이며 이름을 분리한다.

## 고정 데이터 축

- 카탈로그: `text339/catalog.parquet`의 정렬된 고유 영화 85,517편.
- 평가 context: `text339/contexts.json`, 270명 × H=0/1/5/10/30, 총 93,230 target행.
- 역할: `final344/roles.csv`의 calibration 90명, comparison 180명. 사용자 역할은 섞지 않는다.
- 주 조건: H=10이고 실제 입력 `h>0`. H는 강제 입력 수가 아니라 최대 사용 편수다.
- 영화 구간: W_DIRECT 45,074편, C=V+E 10,952편, NATURAL_ZERO 29,491편.
- 기존 ALS와 FM·GBT 각 3개 seed는 원본 seal, 영화 ID축, row ID축이 모두 일치할 때만 읽기 전용 재사용한다.
- C 영화의 모든 상호작용은 ACTUAL_ALS 학습과 mapper 학습·선택에서 제외돼 있어야 한다.

이 270명과 C 영화는 과거 실험에서 이미 사용됐다. comparison 180명은 이번 새 코드의 설정 선택에는
쓰지 않지만 완전한 미사용 test가 아니다. 모든 결론은 재사용 개발 비교로 제한한다. MovieLens의
과거 사용자·별점과 2026년 TMDB snapshot을 결합하므로 2026년 한국 사용자 만족도를 검증하지 않는다.

## 콘텐츠 입력과 Qwen

encoder만 바꾸는 비교가 되도록 REC-EV-019B/027의 영화 본문을 그대로 재구축한다.

```text
{display_title} [SEP] {overview_fallback} [SEP] genres: {genre_names}
[SEP] directors: {director_names} [SEP] cast: {top5_cast_names}
[SEP] keywords: {keyword_names}
```

- 원천: 검증된 ko-KR TMDB cache. 제목과 overview는 기존 fallback 규칙을 따른다.
- 기존 E5의 `passage: ` 접두어를 제거한 같은 body를 Qwen의 문서 입력으로 쓴다.
- `Qwen/Qwen3-Embedding-0.6B`, revision
  `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, BF16 CUDA, left padding,
  last-token pooling, L2 normalization, native 1024D를 고정한다.
- 영화는 canonical `movie_id` 순서로 연속 8편씩 처리하고, 배치 안에서만 dynamic padding한다.
  `batch_size=8`, 입력 순서, padding 결과를 고정하며 length sorting/bucketing과 부분 resume을 쓰지 않는다.
- compute는 BF16, pooling과 L2 normalization 결과는 float32, 저장도 float32다. pilot과 본 실행은
  Python 3.12, PyTorch 2.10.0+cu128, Transformers 5.16.1, CUDA 12.8 경로를 고정한다. 모델·tokenizer
  파일과 pilot manifest·실행 코드의 SHA-256을 입력 봉인에 포함한다.
- 사전 token audit에서 85,517편의 최대 길이는 916 tokens였으므로 max_length=1024로 두고 잘림 0건을
  요구한다. 각 연속 배치의 원래 row 위치를 그대로 저장하고, 정렬·복원 단계는 두지 않는다.
- 모든 영화는 대칭적인 document로 처리하므로 query instruction을 쓰지 않는다.
- Wikipedia, 텍스트 정제, instruction, 차원 축소를 추가 비교축으로 열지 않는다.
- REC-EV-027 catalog build는 기존 REC-EV-019B 임베딩 가능 영화 68,674편을 재사용하고 같은 template로
  추가 영화를 인코딩했다. 그 결과인 `text-embeddings.parquet`에 현재 85,517편이 모두 존재하며
  `feature_eligible=true`임을 manifest·summary·universe·integrity 파일과 함께 먼저 검증한다.
- 기존 E5 input hash와 재구축한 `passage: `+body hash가 85,517/85,517 일치하고, Qwen body가
  그 문자열에서 접두어만 제거했음을 확인해야 인코딩을 시작한다. 빈 선택 필드는 빈 문자열로 남겨
  텍스트가 풍부한 영화만 선별하지 않는다.

## 사용자 콘텐츠 점수

0.5 단위 별점을 10개 bin으로 유지한다. C와 평가 사용자를 제외한 현재 ACTUAL_ALS 학습 데이터
39,859명·4,997,069행에서 사용자를 동일 가중한 10-bin 분포를 다시 계산해 `current-prior.npz`로
봉인한다. 과거 REC-EV-027 prior는 모집단이 다르므로 재사용하지 않는다. 이 prior의 고정 mid-rank
`g0_mid`를 사용해 입력 영화별 상대 가중치를 계산한다.

```text
q_i = (below_i + 0.5 * same_i + 5 * g0_mid[rating_bin_i]) / (h + 5)
w_i = 2*q_i - 1
score(j) = sum_i w_i * cosine(movie_i, movie_j) / sum_i |w_i|
```

`STRUCTURED_DIRECT`, `E5_DIRECT`, `QWEN_DIRECT`는 같은 입력 순서·별점·가중식·후보를 쓰고 영화
표현만 바꾼다. `h=0`, 가중치 분모 0, profile norm 0은 N/A다. 미평가 영화는 UNKNOWN이지 싫어요가 아니다.

## ALS-C2F

Qwen 1024D `x_i`에서 현재 ACTUAL_ALS의 raw 32D item factor `q_i`를 예측한다.

- teacher는 W_DIRECT 45,074편의 ACTUAL_ALS factor만 사용한다.
- 영화별 `train_count`를 1–9, 10–49, 50+로 나눈다. split key는 UTF-8
  `hybrid345-mapper-v1|{movie_id의 unsigned decimal}`의 SHA-256 raw digest를 big-endian unsigned로
  정렬하고 movie_id로 tie-break한다. 각 층의 앞 `floor(0.8*n)`은 train, 나머지는 validation이다.
- intercept가 있는 다중 출력 ridge다. Qwen float32 입력을 float64로 올리고 scaling하지 않으며,
  NumPy 1.26.4 `numpy.linalg.solve`을 쓴다. alpha 후보는 0.01, 0.1, 1, 10, 100이다.
- validation 영화 동일가중 mean factor cosine 최대 → factor RMSE 최소 → 더 작은 alpha 순으로 선택한다.
- alpha 선택 후 W_DIRECT 전체로 한 번 재학습한다. validation cosine이 0 이하이거나 유한하지 않으면
  ALS-C2F를 실패 처리한다.
- C·NATURAL_ZERO의 factor나 평가 별점, 평가 사용자의 별점은 mapper 학습·선택에 사용하지 않는다.
- 사용자 factor는 ACTUAL_ALS factor가 존재하는 과거 입력과 실제 0.5단위 별점만으로 fold-in한다.
  그런 입력이 없으면 N/A다. candidate는 Qwen으로 추정한 factor로 점수화한다.
- fold-in은 combination340과 같은
  `solve(Y.T@Y + 0.1*n_supported*I, Y.T@rating)`이다. `+0.1*I`로 바꾸지 않는다.

이는 ALS가 cold item을 직접 예측한 결과가 아니라 **ALS 협업 공간을 콘텐츠로 전이한 점수**다.
REC-EV-026의 E5→BPR ridge 선례를 현재 Qwen→ALS32 축에 다시 구현한다.

## 보정, 결합, router 선택

MSE·MAE용 별점 보정은 calibration 90명의 해당 cap에서 점수 가능한 전체 행으로 모델당 하나의
`a+b*x (b>=0)`를 적합하되, 각 사용자의 행 가중치 합을 1로 맞춘 뒤 전체 가중치를 합 1로 정규화한다. 최소
20명·40행이 없으면 `INSUFFICIENT`, 가중 raw variance가 `1e-12` 이하이거나 covariance가 음수이면
`b=0`인 상수 보정으로 고정한다. 오류 지표에서만 [0.5, 5]로 clip한다. 순위는 clip 전 점수와
movie_id 오름차순 tie-break를 쓴다. 양의 slope인 단독 모델은 raw와 affine 후 순위가 같다. `b=0`이면
모든 점수가 tie이므로 movie_id 순이다. 기존 FM·GBT 각 seed 보정계수는 같은 산식 재현 여부를 검사한다.

`ALS_QWEN`은 각 head를 affine한 뒤 clip하지 않은 별점축으로 맞춰
`(1-content_weight)*ALS + content_weight*QWEN_DIRECT`를 계산한다. 별도 2차 calibrator를 두지 않아
weight 0이 ALS와 정확히 같아야 한다. `content_weight={0, 0.1, 0.25, 0.5}`를 calibration의
W_DIRECT H10에서만 비교한다. ALS 대비 MSE와 NDCG@2가 모두 나빠지지 않고 하나 이상 엄격히 좋아지며,
Top2 실제 평균별점 손실 <=0.1, 낮은 별점(<=2) 비율 증가 <=3%p인 가장 작은 양의 가중치를 선택한다.
없으면 0을 선택해 ALS를 유지한다.

cold head는 calibration C H10에서 `GBT120_s339`을 incumbent로 둔다. 다른 후보가 같은 Pareto·안전
조건을 통과할 때만 선택한다. 운영 후보는 `STRUCTURED_DIRECT`, `QWEN_DIRECT`, `ALS_C2F`,
`FM150_s339`으로 고정하고 E5·seed344/345·SEED_MEAN은 제외한다. 모든 후보와 incumbent에서 MSE와
NDCG@2가 유효한 공통 uid만 사용한다. 통과 후보 중 NDCG@2 최대 → MSE 최소 → config 후보 순으로
선택하며, 없으면 GBT120_s339를 유지한다. comparison 결과를 본 뒤 head나 가중치를 바꾸지 않는다.

```text
h=0                              -> STARTER (이번 개인화 승자 판정에서 제외)
사용자 ALS factor 있고 item factor 있음 -> 고정 warm head
사용자 ALS factor 있고 item factor 없음 -> 고정 cold head
필수 점수 없음                    -> GBT120_s339 fallback
```

Router는 선택된 각 head의 affine 후·clip 전 별점축 점수만 한 후보 리스트에 놓는다. raw ALS/cosine/FM
단위를 직접 섞지 않는다. 오류 지표에서만 최종 점수를 clip한다. GBT fallback까지 nonfinite이면 해당
context를 조용히 다른 값으로 채우지 않고 fail-closed로 실험을 중단한다.

## 지표와 판정

- 주 평가: comparison 180명, H10, h>0, 같은 사용자의 같은 관측 후보 J.
- 오류: 각 사용자 안에서 먼저 평균한 뒤 사용자를 동일 가중한 보정 MSE, MAE, bias.
- 순위: NDCG@1/2/4/6, Top2/4/6 실제 평균별점, good>=4, low<=2,
  Top2 중 한 편 이상 low, pairwise ordering accuracy.
- 페이지: 1–2, 3–4, 5–6을 따로 집계하고 각 지표의 유효 사용자·행 분모를 공개한다.
- C는 주 cold 비교, V/E 방향 재현과 support·개봉연대·TMDB vote수는 진단이다.
- NATURAL_ZERO는 NDCG@2 유효 사용자가 30명 미만이므로 기술통계만 낸다.
- 전체 85,517편 카탈로그는 H10/h>0 comparison 사용자에서 Top2/4/6 coverage, UNKNOWN,
  고유 노출 영화, HHI, 지원0 노출, 실행시간을 진단한다. UNKNOWN을 정확도 분모로 바꾸지 않는다.
- 이 진단은 `final344/catalog-seal.json`이 봉인한 comparison 180명의 `catalog-cache/{uid}.npz`에서
  `ei`와 FM·GBT 세 seed를 재사용한다. 각 `ei`가 개봉일 조건을 지키고 사용자가 본 영화를 제외한
  canonical catalog row임을 다시 검사한 뒤, 새 Qwen·ALS-C2F·warm head를 정확히 같은 `ei`에 점수화한다.
  93,230행 관측 예측 배열을 전체 카탈로그 점수처럼 사용하지 않는다.

NDCG gain은 `(rating-0.5)/4.5`, discount는 `1/log2(rank+1)`이며 해당 그룹 target이 N편 이상인
사용자만 NDCG@N에 넣는다. `low2`는 Top2 슬롯 중 `rating<=2` 비율, `any_low2`는 한 편 이상,
`both_low2`는 두 편 모두다. calibration의 결합/head 선택과 comparison의 최종 안전 판정 모두
warm은 ALS, cold와 router는 GBT120_s339 대비 `stars2`와 `low2`를 사용한다.

사용자 단위 paired bootstrap 20,000회, seed346을 쓴다. 사전 고정한 대조는 다음 14개뿐이다.

- warm/W_DIRECT/H10: `ALS_QWEN-ALS` × {MSE(낮을수록 좋음), NDCG@2(높을수록 좋음)}.
- cold/C/H10: `QWEN_DIRECT-STRUCTURED_DIRECT`, `ALS_C2F-QWEN_DIRECT`,
  `ALS_C2F-FM150_SEED_MEAN`, `ALS_C2F-GBT120_SEED_MEAN` × 같은 2지표.
- router/ALL/H10/h>0: `ROUTER_s339-FM150_SEED_MEAN`,
  `ROUTER_s339-GBT120_SEED_MEAN` × 같은 2지표.

Warm 2개 대조는 97.5%, cold 8개 대조는 99.375%, router 4개 대조는 98.75% 양측 percentile
interval을 쓴다. 대조별 두 모델에서 지표가 모두 유효한 공통 uid만 골라 uid 오름차순으로 놓고,
각 대조·지표의 유효 uid 집합 크기가 다르므로 draw를 공유하지 않는다. seed는 UTF-8
`hybrid345-bootstrap-v1|346|{family}|{after}|{before}|{metric}`의 SHA-256 첫 8 byte를 big-endian unsigned로
읽어 PCG64에 넣는다. 유효 공통 사용자가 30명 미만이면 구간과 승패를 내지 않는다. user bootstrap은
현재 고정 영화집합에서 사용자 변동만 반영하며
Qwen·ALS 학습 seed, 새 영화 모집단, 한국 사용자 변동을 포함하지 않는다.

판정 우선순위는 `<30명=DESCRIPTIVE_SMALL_N` → 유리 CI와 불리 CI가 함께 있으면 `TRADEOFF` →
안전 gate 실패 또는 불리 CI만 있으면 `DETECTED_HARM` → 하나 이상 유리 CI·불리 CI 없음·안전 통과면
`ONE_METRIC_ADVANTAGE_NO_DETECTED_HARM` → 그 외 `NO_CLEAR_DIFFERENCE`다. 이 판정은 비열등·동등성이나
`CLEAR_WIN`이 아니다.

## 실행 순서와 중단 조건

1. 입력 경로·해시·ID축과 Qwen 모델 파일을 label-free로 봉인한다.
2. 설계·실행 코드 독립 검토 후, 본 실행과 동일한 canonical 연속 batch8 방식으로 504편(63개 완전한 batch) GPU pilot을
   새로 만들고 유한값, norm, 반복 결정성, 메모리를 확인·봉인한다. 앞선 length-bucket pilot은 자원
   가능성 근거로만 남기고 본 실행 gate로 쓰지 않는다.
3. 새 pilot 산출물의 독립 검산이 PASS일 때만 전수 임베딩을 생성한다.
4. mapper validation으로 alpha를 선택하고 예측·availability를 봉인한다.
5. 그 뒤 평가 별점을 열어 calibration 선택, comparison 평가를 수행한다.
6. 독립 결과 검산 후 최종 보고서를 생성한다.

Qwen은 8시간/8,585,216,000 byte GPU, mapper와 scoring은 각 30분/12GiB RAM을 상한으로 둔다. 배치 8에서 OOM이면
부분 결과를 다른 배치 크기와 섞지 않는다. 새 배치 크기로 전량 clean restart하려면 설계와 검토를
다시 고정하며, 그렇지 않으면 `RESOURCE_EXCEPTION`으로 종료한다. 모델·차원·본문·영화·사용자 축을
자동 축소하지 않는다. 중간 결과로 Jira·GitLab·배포를 수행하지 않는다.

