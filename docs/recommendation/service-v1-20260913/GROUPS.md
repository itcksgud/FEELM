# 추천 v1 고정 그룹·콘텐츠 벡터 계약

상태: **APPROVED — 로컬 구현 기준**. 원본 파일 구조·해시를 읽어 확인한 계약이며,
v1 그룹 bundle의 생성·추론·재학습·서버 배포는 아직 실행하지 않았다.
[정책](POLICY.md), [정확한 원본·배열 해시와 구성 키](group-recipe.v1.json)를 함께 사용한다.

## 1. 가져올 파일과 가져오면 안 되는 설정

`cluster/GKT-K128-hierarchy.pkl` 하나로 신규 영화를 배정할 수 없다. 여기에는 하위 중심과
대표·기존 할당은 있지만 전처리와 상위8 중심이 없다. 다음 allowlist로 새 그룹 bundle을 만든다.

| 원본 | 복사할 키 | 쓰임 |
| --- | --- | --- |
| DV2 r2 `final/bundle.pkl` | `preprocessor`, `top_centers`, `top_weights`, `top_names` | 고정 G-913 상위8 및 같은 영화 전처리 |
| DV2 r2 `cluster/GKT-K128-hierarchy.pkl` | `centers`, `sub_weights`, `representatives.mean`, `offsets`, `geometry_version`, `space_hashes` | 각 맛128 중심 및1,024개 검색 대표 |
| 같은 `GKT-K128-assignments.npz` | `service_movie_id`, `taste_id`, `child_id`, `group_id` | 원 기준 스냅샷의 ID축과 검증 기준 |
| 같은 `prepare/catalog.parquet`, `prepare/content.npy` | 기준 영화 원천·GKT131 벡터 | 전처리 및 기준 할당 parity 검증 |

`final/bundle.pkl` 전체를 v1 실행 객체로 로드하지 않는다. 그 안의 실제 선택 정책은
`v1-fixed16:group:mean:B50:Q25`, 하위 가중치는 `[1,0,0]`다. `policy`, `predictor`,
`child_centers`, `sub_weights`, `representatives`, `quality`, `runtime_rules`를 여기서
복사하면 현재128개/10×10/GBT 단독 정책과 달라진다. 기존 router·`variant=original` 실행도 금지한다.
반면 K128 파일의 실제 `sub_weights`는 `[0.5,0.35,0.15]`이며 중심은8개의128×131 배열이다.

복사 전 전체 파일 SHA·bytes와 지정 배열 SHA를 모두 확인한다. 원본 ID를 현재 DB ID로
추정하지 않는다. 기준 카탈로그 `service_movie_id↔tmdb_id`와 현재 DB export를 검증해
연결하고, ID축이 달라진 배포에서는 TMDB ID를 통해 현재 serviceMovieId를 명시적으로 매핑한다.
고정 맛·그룹 ID는 재번호화하지 않는다. 기준 할당과 현재 카탈로그 할당은 별도 버전으로 저장한다.

## 2. 영화에서131차원 벡터를 만드는 순서

입력은 TMDB `genre_ids`, `keyword_ids`, `overview`다. Wikipedia·QWEN embedding·대중평점·
관객은 이 공간의 입력이 아니다. 이 GKT 전처리는 GBT/FM의 RH230 장르18열과 별개다.

1. 장르/키워드 ID는 양의 정수의 중복 없는 목록으로 정규화한다. bool·비정수·0 이하 ID 원소는
   제외하고 원천 오류 수를 기록한다. 목록이 아닌 필드는 빈 목록과 INVALID_TYPE으로 남긴다.
   결측은 빈 목록, overview 결측/문자열 아닌 값은 빈 문자열과 결측/오류 사유로 기록한다. overview는 원문
   앞뒤 공백만 제거하고, 번역·요약·불용어 제거·새 tokenizer를 추가하지 않는다.
2. 장르는 고정19개 vocabulary 순서의 binary multi-hot이다. 복수 장르를 모두 반영한다.
   알려지지 않은 ID는0기여로 기록하고 vocabulary를 확장하지 않는다. L2 정규화해 `g`를 만든다.
3. 키워드는 고정6,543개 vocabulary에 binary로 표시하고 고정 IDF를 곱한다. sparse 행을 L2
   정규화한 뒤 고정64×6,543 components의 전치와 곱하고 다시 L2 정규화해 `k`를 만든다.
