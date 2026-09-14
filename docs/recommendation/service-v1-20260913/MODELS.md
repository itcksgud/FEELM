# 추천 v1 모델·특징·학습·비교 계약

상태: **APPROVED — 로컬 구현 기준**. 모델 학습/배포 완료 상태와 구분한다.
[정책](POLICY.md), [기계 특징 목록](feature-schema.v1.json), [운영 연결](OPERATIONS.md).

## 1. 모델의 역할과 현재 파일

| 모델 | 서비스 역할 | 기존 자료 | v1 새 산출물 |
| --- | --- | --- | --- |
| GBT | 기본 노출·후보 배치·재정렬 | GBT120_s339, RH230, 로컬 native 있음 | RH230+KOBIS15=245열로 학습·보정한 bundle 필요 |
| FM | 공통 요청의 비교 scorer | FM150_s339, RH230, portable 있음 | 같은245열 FM은 별도 학습 후 비교. 그 전230열은 legacy feature 비교임을 표시 |
| ALS | 협업 기준 비교 scorer | 기존 factor·fold-in·보정 자료 있음 | 원 factor를 재사용할 수 있으나 공통 비교용 보정 필요 |

기본 registry는 GBT다. FM/ALS를 구현한다는 것은 같은 후보·사용자 입력으로 점수를 계산해
비교 결과를 저장할 수 있게 한다는 뜻이다. shadow 오류가 기본 응답을 실패시키지 않아야 한다.
실시간 요청의 사용자가 모델 이름을 임의로 지정하게 하지 않는다. 운영 모델 교체는 새 bundle을
검증하고 활성 포인터를 바꾸는 절차다. 오차가 낮다는 이유로 자동 전환하지 않는다.

추가 비교 정책은 [ALS 결합 비교군](HYBRID-COMPARISONS.md)과
[comparison-registry](comparison-registry.v1.json)를 따른다. ALS+GBT·ALS+FM 각각에 전환과
shrink100 혼합을 두어 단독3개+결합4개의7개 점수 경로를 남긴다. 새 학습 모델4개를 추가하는
뜻은 아니다. 각 component의 보정 연속값을 재사용하며 기본 노출의 자동 혼합 금지는 유지한다.

기존230열 GBT를 개발 fixture/재현 기준으로 로드할 수 있지만 그 결과에 `featureVersion=rh230`
를 붙인다. 새245열 모델이 없으면 `NOT_BUILT`다. 이 문서는 모델 파일의 존재를 대신하지 않는다.
KOBIS를 후보에 사용하는 단계만 끝났을 때 ‘개인 점수에도 KOBIS를 학습했다’고 기록하지 않는다.

기존 경로는 다음과 같다. 모두 개인 연구 자료이므로 팀/EC2에 이미 있다는 뜻은 아니다.

- GBT: `outputs/recommendation-evidence/final344/GBT120_s339/model/native/`
- FM: `outputs/recommendation-evidence/final344/FM150_s339/model/portable.npz`
- 특징: `outputs/recommendation-evidence/foundation340/RH/feature-info.json`
- 기존 보정: `outputs/recommendation-evidence/hybrid345/calibration.json`
- 분류: `.codex-tmp/fixed-k8-discovery-v2-20260913/outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2/cluster/GKT-K128-hierarchy.pkl`

분류 파일 하나에는 전처리·상위8 중심이 없다. [GROUPS](GROUPS.md)와
[그룹 구성 설정](group-recipe.v1.json)의 허용 키만 결합해야 한다. 기존 final/bundle 전체를
실행 객체로 가져오면 하위16·그룹25편·과거 predictor 설정이 섞이므로 금지한다.

파일은 배포 전에 원 manifest/SHA와 대조해 새 artifact 저장소로 복사한다. 절대 로컬 경로를
EC2 설정에 그대로 넣지 않는다. 출처·객체 경로·바이트·SHA·runtime을 새 manifest에 기록한다.

## 2. 공통 scorer 입출력

