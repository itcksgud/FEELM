# S2~S4 — 조건부 상세 설계와 실행 금지 조건

상태: DRAFT / 독립 설계 검토 대기. 아래 조건은 연구용 기준이며 제품 정책이 아니다.

## S2: 의미 라벨 분류

### 입력과 시작 조건

S1 input-manifest, 원 TMDB overview, 사람이 확정한 label-registry, 영화×라벨 annotations, leakage-group manifest를 사용한다. 라벨 사전에는 ID, 정의, 포함/제외 기준, 모호한 경우 UNKNOWN, 작성자 유형과 검수일, 근거 출처가 있어야 한다. annotation은 POSITIVE / NEGATIVE / UNKNOWN으로, 누락 필드를 NEGATIVE로 바꾸지 않는다. 기존 source keyword만으로 충족되지 않는다.

평가 모집단은 고정85,517편에서 동일 TMDB/동일본문 및 확인된 근접중복을 합친 콘텐츠 그룹이다. 각 그룹에서 `gold-representative|movie_id` SHA 순서 첫 영화 하나를 대표로 정한다. 언어·source keyword 보유 여부로 제외하지 않는다. train/validation/test 그룹 분리는 label 판정을 보기 전에 봉인한다. validation/test는 각 split의 그룹을 `gold-sample|group_id` hash 순서로3,000개씩(부족하면 해당split 전부) 선택한다. 각 그룹의 추출 확률은 동일하며 원키워드 등록/미등록, 양성/음성, 예측 점수를 기준으로 표본을 바꾸지 않는다. precision의 모집단은 이 콘텐츠그룹 대표 표본이며 원카탈로그 영화수 가중 precision이나 실제 추천 노출 precision으로 확대하지 않는다. 한 그룹의 여러 영화를 독립 표본으로 세지 않는다. train만 별도 양성/음성 지원을 확보할 수 있다.

연구 최소 조건은 검수된 의미 라벨 2개 이상이며 각 라벨에 train 양성100·음성100, 고정 validation 양성40·음성40, 고정 test 양성60·음성60 이상이다. 이는 표본을 채우기 위한 quota가 아니라 고정 표본에서 수정 없이 검사하는 readiness 조건이다. 해당 최소수 미달 시 새 양성/음성 사례를 골라 보충하지 않고 S2를 보류한다. 미확인 gold는 UNKNOWN으로 유지한다. 이번 표준 분류안은 고정 validation/test의 선택 라벨이 모두 의미 판정 완료되어야 하며 UNKNOWN이 남으면 표본을 삭제/교체하거나 음성으로 채우지 않고 S2를 보류한다. 이는 UNKNOWN을 무시한 precision gate를 피하기 위한 명시적 정답 준비 조건이다. 성능 test의 판정을 읽기 전 독립 keeper가 ID/지원량/provenance만 승인한다. 동일 TMDB/동일본문, 알려진 언어판·시리즈·near-duplicate 그룹 누수 점검을 통과해야 한다. S1 전체 지원량을 본 분할은 exploratory 표지를 유지하며 새 최종 test가 아니다. 실제 라벨·분할·source hash가 채워지고 코드가 검토되기 전 실행 금지다.

### 학습과 선택

입력은 overview만이다. label name/keyword list를 연결하지 않는다. TF-IDF: word analyzer, ngram_range(1,2), min_df3, max_df.95, max_features50000, sublinear_tf, L2, lowercase, float64. 빈 본문/전처리 후 영벡터는 train 학습에서 제외하며 최소POS/NEG수도 제외 후 계산한다. validation/test의 빈 입력은 삭제하지 않고 abstain(출력라벨없음)으로 유지해 실제 양성의 recall 실패와 coverage에 반영한다. 어휘와 IDF는 train 본문에서만 fit한다. 언어별 토큰화 최적화는 이번 고정 첫안에 추가하지 않는다.