4. overview는 저장된 `text_vectorizer.transform`만 사용한다. 실제 사양은 unigram, lowercase,
   `(?u)\b\w\w+\b`, smooth IDF, L2, stop_words=null이며 vocabulary16,000개다.
   고정48×16,000 components의 전치와 곱한 뒤 L2 정규화해 `t`를 만든다. fit/fit_transform 금지다.
5. 다음 두 벡터를 따로 만든다. 연결한 벡터도 다시 L2 정규화한다.

```text
top_x = L2(concat(g, 0×k, 0×t))
sub_x = content_x = L2(concat(sqrt(0.5)g, sqrt(0.35)k, sqrt(0.15)t))
```

모든 기하 계산은 float64다. `L2(v)`는 norm>1e−12일 때만 나누고 나머지는 같은 차원의0벡터다.
원 source의 수치 구현은 해시로 고정한 `k8_common.{csr_ids,norm,combine,transform,nearest}`다.
다른 함수·옛 signed_profile·실행 모듈의 부수효과를 통째로 재사용하지 않는다.
TF-IDF의 min_df/max_features는 기존 학습 설정의 기록이며 서비스에서 다시 fit할 조건이 아니다.

고정 원본은 python3.12.5, numpy1.26.4, scipy1.15.2, sklearn1.9.0이다. 다른 runtime으로
내보내면 같은 전처리/배열·거리·할당 parity를 통과해야 한다. pickle을 외부의 임의 파일에서
역직렬화하지 않으며, 검증된 원본에서 serving용 배열/어휘/IDF/파라미터로 분리해 내보낸다.

## 3. 배정 중심과 검색 대표

상위 배정은8×131 `top_centers`와 `top_x`의 제곱 유클리드 거리 최솟값이다. 그 taste_id의
128×131 `centers[taste_id]`와 `sub_x` 거리로 child_id를 정한다.

```text
d(x,c) = sum_j (float64(x_j)−float64(c_j))²
top_id = argmin_c d(top_x, top_centers[c])
child_id = argmin_c d(sub_x, child_centers[top_id][c])
group_id = 128×top_id + child_id
```

축 순서를 고정한 원소별 차이/제곱/합으로 계산한다. `||x||²+||c||²−2x·c`나 BLAS의 배치
행렬곱으로 바꾸지 않는다. 정확히 같은 거리이면 낮은 고정 ID다. 운영 시 중심 재정렬,
canonicalization, 빈 그룹 통합, 최소 표본에 따른 K 축소를 하지 않는다. 실제 offsets는
`[0,128,256,384,512,640,768,896,1024]`다.

검색 대표 `mean[g]`는 원 기준 스냅샷에서 그 그룹에 배정된 **GKT 지원 영화**의 content_x를
같은 가중치로 평균한1024×131 배열이다. 영화의 인기·평점·추천 하한을 평균 가중치로 쓰지 않는다.
이미 계산된 frozen mean을 그대로 복사하며 다시 단위 정규화하지 않는다. `representatives.norm`,
`qmean`, `medoid`, `multi4`는 v1 대표가 아니다. 배정 중심을 검색 대표로 바꾸지도 않는다.
현재 공급 eligibility가 달라져도 원 mean에서 영화를 빼거나 다시 평균내지 않는다.

사용자 방향은 POLICY §5의 원본 H·실제 별점 수식이다. 그룹 순서는
`sum_j(p_j×mean[g,j]) DESC, group_id ASC`다. 점수 부호로 추가 제외하지 않는다.
이 점수는 GBT 예상 별점이나 대중 best_rank에 더하지 않는다.

## 4. 0벡터와 감상 분모

```text
top_supported = sum(top_x²)>1e−12
sub_supported = sum(sub_x²)>1e−12
group_mapped = ID mapping valid AND top_supported AND sub_supported
```

원 frozen assign은0벡터에도 거리 규칙으로 label을 남겼다. 그 진단 label은 보존하되
`group_mapped=false`이면 v1 발견 공급 목록과 `total_mapped_viewed`/`viewed_g`에 넣지 않는다.
장르가 없고 keyword/overview만 있는 영화도 top_supported=false다. 장르 취향을 추정했다고
표현하지 않는다. 실제 평점·K·H·감상·추천 제외 기록은 그대로 유지한다.
GBT 입력이 지원된다면 개인 맞춤 후보가 될 수 있다. 개인화 가능 여부와 그룹 지원은 별도다.