```text
score_batch(context, movie_ids, feature_pack, model_bundle) -> ScoreResult[]

context: userId, rankingInputVersion, originalHistory, originalCap, catalogVersion
feature_pack: featureVersion, orderedNamesHash, float32 row vectors, per-source validity
ScoreResult: movieId, modelId, modelVersion, featureVersion,
             supported, unsupportedReason, rawScore, calibratedScore, predictedRating
```

unsupported이면 모든 점수는 null이다. 실제0점을 넣어 다른 모델 평균에 섞지 않는다.
비교에서도 ALS 미지원과 예측 오차를 분리한다. 실제 사용자 별점은 별도 원본 엔티티다.
점수 순위는 보정 연속값 DESC/ID ASC, 오차·예상 별점 표시는 `[0.5,5]` clipping이다.
예측값을0.5 단위로 반올림해 정렬하거나 학습 정답으로 저장하지 않는다.

GBT/FM는 동일 사용자 ID에 고정 벡터 하나를 저장해서만 계산하는 모델이 아니다. 현재 원본
이력에서 영화 속성에 대한 반응을 만들고 같은 공유 모델에 입력한다. 서비스 user ID를
MovieLens user ID로 대체하지 않는다. ALS도 서비스 사용자는 지원 이력에서 fold-in한다.

`model_input_supported`는 서비스 영화에 연결된 RH230 catalog 입력행이 있고, 장르·키워드·
국가/언어·개봉시대·러닝타임·감독/배우·제작사/시리즈·유효 TMDB 채널·유효 KOBIS 중
실제로 관측한 원천 필드가 하나 이상일 때 true다. 이 조건을 만족하는 선택 H의 편수를 GBT
유효 이력 수로 센다. 기본값/결측0·사용자 별점 자체·벡터가 비제로라는 이유로 true로 만들지 않는다.
RH230에 사용하지 않는 줄거리만 있는 영화도 이 조건을 충족하지 않는다. 원천의 추천 하한은
여기 적용하지 않는다. 후보 입력 지원도 같은 판정을 쓰고 false이면 MODEL_INPUT_UNAVAILABLE다.

## 3. GBT·FM의 공통245열

### 기존230열

기존230개 이름·순서·수식은 `feature-schema.v1.json`과 함께 고정한 기존 변환 코드의 계약을
그대로 따른다. prefix를 임의로 바꾸지 않는다. 기준 코드는 `rec046_common.py`,
`rec047_features.py`, `cold_item_features.py`, `foundation340_features.py`, `final344_adapter.py`,
관계 특징의 하위 의존성인 `text339_relations.py`다.
새 구현은 파일 해시 및 원230열 fixture와 대조해야 한다.

| 범위 | 정보 |
| --- | --- |
| x000~021 | 후보 장르18개·개봉연도·러닝타임과 존재 여부 |
| x022~091 | 장르·키워드·국가/언어·시대·감독·배우·제작사/시리즈와 사용자의 반응 |
| x092~163 | 장르별 이력 비중·별점 반응·후보 교차 |
| x164~199 | 이력 별점 평균·SD·편수·관계 및 장르 근거량 |
| x200~205 | TMDB 평균평점·투표 수·popularity 값과 present |
| x206~229 | TMDB 각 채널별 유효 이력 비율·평균·분산·근거·공분산·차이·교차·존재 |

GBT 장르18차원과 발견의 GKT 장르19차원은 서로 다른 고정 전처리다. 맞추려고 열을 더하지 않는다.
팝콘 맛/하위 그룹 ID 자체는245열에 추가하지 않는다. 그룹은 후보 검색에 쓰고 원 속성은 모델에 쓴다.

TMDB 평균 유효 조건은 `0<R≤10`, v>0이다. 투표·popularity는 유한한0 이상 값이 유효하다.

```text
rating_z = (v×R + 48×6.173088067675869)/(v+48)/10
votes_z  = clip(log1p(v)/log1p(100000),0,1)
pop_z    = clip(log1p(popularity)/log1p(1000),0,1)
```

평균이 결측이면 rating_z=0,present=0이다. 실제 votes=0과 결측은 present로 구분한다.
현재 선정 모델의 TMDB 평점 축소는 실제 학습에서 사용한 처리다. 원평점/10을 그대로 넣지 않는다.
원 스냅샷과 prior는 첫 대조에서 고정하고 결측 처리를 바꾸는 효과와 재수집 효과를 섞지 않는다.