라벨마다 검수된 POS/NEG train 행만 사용해 LogisticRegression(C=1, solver=liblinear, class_weight=balanced, max_iter=1000, random_state=20260911)를 학습한다. UNKNOWN은 loss에서 제외한다. 병렬1, 전체60분/4GiB 상한, 경고·수렴 실패를 숨기지 않는다. 패키지 설치나 기존 모델 재학습은 없다. 이 설정은 비교할 첫 가설이며 우수성을 사전 가정하지 않는다.

기준선은 train 다수 클래스 예측과 사람이 사전에 확정한 라벨명/별칭의 경계 있는 문자열 매칭이다. 별칭을 에이전트가 자동 생성하지 않는다. keyword source presence는 추가 진단 기준이며 human gold가 아니다.

추천에 실제 사용할6개 route(full train1개, train group을 `oof-fold|group_id` hash mod5로 나눈5개 OOF 모델)를 S2에서 모두 준비한다. 각 route의 전처리는 그 route train에서만 fit한다. 각 OOF train에서 라벨별 POS/NEG 최소20 미달이면 실패한다. 모든 route의 모델·전처리·영화별 routing·임계값을 test 전에 함께 봉인한다.

route별 출력 라벨 threshold는 공통 validation에서 .5/.7/.9 순으로 검사해, precision Wilson95% 하한>=.75이고 recall>=.20인 가장 낮은 값을 선택한다. 예측양성0이면 precision gate 실패다. UNKNOWN이 없다는 시작 조건을 재확인한다. 모든6route에서 통과값이 있는 라벨의 교집합을 test 전에 고정한다. 최소2라벨이 남지 않으면 S3 중단이다. 이 선택용 Wilson은 validation tuning 기준이지 최종 보장 구간이 아니다. 확률 보정 전 raw score를 의미 확률이라고 설명하지 않는다.

test는6개 route와 threshold 봉인 뒤 한 번 채점한다. 각route/라벨의 precision/recall/F1, macro/micro, POS/NEG/UNKNOWN, abstention/지원률, 언어·연대·키워드근거량 층을 보고한다. 모든 사용route/라벨에서 test precision의 Bonferroni-Wilson 하한>=.70를 확인한다. precision 구간 family는6×L이며 z=NormalQuantile(1−.05/(2×6×L))로 고정한다. 서로 다른 route는 같은 test대표 영화를 사용한다. 한 route라도 실패하면 해당 route/라벨을 사후 제거하거나 좋은 route로 바꾸지 않고 S3 전체 진입을 보류한다. 여섯route 각각의 macro-F1−train 다수클래스 기준 차이도 동일 test대표그룹 bootstrap20,000회(seed20260911)의6대조 보정99.1667% 하한>0이어야 한다. precision family와 F1 family는 각각의 명목 범위이며 전체절차의 통합95% 보장을 주장하지 않는다. 수치미달은 정상 실패다.

이 기준을 통과해도 semantic dataset의 표집 범위 밖 정확성은 보장되지 않는다. 표본을 결과에 맞춰 축소하거나 C/threshold를 test에서 바꾸지 않는다.

## S3: 고정 FM과 분류 표현의 연결

### 고정 입력과 네 비교안

기존 foundation340 B FM과 policy341에서 사용한 동일 점수 캐시·카탈로그·사용자 입력/후보를 source manifest로 고정한다. score/source 해시를 채우기 전 실행 금지다. 기존 270명/개인 입력186명/관측 정책158명은 개발 집단이며 새 최종 test가 아니다.

- A: P0, FM 점수 순서.
- B: 기존 장르 P2_010을 그대로 재현.
- C: 확정 label-registry에 exact source 키워드 연결이 있는 **등록 양성**만의 동일 주제축.
- D: S2 threshold로 확정한 예측 다중라벨의 동일 주제축.

C의 미등록은 의미 음성이 아니라 관측 근거 없음이다. C/D의 영벡터는 발견 근거 없음으로 처리하고 후보를 삭제하지 않는다. C의 사용 가능한 라벨 집합과 D의 출력 집합이 같지 않으면 별도 정책 효과가 섞이므로 시작 전에 공통 축·source 연결을 검토한다. source 연결이 없는 사람이 정의한 라벨은 C의 기준선 구성 가능성을 먼저 해결해야 한다.