사용자 content 방향은 POLICY의 지원 sub_x만으로 만들므로 장르0·sub지원 영화도 방향에
기여할 수 있다. 다만 그 영화 자체를 발견 그룹 경험량에 포함하는 것은 위 group_mapped를
통과할 때뿐이다. 전체 감상 분모는 최신 원본10편이 아닌 현재 distinct 확정 감상 전체에서
group_mapped=true인 영화 수다. 삭제·확정 감상 변경은 현재 catalogVersion으로 다시 계산한다.

## 5. 신규 영화·원천 갱신

새 영화는 현재 DB serviceMovieId와 확인한 TMDB ID를 연결하고, 같은 전처리→top 거리→해당
child 거리로 처리한다. 고정 전처리의 transform과 이 문서의 거리 배정만 사용하며 클러스터 재학습은 하지 않는다.
저장 행은 serviceMovieId,tmdbId,catalogVersion,geometryVersion,groupRecipeHash,tasteId,
childId,groupId,topSupported,subSupported,groupMapped,vectorHash,sourceHash를 포함한다.
고정 그룹 수1,024를 만족하지 않는 bundle은 준비 실패다.

기존 영화의 장르/키워드/overview가 바뀌면 새 catalogVersion에서 같은 고정 중심으로 다시
배정하고 이전 버전의 행은 보존한다. 대중평점·관객만 바뀌면 GKT벡터/그룹은 바뀌지 않는다.
영화 추가·비활성화·원천 갱신은 mean/중심/vocabulary/IDF/SVD를 재적합할 이유가 아니다.
새 catalogVersion의 벡터·할당·공급목록·사용자 감상 그룹을 함께 게시하고 버전을 섞지 않는다.
발견 공급·개인 점수·API 조립은 POLICY/CONTRACTS의10×10 및2+1을 따른다.

## 6. 실행 전·배포 전 인수 조건

다음은 아직 실행하지 않은 구현 검수다. 소스 파일 구조를 읽은 감사를 추론 PASS로 바꾸지 않는다.

| ID | 검수 | 통과 조건 |
| --- | --- | --- |
| G01 구성 키 | allowlist와 원 SHA/배열 shape/hash | old final policy/router/하위16 설정 유입0, top8×131·child8×128×131·mean1024×131 |
| G02 원 스냅샷 |237,817영화 전처리·배정 재현 | source ID축 동일, 벡터절대차≤1e−10·top/child/group ID 완전일치. 원진단label과 v1 groupMapped 분리 |
| G03 새로운 입력 | 중복/미지 ID·결측/빈 텍스트·장르0·GKT0 | 고정차원·유한값·support 규칙 준수, missing을 긍정근거로 만들지 않음 |
| G04 수치 경계 | 같은 영화 단건/여러 batch·거리동률·norm 경계 | 배치크기무관 ID 동일, 정확거리동률 낮은ID, eps규칙 동일 |
| G05 대표 | 불균일 norm·평균과 중심이 다른 fixture | raw frozen mean으로 검색, mean재정규화·중심대체 없음 |
| G06 ID·업데이트 | DB ID축변경·새영화·정보수정·대중값만변경 | 명시매핑, 새catalog만 재배정, 대중값만 변하면 벡터/그룹 불변, 원학습배열 해시 불변 |
| G07 서비스 연결 | 장르0/전체감상/그룹공급 fixture | groupMapped false는 발견/감상분모에서 제외하되 K/H/실제감상제외 유지;10공급그룹·각10상한 |

고정 소스에는 당시 연구용 컬럼/로컬 경로가 있다. 배포 manifest는 위 원본 SHA와 실제 export
URI·bytes·SHA·runtime·groupRecipeHash를 함께 기록해야 한다. 파일이 없거나 parity가 실패하면
`GROUP_BUNDLE_NOT_READY`이며 다른 K/가중치/모델로 조용히 대체하지 않는다.

## 7. 상세 유사 영화에서의 벡터 재사용

[SIMILAR-MOVIES](SIMILAR-MOVIES.md)는 이 문서 §2의 content_x를 재사용한다. 영화끼리의
cosine 검색이며 맛·하위 그룹·mean 대표를 검색 범위나 점수로 사용하지 않는다. 유효 sub_x만
필요하므로 groupMapped=false라도 검색 지원이 가능하다. DB float32 검색 사본은 별도 export며
기존 float64 원본·그룹 할당의 정확도 계약을 바꾸지 않는다. 이 기능만을 위해 군집이나 텍스트 모델을 재학습하지 않는다.