기존24개 TMDB 반응 열의 비율 분모는 매핑 후 실제 입력 k다. 아래 KOBIS H와 다르므로
두 출처의 이력 비율을 같은 정의라고 설명하지 않는다. 원천 valid와 추천 후보 최소21표/1만
조건은 다르다. 유효한 낮은 관객·낮은 투표값도 이력 반응에 남긴다.

### KOBIS15열

관객은 검증한 같은 누적 범위의 수치만 쓴다. `z=clip(log1p(audience)/q99,0,1)`이며
q99는 학습 역할의 유효 영화별 `log1p(audience)` 99백분위(선형 보간)다. 영화 한 편을 한 번
사용한다. 확인용 영화·평가량으로 가중하지 않는다. q99≤0 또는 학습 원천 부족이면 준비 실패다.
입력은 [KOBIS-DATA](KOBIS-DATA.md)의 검증된 crosswalk·누적 범위·기준일을 통과한 영화 값이다.
수집한72,700개 영화·연도 행을 그대로 q99에 넣거나 제안 TMDB ID로 학습 목표와 연결하지 않는다.
같은 영화의 여러 연도는 중복 학습 근거가 아니며, 음수 누적은 valid=false로 처리한다.

| 열 | 이름·계산 |
| --- | --- |
| x230~232 | 후보 z, valid, 원 log값이 학습 q99를 초과했는지 |
| x233 | 유효 이력 편수 m / 선택 원본 편수 H |
| x234~235 | 실제 m, m/(m+5) |
| x236~237 | zbar, Σ(z−zbar)²/m |
| x238~239 | 유효 이력 ybar, ybar−선택 원본 전체 y평균 |
| x240 | Σ(z−zbar)(y−ybar)/(m+5), y=실제별점/5 |
| x241~242 | 후보 z−zbar, 이 차이×x240 |
| x243~244 | history_present, response_estimable |

서비스/주 평가 H≤10, 학습은 기존 행 cap0/1/5/10/30을 유지하므로 m≤30이다. KOBIS만 별도로
최신10으로 자르지 않는다. m<2 또는 지표 분산≤1e−8이면 cov·교차0, estimable0이다.
후보가 결측이면 후보 z/valid/초과 플래그와 delta·교차는0이다. m=0이면 평균·분산·별점 평균·
전체 평균과 차이도 계산용0, history_present0이다. H=0의 학습 행은 유지한다.

TMDB와 KOBIS를 원값 max로 합치지 않는다. 흥행은 높지만 평균평점은 낮은 정보도 보존한다.
후보에서 쓰는 best_rank를 개인 예상 별점에 추가 가산하지 않는다. 높은 인기도를 모든 사용자에게
좋다고 강제하는 단조 제약·한국영화 고정 보너스도 없다.

### 같은 스냅샷으로 갱신

외부 값 갱신 시 후보 영화의 값과 사용자가 평가한 이력 영화의 값·평균·공분산을 함께
재계산한다. 새로운 관객 수와 이전 사용자 반응 캐시를 섞지 않는다. q99/prior는 모델 버전에
고정하고 일반 일별 스냅샷에서 재적합하지 않는다. 새로운 범위의 값은 clipping/초과 플래그로
처리하고 분포 이동을 로그에 남긴다. 새 특징 의미가 필요하면 새 학습 버전이다.

## 4. 계산 adapter

GBT는 Spark native treeWeights와 각 나무의 분기/leaf를 동일하게 계산한다. 임계치의
`≤`/`>`·float32 입력·categorical split·NaN 거부·열 순서를 보존한다. 같은245열에 대해
Spark 예측과 Consumer 예측의 절대차≤1e−6을 일반 fixture와 분기 경계 fixture에서 확인한다.
고정 현재 모델의 임계치를 새 모델에 재사용하지 않는다.

FM은 모델 파일의 intercept, linear, factors를 사용한다.

```text
raw = intercept + dot(linear,x)
      + 0.5 × sum_f((sum_j factors[j,f]×x[j])² − sum_j(factors[j,f]×x[j])²)
```