분류 학습 영화의 D 예측은 S2에서 준비·test검증한5개 route의 out-of-fold 출력과 각 route의 봉인 threshold를 사용한다. validation/test/미주석 카탈로그는 S2 full train route로 추론한다. S3에서 새 route를 학습하거나 threshold를 바꾸지 않는다. 영화별 모델 provenance를 보존하고 in-sample 예측을 OOF라고 하지 않는다. 추천 전체가 새 영화 일반화 평가라는 주장을 하지 않는다.

### 정책

각 영화의 활성 주제 binary vector를 L2 정규화한다. 전체 관측 입력으로 경험 프로필을 만들고, 별점>=4인 입력과의 공통 활성 주제가 하나 이상인지를 긍정 연결로 계산한다. `>=4`는 이번 연구의 명시적 입력 가정이며 개인의 확정 취향으로 단정하지 않는다. C/D에 동일하게 적용한다. 경험량 프로필과 긍정 연결의 분모를 구분한다.

첫3편을 선택할 때 FM 1·2위는 유지하고, 남은 전체 후보 중 기준3위보다 cosine novelty가 .10 이상 높고 기존 전체 입력 프로필 유사도>=.25이며 FM 점수>=기준3위−.30이고 긍정 연결을 갖는 후보 중 최고점 영화를 선택한다. 이 상수는 장르와 동등한 의미를 주장하는 값이 아니라 **사전에 고정한 단일 연구 가설**이다. validation이나 test에서 다시 최적화하지 않는다. 없으면 기존3위 fallback. 입력/분류 근거가 없으면 fallback. 다음3편도 반환된 영화만 제외하여 같은 과정을 총3묶음 반복한다. movie_id tie break, 기시청·중복 배제, 전체 공통 후보 유지.

주 효과 D−C는 동일 정책에서 원키워드 등록 표현 대신 분류 예측 표현을 사용하는 구성의 효과다. 정보 가용량과 오류도 함께 바뀐다. D−B는 새 표현과 긍정 연결 조건까지 포함한 전체 구성 비교이므로 분류기 단독 인과효과로 해석하지 않는다. B와 완전히 일치하는 정책 재현 assertion을 둔다.

## S4: 실제 추천 평가

### 정답과 독립 새로움

S3 전안 순위·코드를 봉인한 뒤만 기존 관측 별점을 읽는다. 관측 후보 사용자·후보 쌍은 전안 동일하다. FM 점수나 실제별점이 있는 후보만 D에 남기는 추가 필터는 금지한다.

주제 새로움 주평가는 D의 예측값을 사용하지 않는다. 모든 비교안에서 반환된 영화와 사용자 입력 영화에 대해 **정책/모델/랭크를 숨긴 별도 사람 reference annotation**을 확보해야 한다. 사람이 검수할 대상은 무작위로 섞고 같은 영화는 한 번만 판정한다. 의미 reference가 없으면 주제 발견 검증은 BLOCKED_REFERENCE_LABELS다. 원장르 novelty는 보조 proxy로만 계산할 수 있고 이것으로 주제를 발견했다고 주장하지 않는다.

reference 축은 S2 test 전에 고정한 공통 label-registry의 동일L개다. 영화i의 reference binary vector r_i는 POS=1, NEG=0이며 UNKNOWN이 하나라도 있으면 annotation incomplete다. 완결됐지만 모든 값이0인 영화는 `ANNOTATED_ALL_NEGATIVE`로, UNKNOWN과 구분하여 taxonomy coverage 부족으로 표시한다. 양의 norm인 완결 r_i만 x_i=r_i/||r_i||로 변환한다. 사용자별 O의 모든 영화가 완결이고 양의 norm이며 전안 첫3편의 합집합 영화도 동일 조건을 만족해야 해당 UID의 reference 지표가 계산 가능하다. 입력O가 비면 지표 불가다. p_u=normalize(sum_{j in O} x_j), N(u,i)=1−clip(x_i·p_u,0,1), 정책별 N3는 반환 첫3편 N의 산술평균이다. 모든 정책이 같은reference와수식을 사용한다. all-negative 입력/후보를 novelty1로 보상하지 않는다.

