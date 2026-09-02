# REC-EV-019B — TMDB 영화 특징 전체 생성 결과

> 상태: `DONE / PASS_FULL_GATES`
> 범위: Base 역할 사용자가 전체 기간에 평가한 영화 69,603편의 넓은 콘텐츠 특성 집합
> 후보 권한: 없음 — 실제 후보는 REC-EV-019A cutoff-safe 집합과 identity allowlist의 교집합
> 제품 반영: 없음 — `APPROVED_C2A_INTERNAL_POPULARITY_ONLY` 유지
> Locked Test 사용: 없음

## 한 줄 결론

MovieLens는 사용자 행동에만 쓰고 TMDB는 영화 정보에만 쓰는 경계를 실제 데이터 파일로 만들었다.
69,603편 전체 실행에서 링크·identity·구조 특징·텍스트 특징 Gate를 모두 통과했다. 이 집합은 영화 정보를
넓게 준비한 범위이지 추천 후보군은 아니다. 실제 Validation 모델 비교 전에는 019A의 시간 안전 후보와
교집합을 사용해야 한다. 이 결과 자체는 콘텐츠 추천이 더 좋다는 성능 증거가 아니다.

## 무엇을 만들었나

![MovieLens 행동과 TMDB 영화 정보의 결합 흐름](../figures/tmdb-feature-build-flow.png)

1. Base 역할 사용자에게 전체 기간 중 한 번이라도 평가된 영화 69,603편을 고정 규칙으로 추렸다.
2. MovieLens `links.csv`의 TMDB ID와 IMDb ID를 실제 TMDB 응답과 다시 대조했다.
3. 확인된 영화에서 장르·감독·배우·키워드·언어·연도·상영시간을 추출했다.
4. 제목과 줄거리 등을 고정 E5 모델로 384차원 embedding으로 만들었다.
5. 정보가 비어 있는 필드는 싫어요로 해석하지 않고 missing mask와 B0 fallback 사유로 남겼다.

TMDB `popularity`, `vote_average`, `vote_count`, watch provider, 매출, 예산은 취향 특징과 embedding 입력에서
제외했다. TMDB는 영화의 설명이고 MovieLens 평점은 사람의 행동이라는 역할을 섞지 않기 위해서다.

`69,603`은 시간 cutoff 뒤에 처음 나타난 영화도 포함한다. 따라서 이 숫자를 “Base Train 후보”라고 부른
기존 표현은 잘못이었다. 실행 코드는 같은 데이터를 유용한 feature superset으로 보존하되, 계약·검증기·보고서는
후보 권한이 없음을 검사하도록 수정했다.

## 전체 결과

![TMDB 전체 특징 coverage와 결측](../figures/tmdb-feature-coverage.png)

| 검사 | 결과 | Gate | 판정 |
| --- | ---: | ---: | --- |
| TMDB 링크 존재 | 69,508 / 69,603 (99.8635%) | 99.8% 이상 | PASS |
| identity 확인 또는 IMDb 복구 | 68,674 / 69,508 (98.8001%) | 98% 이상 | PASS |
| 구조 특징 사용 가능 | 68,201 / 68,674 (99.3112%) | 95% 이상 | PASS |
| 텍스트 특징 사용 가능 | 68,534 / 68,674 (99.7961%) | 95% 이상 | PASS |
| embedding | 384차원, L2 0.99999988~1.00000012 | 오차 0.0001 이내 | PASS |

identity 결과는 다음과 같다.

| 상태 | 영화 수 | 처리 |
| --- | ---: | --- |
| MovieLens↔TMDB 확인 | 68,363 | 사용 |
| IMDb로 단일 영화 복구 | 311 | 사용 |
| IMDb가 TV로 확인됨 | 411 | 격리 |
| TMDB에서 찾지 못함 | 357 | 격리 |
| ID 불일치·모호함 | 161 | 검토 대상으로 격리 |