학습 시 포함한 linear/intercept 옵션과 float 정밀도를 유지한다. native와 portable의
동일 입력 비교를 통과해야 한다. 차원이230인 기존 portable을245입력으로 실행하지 않는다.

ALS는 실제 별점 원점수와 영화 factor로 fold-in한다.

```text
u = solve(YᵀY + lambda×n_supported_history×I, Yᵀr)
raw_ALS = q_iᵀu
```

lambda·rank·별점 변환은 factor를 만든 모델 manifest와 일치시킨다. 기존 연구 lambda는0.1이다.
입력 지원 영화0편, 후보 factor 없음, 비유한 해는 unsupported다. TMDB 투표·KOBIS 관객을
실제 별점이나 n_supported_history로 넣지 않는다. ALS 영화 factor는 외부 메타데이터가 아닌
사용자 평점으로 학습한 것이다. v1에서는 ALS 점수도 비교 로그에 남기며 GBT를 덮어쓰지 않는다.

## 5. 학습·보정 역할과 실행 범위

현재 요청의 실행은 데이터 분석과 문서 작성이다. 아래는 개발자가 구현할 학습 계약이며 새
학습을 이미 수행했다는 뜻이 아니다. 기존 `final344_worker.py`는230열·정확한 원행 수를
전제하므로 그대로245열/증강 데이터를 실행하는 명령으로 소개하지 않는다.

1. 기존 학습 사용자·영화 차단 역할·목표행 ID를 고정한다. 평가 사용자를 학습에 추가하지 않는다.
2. 목표 시각보다 엄격히 이전인 원 이력만 사용하고 목표 영화의 평가를 이력에서 제외한다.
3. 원499만 행의 cap0/1/5/10/30을 보존한다. 추천 최소 근거 하한으로 학습 행을 지우지 않는다.
4. 기존90명 보정 풀을 `SHA256("cal-select-v1:"+user_id)` 오름차순으로45명 보정/45명 선정으로
   나누고 기존180명 확인 풀은 유지한다. 숫자 ID는10진 문자열, UTF-8, 동률 user ID ASC다.
5. 모델별 affine은 동일 보정45명의 주 cap10 공통 목표에서 적합한다. ALS는 지원 목표만 사용한다.
6. 별점 y에 대한 사용자 동일 가중 최소제곱 `a+b×raw,b≥0`를 사용한다. 사용자 u의 각 행 weight는
   `1/n_u`다. b=max(0,weighted_cov(raw,y)/weighted_var(raw)), a=weighted_mean(y)−b×weighted_mean(raw).
   raw 분산≤1e−12·유효자료 없음이면 CALIBRATION_UNAVAILABLE이며 임의0계수로 성공시키지 않는다.
7. 같은45명으로 기존 GBT/FM/ALS도 다시 보정한다. 기존90명 보정의 점수는 legacy 참고 표로만 남긴다.
8. 선정에서 고정한 계수/설정을180명 확인 결과를 보고 다시 맞추지 않는다. 이는 이미 열어본
   개발 자료이며 새로운 블라인드 test가 아니다.

기존 train.parquet에는 row_id/uid/label/x열만 있으며 target movie ID는 없다. 새 특징을 만들기
전에 원 `text339/{ratings,episodes,catalog}.parquet`와 contexts.json, prepared-seal을 검증한다.
원 코드처럼 정렬된 Histories의 row_id 위치에서 목표 ML movie_id·시각·assigned cap·엄격한
과거 이력을 복원하고, 기존 row_id/uid/label의 순서와 원230열 parity를 전부 확인한다.
이 연결을 `row-lineage.parquet`에 row_id,uid,target_ml_movie_id,target_timestamp,original_cap,
history_ml_ids/history_stars/history_timestamps로 저장한다. 별도의 서비스 ID가 없다는 이유로
학습 행을 제거하지 않는다. 평가 목표는 contexts의 ei와 같은 catalog.movie_id 인덱스로 복원한다.
이력 재구성이 불가능하거나 원행/특징이 다르면 새 학습 준비를 중단한다.