모든A/B/C/D에 필요한 reference가 완결된 공통 UID 비율과, 위수식의 지표가 계산 가능한 공통 UID 비율을 구분한다. 주158명 중 두 비율 모두95% 이상(최소151명)이어야 한다. 비교별 서로 다른 complete cases를 사용하지 않는다. 본문과 주제축의 결측 이유를 별도 공개한다.

주 paired 차이 delta_u=N3_D−N3_B 또는 N3_D−N3_C의 범위는[-1,1]이다. 공통 계산가능 UID에서는 실제delta를 쓰고 나머지는 하한delta^L=-1/상한delta^U=+1을 둔다. 전체158명 평균의 identification bounds는 sum(delta^L)/158, sum(delta^U)/158이다. 아래 shared158 UID bootstrap의 **각 재표집 안에서도** 이 하한/상한 endpoint의 평균을 구한다. novelty gate에는 하한endpoint bootstrap의 보정분위수 .05/(2×6)를 쓰며 이것이0보다 커야 한다. 상한은 상한endpoint의1−.05/(2×6)분위수다. 동일6대조 family 보정이 이 경계에도 적용된다. 실제별점/낮은별점의 나머지4대조는 reference 결측으로 사람을 제외하지 않고 전체158명을 채점한다. 관측 완결 부분의 CI만으로 PASS하지 않는다.

### 고정 지표와 판정

주 집단은 h>0·관측후보 J>=3의 기존158명이며 신규 ID/분모 drift는 실행 오류다. fallback을 포함해 첫3편으로 비교한다. 별점은0.5~5.0 원값, 낮은별점<=2. 주 비교 D−B, D−C 각각의 (1) reference novelty, (2) 실제 평균별점, (3) 낮은별점 비율 6대조를 한 family로 둔다. 동일 UID 재표집20,000회, seed20260911, Bonferroni 양측99.1667% 명목 구간. 새로움 하한>0, 별점 하한>=−.10, 낮음 상한<=+.03을 두 비교 모두 만족해야 현재 고정설정의 추가 기여를 지지한다. CI는 개발자료 선택 편향을 없애지 않는다.

주집단에서 발견 공급률10% 이상도 최소 조건으로 둔다. 이것은 사용자 만족도 기준이 아니라 극소수만 교체한 결과를 전체 개선으로 포장하지 않을 연구 조건이다. 전체 카탈로그 입력186명에서도 공급률을 별도 공개하며10% 미달이면 카탈로그 적용성 부족으로 표시한다. A 대비, 첫3/6/9편·Top2/4/6·발견 수신자만의 품질, 장르 novelty, NDCG·좋은별점>=4 등은 보조 기술통계이며 주승자 선택을 바꾸지 않는다. 입력0 84명은 fallback·품질 회귀가 없는지 확인한다.

전체 카탈로그에서는 UNKNOWN을 유지하고 분류 지원·공급률·fallback·새 영화 수·인기도/투표수/언어 쏠림·시간/메모리를 보고한다. 관측부분 평균으로 미관측 추천 만족도를 추정하지 않는다. 주 기준 미달은 개선 미확인이고, 데이터/정답 부족은 미입증이다. 성공해도 현재 개발 표본의 연구 결론이며 실제 서비스 채택/노출정책 변경이 아니다.

## 단계별 미확정 실행 자료

S2: 사람의 label-registry/annotations/그룹 manifest 및 hash가 아직 없다.
S3: 선택된 분류기, 라벨 threshold, source-라벨 연결, score/cache manifest 및 실행 코드 hash가 아직 없다.
S4: 독립 사람 reference, 전안 순위 seal 및 실행/통계 코드 hash가 아직 없다.

이 문서는 위 설계의 타당성을 검토받기 위한 것이다. 미확정 입력을 에이전트가 사람 정답으로 채우거나, 조건부 설계 승인을 실행 승인을 받았다고 해석할 수 없다.
