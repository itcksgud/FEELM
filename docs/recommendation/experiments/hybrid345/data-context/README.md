# 발표용 데이터 배경 — 출처와 집계

상태: DRAFT / LOCAL_ONLY. 2026-09-13 기술통계 집계와 독립 결과 검토 PASS.
모델 학습·예측·선정 및 과거 실험 봉인은 변경하지 않았다.

## 먼저 볼 파일

- [발표 문서](../PRESENTATION-DECISION.md)
- [MovieLens 평가 수 상위 10편](movielens-popular-top10.csv)
- [한국 제작 영화의 MovieLens 평가 수 상위 10편](korean-movielens-top10.csv)
- [2020~2026년 두 카탈로그의 분포](recent-catalog-coverage.csv)
- [같은 영화의 MovieLens·TMDB 평점 비교](recent-same-film-ratings.csv)
- [모집단과 집계 요약](summary.json)
- [동일 영화의 평가 수 배수·상관·분포](count-ratio/count-ratio-summary.csv)
- [평가 수 배수 사례](count-ratio/count-ratio-examples.csv)

평점 평균은 0.5 단위로 반올림하지 않는다. 개별 MovieLens 입력 별점은 원본의
0.5~5.0, 0.5 간격을 검증했으며 표에는 평균을 소수 셋째 자리까지 표시했다.
TMDB 0점/투표 없음/비정상 값은 유효 평균평점 비교에서 제외한다.

## 고정한 원자료와 비교 범위

| 자료 | 범위와 역할 |
| --- | --- |
| MovieLens 32M ZIP | 원본 87,585편·32,000,204평점 전수. `movieId,rating,timestamp`만 읽어 영화별 count/sum/mean 계산. 사용자별 데이터는 새로 저장하지 않음 |
| 서비스 TMDB metadata | 237,817편. 서비스 ID/TMDB ID 고유성 확인. 영화 정보·국가·평점·날짜·연결 상태 열만 읽음. 분류·취향 ID 사용 안 함 |
| MovieLens 연결 | `MATCHED` 86,181편만 일대일 연결. `TMDB_ID_AMBIGUOUS` 35편의 연결을 임의로 고치지 않음 |
| 한국 제작 | `production_country_codes`에 `KR` 포함. 공동제작 포함. 원어·원산국·한국 흥행 기준과 다름 |
| 같은 영화 비교 | 두 서비스 모두 평가가 있는 영화만. 2020년대의 연도는 MovieLens 제목 연도로 양쪽 통일. 영화별 평균에 동일 가중치 |
| 목록 분포 | ML 제목 연도 / TMDB `release_date` 연도. 각각 자기 목록의 편수이며 교집합 비교 아님. 미개봉 상태·성인 등 제품 필터 미적용 |

서비스 스냅샷은 2026-09-09 DB export 기준, 2026-09-12 확보본이다. 원본 수집 manifest에는
MovieLens 기반 요청 87,425편과 2023-10-13~2026-09-07 `primary_release_date` 확장이
기록되어 있다. 전체 요청 239,028편 중 정상 원본 237,817편, 누락 1,211편으로 기록됐다.
수집 완료 manifest 생성 시각은 2026-09-07T14:37:14Z이며 상태는 PARTIAL이다.
이는 TMDB 전체 카탈로그 전수나 영화별 평점 취득 시각이 아니다. 파일명·tar mtime을
실제 평점 관측 시각으로 사용하지 않았다. 원자료의 개봉일·국가 등 사실을 새로 외부 검증한 작업도 아니다.

MovieLens 제목과 TMDB 개봉연도가 모두 있는 연결 중 5,718편은 연도가 다르다.
최근작 표는 이 차이를 숨기거나 한쪽의 연도를 정답으로 덮어쓰지 않는다.
TMDB 평균을 2로 나누는 것은 척도 변환일 뿐, 사람·시점·평점 집계 방법의 차이를 보정하지 않는다.

한국 영화의 2024·2025·2026년 예시를 원자료의 TMDB 투표 수 최대 1편씩 뽑은
[추가 CSV](korean-recent-examples.csv)도 보존했다. 공동제작과 원자료 표기 영향을 받는
자동 선택 목록이며 대표 한국 영화 목록이 아니다. 이 예시들의 개봉 사실·국적 해석을
별도 검증하지 않았으므로 발표 본문은 집계 표와 명시한 MovieLens 상위 작품만 사용한다.

## 재현과 감사

- 코드: `scripts/report_presentation_data.py`
- 실행: 저장소 루트에서 `outputs/recommendation-evidence/text339-runtime/venv/Scripts/python.exe -X utf8 scripts/report_presentation_data.py`
- 새 결과 폴더가 없을 때만 실행된다. 기존 결과가 있으면 중단하며, 재집계는 출력 버전을 분리해야 한다.
- 원본 ZIP: `C:/higher/projects/MM/data/raw/ml-32m.zip`
- 선택한 metadata: `.codex-tmp/fixed-k8-discovery-v2-20260913/outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2/prepare/catalog.parquet`
- 데이터 확보 기록: `.codex-tmp/rec038-korean-movie-guide-20260911/outputs/service-catalog-snapshot-20260909/README.md`
- 영화별 합계·원본 수집 manifest·실행 코드 및 입력/출력 SHA-256: `outputs/recommendation-evidence/presentation-data-20260913/`