최신 TMDB/KOBIS 수치로 과거 MovieLens 별점을 학습하므로 당시 사용 가능했던 정보만으로
미래 예측했다고 말하지 않는다. 이 한계와2026 한국 사용자에 대한 미검증을 모델 card에 남긴다.

## 6. 결측 학습과 새 모델 준비 조건

GBT 준비 비교는 기존 B0, TMDB 결측 증강 B1, KOBIS 영화3열 B2, KOBIS 반응12열까지 B3의
최대3회 새 GBT 학습으로 분리한다. v1의245열 목표는 B3이다. B1/B2를245열인 것처럼 배포하지 않는다.
모델 기본 가족을 GBT로 정한 것과 B3 파일이 품질/기술 조건을 통과한 것은 별개다.
각 변형은 [training-recipe](training-recipe.v1.json)의 이름·열 목록을 따른다. B1은230열,
B2는233열, B3은245열이다. 없는 채널을 가린 view도 삭제하지 않고 동일 행으로 남겨서 네 view를
유지한다. FM245는 B3 특징으로 최대1회 비교 학습이며 B1/B2별 추가 FM 탐색은 하지 않는다.
B0는 과거 자연 상태1view로 학습한 기존 모델이다. B1부터는 네 view와 학습 행 수가 달라
GBT subsampling/분기에도 영향이 있으므로 B1−B0를 결측 처리만의 순수 효과라고 부르지 않는다.
B0는 실제 기존 모델 대조, B1~B3는 같은 네 view에서의 특징 확장 비교다.

증강은 원행당 자연 상태·TMDB 가림·KOBIS 가림·양쪽 가림4view다. 영화 식별자는 서비스 ID가
아닌 원 MovieLens 정수 ID를10진 문자열로 표현한 `ml:<movie_id>`다.
`SHA256("train_mask:339:ml:"+movie_id)`를 정수로 읽어 전체 범위의 하위20%인 영화에
가림을 적용한다. eval은 `eval_mask:339:ml:`로 분리한다. hash/(2^256)<0.2의 의미이며 정확히
영화 수20%를 강제로 맞추는 표본추출이 아니다. 모델 간 같은 mask를 쓴다.
가림은 후보뿐 아니라 그 view의 모든 이력·valid·평균·공분산·m까지 다시 계산한다.
여기서 TMDB 가림은 **평균평점·투표 수·popularity의 세 대중 채널**에 한정한다. 장르·인물·
줄거리 등 영화 콘텐츠를 지우는 실험이 아니다. KOBIS 가림은 관객 채널이다. 두 출처의 mask는
같은 영화 선택을 사용하며, 학습 정답/원본 H/cap/목표 시각은 바꾸지 않는다.
GBT 각view weight0.25, 원행 합1을 유지한다. 인위적 가림은 실제 구조적 결측을 대표하지 않는다.

고정 GBT 설정은120trees, depth5, maxBins32, stepSize0.05, subsamplingRate0.8,
featureSubsetStrategy=sqrt, loss=squared, seed339다. 나머지는 기계 학습 설정의 원 resolved
map을 유지하며 `weightCol`만 증강 계약에 맞춰 추가한다. 기존 학습된 나무를 고정하는 것은 아니다.