확인된 68,674편의 필드별 결측은 키워드 16,772편(24.42%), 배우 2,336편(3.40%), 장르 465편(0.68%),
상영시간 363편(0.53%), 감독 306편(0.45%), 줄거리 140편(0.20%), 개봉연도 19편(0.03%)이었다. 제목과
원어는 비어 있지 않았다. 특히 키워드가 없는 영화가 많으므로 키워드 하나에 의존하는 모델은 만들지 않고,
구조·텍스트 모델별 fallback과 missingness 구간 결과를 반드시 따로 봐야 한다.

## 실행 중 발견한 문제와 수정

### 인증 형식을 잘못 가정한 문제

첫 100편 호출은 환경 변수 값을 JWT라고 가정해 모두 401이었다. 실제 값은 TMDB v3 API key였다. 비밀값을
출력하지 않고 형식만 판별해 JWT는 Bearer, v3 key는 `api_key` query로 전송하도록 고쳤다. 인증값은 cache,
manifest, 로그 어디에도 저장하지 않는다.

### 0% 결과도 통과하던 검증기

초기 검증기는 파일 schema만 확인해 identity 0%도 통과할 수 있었다. 사전검사와 전체검사에 각각 coverage
하한을 직접 검사하도록 바꿨다. 실패한 호출이 있어도 “파일 생성 성공”을 “데이터 품질 성공”으로 부르지 않는다.

### embedding 재시작 비용

첫 전체 embedding 실행은 93.2%에서 세션이 끊겼고, 당시에는 완성된 batch를 다시 사용할 수 없었다.
메모리맵 checkpoint와 모델·입력 hash signature를 추가해 batch마다 저장하도록 고쳤다. 텍스트 길이순 stable
batching은 영화 순서를 복원하며, 100편 비교에서 기존 방식과 최대 절대 차이 `0.0`을 확인했다.

## 시간·재현성

- 최초 전체 TMDB 수집: 약 2시간 6분
- raw response cache: 112,580개, 약 2.21 GiB
- 최종 재실행: cache hit 112,614회, network request 0회
- embedding model: `intfloat/multilingual-e5-small`
- model revision: `614241f622f53c4eeff9890bdc4f31cfecc418b3`
- ONNX SHA-256: `ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665`
- MovieLens SHA-256: `e4a68655d7386b8f95f2f2424b2ff975dfdd15ffd59e0d864a14dca43e99d6ee`

대용량 cache와 Parquet은 Git에 넣지 않는다. 추적 manifest가 각 결과 파일의 크기와 SHA-256을 보존한다.

## 이 결과가 여는 것과 열지 않는 것

| 이제 할 수 있는 것 | 아직 할 수 없는 주장 |
| --- | --- |
| 019A 최종 후보의 영화 특징 조회 | 콘텐츠 추천이 인기도보다 좋음 |
| 019C 계약에 따른 runner·합성 preflight 구현 | 신작·한국영화 추천 성능이 검증됨 |
| cold-item Validation 파일럿 준비 | 실제 FEELM 사용자가 만족함 |
| 결측 구간별 fallback·성능 비교 | 개인화 champion 승인 |

REC-EV-019A 결과와 합치면 `42,123편 cutoff-safe 1차 후보 ∩ 019B identity allowlist = 41,625편`이다.
REC-EV-019C 실행 계약은 이 동일한 후보를 모든 모델이 사용하도록 고정하고 자동 검사를 통과했다. 다음은
runner·합성 preflight다. cold-item은 `REC-EV-021P` 사전검사를 통과해 별도 Validation 파일럿을 준비할 수
있다. 두 경로 모두 실제 Validation 승인, Locked Test와 제품 정책 변경은 아직 금지된다.

## 재현 명령

```powershell
py -3 -m pip install --require-hashes -r requirements-ml.lock
py -3 -m unittest scripts/tests/test_build_rec_ev_019b_features.py
py -3 scripts/build_rec_ev_019b_features.py --contract docs/recommendation/contracts/rec-ev-019b-artifacts.json --resume
py -3 scripts/verify_rec_ev_019b_features.py --manifest docs/recommendation/evidence/manifests/rec-ev-019b.json
```

실제 TMDB 호출에는 `.env.local`의 `TMDB_READ_ACCESS_TOKEN`이 필요하다. 캐시가 그대로 있으면 마지막 두
명령은 인증값을 저장하지 않고 network request 없이 결과와 checksum을 재검증한다.