독립 사전 검토에서 수집 manifest 멤버명 누락을 수정한 뒤 PASS를 받았다.
실행 코드 SHA-256: `eb67ae5d2826e8df4b3592ddf150a930c09489c0f60bc5e757a31a23cbb97d62`.
실행 중 입력 해시가 변하지 않았고, 영화별 평가 수 87,585개 모두 기존 전수 감사와 일치했다.
독립 결과 검토:

- `outputs/recommendation-evidence/presentation-data-audit-20260913/result-review.json`: **PASS**.
  Arrow CSV·정수 반별점 합산으로 32,000,204행을 재집계해 영화 87,585편의 count/sum/mean,
  CSV 5개의 모든 행과 summary 18필드가 일치했다.
- `outputs/recommendation-evidence/presentation-data-recent-audit-20260913/review.json`: **PASS**.
  독립 dictionary 연결·집계로 최근 영화 관련 105개 값 확인. 최대 오차 4.44e-16.
- `outputs/recommendation-evidence/presentation-data-document-audit-20260913/document-review.json`:
  추가 당시의 본문·표·그림 검토 **PASS**. 이후 평가 수 비율 비교 추가분은 별도 검토 대상으로 구분한다.

과거 [개봉연도 전수 감사](../../../../research/movielens-release-distribution/REPORT.md)의
상위 1%·연대별 집중도 수치는 당시 독립 검산 결과를 인용했다. 이번 원본 전수 평가 수가
그 결과와 일치함을 확인했다. 모델 비교·신뢰구간·선정 상태는 기존 문서의 범위를 유지한다.

## 평가 수 배수 추가 비교

사용자 지적에 따라 두 서비스의 평가 수가 일정한 배수로 차이 나는지 별도로 확인했다.
기존의 서로 다른 목록 분포·평점 평균 비교를 이 질문의 답으로 대신하지 않는다.

- 입력: 앞에서 검산한 영화별 MovieLens 평가 수와 동일 서비스 TMDB metadata.
- 대상: `MATCHED` + MovieLens 평가 수 양수 + TMDB 유효 평점/투표 수. **82,181편**.
- 영화별 비율: `r_i = TMDB vote_count_i / MovieLens rating_count_i`.
- 중앙값·분위수는 각 영화에 동일 가중치. 5~95백분위는 영화별 분포 범위이며 신뢰구간 아님.
- 총합 비율 `sum(TMDB) / sum(ML)`은 ML 평가 수를 가중치로 준 비율 평균이므로 영화별 중앙값과 다름.
- Spearman은 동점에 평균 순위를 부여. 로그 Pearson은 양수 평가 수의 `log10`에 적용.
  둘 다 연관성 지표이며 일정한 배수나 인과관계를 입증하지 않음.
- 전체 중앙값 `4.4`를 공통 기준으로 `[2.2, 8.8]` 범위의 포함률을 전체와 각 집단에서 계산.
  집단 자체 중앙값 기준 포함률은 별도 열로 분리. 2배·4배 허용폭은 읽기용 기술통계이며 통계적 동등성 검정 아님.
- 관측량이 적은 영화에만 생긴 현상인지 점검하기 위해 **양쪽 모두 평가 100개 이상**도 별도 집계.
- 제작국 KR / 제작국이 있으나 KR 미포함 / 제작국 미기재는 상호 배타적으로 분리.
  국가·연대·ML 평가량의 각 분할이 전체 영화를 정확히 한 번씩 포함하는지 검증.
- 연대·최근 연도는 MovieLens 제목 연도. 평가량별 분석은 비율의 분모로 조건을 거는 한계가 있음.
- 사례 ID는 실행 전에 고정. 예시는 대표 표본이 아니며 실제 결론은 전체 분포와 관측량 제한 비교에 근거.

**확인 결과:** 순위 상관 0.781, 비율 중앙값 4.40배, 5~95백분위 0.405~28배.
양쪽 100개 이상 10,702편도 0.143~6.925배로 넓다. 서로 다른 자료에서 관측한 영화별
평가 수를 하나의 배수로 환산하기 어렵다. 시점·노출·참여 집단 가운데 무엇이 얼마만큼
원인인지는 이 비교에서 분리하지 않았다. 새로운 보정계수·모델을 채택하지 않았다.

집계 코드: `scripts/report_presentation_count_ratio.py`.
사전 검토 PASS 코드 SHA-256: `aec9b08466277d27822cc0cd32fa6df4f3a09ea974380da547a2abe77daeef6d`.
입출력 해시·82,181편의 비율은 `outputs/recommendation-evidence/presentation-count-ratio-20260913/`에 보존했다.
초기 그림의 로그 축 마이너스 글꼴 문제는 `scripts/render_presentation_count_ratio.py`로 숫자
눈금만 바꾼 v2 그림을 생성해 해결했다. 집계 결과와 초기 봉인은 그대로 두고 별도 렌더 manifest를 남겼다.

독립 결과 검토 두 건 모두 **PASS**:

- `outputs/recommendation-evidence/presentation-count-ratio-audit-20260913/review.json`:
  dictionary 연결·수동 정렬/보간·동점 평균순위로 21집단의 399값 재계산. 최대 오차 2.84e-14.
- 같은 폴더의 `result-review.json`: index 연결과 별도 동점 평균순위로 82,181편 전수 및
  21개 집계행·11개 예시·집단 분할 재검산. 허용오차 1e-12 이내 일치.