FM245 비교가 가능해지면 같은245열·원행·역할로150iterations, factorSize16, fitLinear/
fitIntercept=true, adamW, regParam0.01, miniBatchFraction0.25, stepSize0.01, tol1e−6,
seed339를 사용한다. Spark4.1.3 FM은 API에 weight 속성이 보이지만 실제 학습 소스는 label과
features만 읽는다. `weightCol`을 전달해0.25가 적용됐다고 가정하지 않는다.
[고정 버전의 실제 FM 학습 코드](https://raw.githubusercontent.com/apache/spark/v4.1.3/mllib/src/main/scala/org/apache/spark/ml/regression/FMRegressor.scala).
FM에는 **모든 원행의 네 view를 빠짐없이 같은 횟수로 넣고 행 weight를 전달하지 않는다**.
균등한4view의 상대 표본 비중이0.25씩인 구성이며, GBT도 같은 원행/view를 쓴다. view를 일부
삭제·중복하거나 사용자별 가변 weight를 적용하는 최적화는 이번 비교에 없다. manifest에
`FM=EQUAL_FOUR_VIEWS_UNWEIGHTED`, `GBT=FOUR_VIEWS_WEIGHT_0_25`를 구분해 기록하고
각 원행의view수4·모델 간 동일 행/특징을 검사한다. 서로 다른 알고리즘의 학습 절차 자체가
동일하다는 뜻은 아니다. 기존230열 비교는245열 비교와 분리한다.

KOBIS는 연도별 전체72,700행 수집과 원문 검산까지 완료했다. 이전의 ‘Top200만 있어 저관객
자료가 없다’는 상태는 바뀌었다. 다만 직접 코드2,176개의 표본은 연도별 화면 첫100행에서 왔고,
확대 자료의 TMDB/서비스 확정 연결은0개다. 현재 남은 문제는 **영화 식별·누적 기준 통일·평가 교집합 검증**이다.
이번 연간1만~100만 미만5,352행을 누적 저관객 영화 수나 KO_LOW 평가 표본으로 세지 않는다.

- 고정 학습/보정/선정/확인 원본 이력·목표와 전체 수집 프레임을 대조해 후보 교집합을 보고한다.
  이어 검증된 crosswalk·동일 누적 범위/기준일을 통과한 실제 교집합의 사용자·영화·평가행 수를 계산한다.
  미연결을 TMDB 평균 결측이나0관객으로 바꾸지 않는다. KO_ONLY와 단순 미연결은 별개다.
- KR·저관객 **1만 이상~100만 미만**·KOBIS관측/TMDB평균결측 각 구간에서 선정/확인 각각 최소30사용자·
  30목표영화·200평가행을 요구한다. B3은 m≥2·분산>1e−8인 반응 사용자도 각각30명 이상이다.
- 하한은 검정력을 보장하지 않는다. 부족하면 INSUFFICIENT이며 B2/B3 전체 서비스 채택 판단을
  중단한다. 이 하한을 결과를 보고 낮추거나 인기 영화로 표본을 채우지 않는다.
- 후속 식별·누적 확인은 상위100/200 코드만으로 제한하지 않고 고정 서비스/평가 프레임에서 진행한다.
  같은 영화는 한 번 계산하고, 제안과 확인된 연결의 지원률을 따로 남긴다. 추가 수집이 필요하면
  대상·요청 상한·매핑 코드를 별도 검토한다. 현재 원자료 수집을 다시 선행 미완료 업무로 적지 않는다.

단계별 완료/미완료와 원자료 파일은 [KOBIS-DATA](KOBIS-DATA.md)를 따른다. 수집 검산 PASS는
위 모델 준비 하한을 충족했다는 뜻이 아니며 B2/B3·FM245 학습/성능 결과는 아직 없다.

학습은 기존 고정 Spark4.1.3·4CPU·12GiB·driver8GiB에서 순차 실행한다.4view 약1,999만 행의
용량을 사전 점검한다. 프로세스90분 상한, OOM/시간초과는 미완료로 남기고 자동 증설/행 축소/
재시도를 하지 않는다. 새 학습 실행 코드·split/특징/가중치·메모리 사전 검토를 통과해야 한다.
245개 float32 특징만으로도 확장 행은 약19.6GB다. 단일 NumPy 배열로 모두 적재하지 않고
최대4,096행씩 특징/view를 생성해 Parquet에 기록한다. Spark 학습 입력은 이 분산 파일이며
spill/checkpoint 경로와 실제 여유 용량을 사전 검토에 남긴다. 컨테이너12GiB가 전체 행렬의
메모리 적재를 허용한다는 뜻이 아니다.

## 7. 비교 지표와 노출 분리

같은 입력·후보·버전에서 GBT/FM/ALS를 비교한다. ALS는 지원되는 공통 목표에서만 오차를
비교하고, 전체/미지원의 계산 가능 비율을 따로 보여준다. 일반 GBT 순위의 첫2와 ALS 지원
영화만의 첫2를 같은 후보 추천 품질로 빼지 않는다.

| 질문 | 기록 |
| --- | --- |
| 별점 예측 | 사용자 macro MSE/MAE, 행 micro·영화 macro, 보정 bias |
| 맞춤2편·추가 추천 | NDCG@2/4/6/10, 실제별점 평균,≤2점 비율, 각 맞춤2편 라운드 |
| 발견1편 | 첫1편 및 누적2/3/5/10,≤2점 비율, 그룹 경험·중복·같은 후보 예측 |
| 전체 응답2+1 | 실제 구성, 대체/부분반환 비율, 세션 중복, 재요청 일치 |
| 서비스 카탈로그 | 점수화 지원·UNKNOWN·국적/시기/출처 구성·영화/그룹 집중·pool 소진 |
| 결측/근거 | 출처4조합, 인위 가림, 사용자 유효 m, 후보 평가/관객 규모별 오차 |
| 비용 | 특징 생성/추론/정렬 각각 시간·메모리, batch시간, 입력→새순위 지연 |

관측 별점이 없는 영화는 UNKNOWN이다. 점수0·실패·정답으로 채우지 않는다. 자료 분포 장표는
이 평가의 정답을 보정하는 가중치나 한국영화 가산점이 아니다. 자연 결측 구간이 작으면 해당
지표는 판단 불가다. 사용자/영화의 반복을 고려한 paired 구간을 보고한다.
정확한 정답·분모·재표집·Holm family는 [EVALUATION](EVALUATION.md)을 따른다.

새 GBT는 공통 보정 B0 대비 NDCG@2 감소0.01·Top2 low 증가2%p·핵심 구간 MSE 악화5%
한계를 사전에 적용한다. 비열등을 주장하려면95% 구간도 한계 안이어야 한다. 특징3대조의 우위는
Holm 보정하고 한seed의 결과를 모델군 전체 우위로 확대하지 않는다. 실패/부족이면 새 B3 활성화
조건을 충족하지 못한 것이다. 사용자 지시 없이 FM/ALS로 기본 가족을 바꾸지 않는다.
필수 구간은 EVALUATION의 ALL/ALS 목표 factor 두 층(B1~B3), KR/KO_LOW/KO_ONLY(B2~B3),
KO_RESPONSE(B3)다. 각 구간의 원행·최소 표본과 선정45→확인180 양쪽 통과, bootstrap draw별
상대 MSE·0분모 처리·CI 끝점 방향까지 EVALUATION의 활성화 절차로 고정한다.

shadow 기본은 같은 pool의 모든 ID와 발견 후보≤100에 대해 비교 점수만 저장하는 것이다.
후보 생성까지 모델별로 바꾼 비교는 `END_TO_END`로 분리한다. 이 경우 모델별 전체 적격
카탈로그 점수화→pool500→동일 재정렬/제외 정책을 실행하고 예측 차이와 후보 차이를 함께 보고한다.
기존 GBT pool만 사용한 FM/ALS를 각 모델의 최적 후보 전체 성능이라고 부르지 않는다.
shadow 실행은 비동기로 하고 실패·미지원·feature 불일치를 비교 결과에 남긴다. 실제 사용자가
평가하면 당시 노출/예측/버전과 연결하고 미노출 영화에 임의 feedback을 생성하지 않는다.

## 8. 배포 bundle과 실패

manifest 필수: bundleId, modelId/version, featureVersion/245열 names hash, model file 목록과
SHA/bytes, 고정 runtime, training/split/source manifests, TMDB prior, KOBIS q99, calibration
계수·역할·cap10, GKT preprocess/centers/representatives/assignments 해시, 평가/검토 상태.
분류가 모델별로 달라지는 것이 아니며, 모든 scorer가 같은 카탈로그/정책을 참조한다.

새 artifact는 staging에서 무결성·열·유한값·원천 scope·배치/온라인 parity·빈 이력·미지원·
분기 경계·reload를 검증한 뒤 활성화한다. 새 GBT가 준비되지 않은데 옛230 모델로 자동 위장
하지 않는다. 운영 중 장애는 직전 정상 같은정책 bundle로 원자 rollback하거나 명시된 초기
추천/준비 상태를 반환한다. ALS로 몰래 failover하지 않는다. 모델을 바꾸면 현재 사용자 반응과
pool·순위도 같은 bundle로 다시 준비한다.
