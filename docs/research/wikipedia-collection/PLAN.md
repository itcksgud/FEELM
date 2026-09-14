# Wikipedia 영화 줄거리 수집

상태: DRAFT — 2026-09-10 사용자 요청에 따른 로컬 데이터 수집. 서비스 계약이나 모델 채택이 아니다.

## 목적과 모집단

기존 비교 대상 85,517편에 Wikipedia의 자세한 줄거리를 연결한다. 기존 TMDB overview와
별도 자료로 보관하고, 아직 임베딩 생성·모델 재학습·성능 판정은 하지 않는다.
이 모집단은 기존 구조화 정보와 TMDB 텍스트의 공통 집합이며 전체 영화나 최신 한국 시장을
대표하지 않는다. 현재 TMDB 텍스트는 영어 fallback을 포함하면 85,517편 모두 존재한다.
29,058편은 한국어 요청 원응답에 overview가 비어 있지 않은 수이지 전체 텍스트 가용량이 아니다.

## 수집과 매칭

1. 기존 REC027 identity와 REC033/045 metadata를 해시 고정하고 movie_id·TMDB ID 일치를 확인한다.
   평점 원자료와 정답·사용자 정보는 읽지 않는다. 수집 순서는 movie_id 해시로 고정한다.
2. Wikidata 공식 SPARQL에서 TMDB 영화 ID(P4947)와 IMDb ID(P345)를 동시에 조회한다.
   두 ID가 서로 다른 QID를 가리키거나 한 ID의 QID가 복수이면 REVIEW_REQUIRED로 격리한다.
   하나의 QID만 있고 다른 ID가 없으면 단일 ID 매칭임을 표시한다. 존재하는 다른 ID가
   로컬 ID와 다르면 격리한다. 제목만으로 자동 매칭하지 않는다.
3. 확인된 QID의 enwiki·kowiki 링크를 각각 수집한다. 영어 우선 선택이나 자동 번역으로
   두 언어를 합치지 않는다. 문서의 pageprops.wikibase_item이 예상 QID와 같아야 사용한다.
   문서 이동은 API redirects로 해석하고, 다른 작품/시리즈/동음이의 문서는 격리한다.
4. MediaWiki Action API의 revisions로 실제 버전의 원문을 일괄 수집한다. 제목, page ID,
   revision ID·시각, URL, 조회 시각, 응답 해시, 언어별 라이선스를 남긴다.
5. Plot / Plot summary / Synopsis / 줄거리 / 시놉시스의 제목과 그 하위 절만 추출한다.
   같은 높이 이상의 다음 절에서 멈춘다. 흥행·평점·비평·배우 목록·서론으로 빈 줄거리를
   채우지 않는다. 줄거리 안의 인용 각주/표/템플릿은 보수적으로 정리하며 원문을 보존한다.
   추출된 내용이 모든 결말을 담는다고 보장하지 않는다. 줄거리 없음은 선호 없음이 아니다.

## 운영과 검증

- 새 외부 의존성 없이 기존 requests/pandas/pyarrow와 표준 라이브러리를 사용한다.
- 단일 요청 흐름, 요청 사이 최소 1초, maxlag=5, gzip, 식별 User-Agent를 사용한다.
  429/503/네트워크 오류에는 Retry-After와 지수 대기를 적용한다. 재시도 한도 후 중단하며
  이를 문서 없음으로 바꾸지 않는다. 성공 응답은 요청별 압축 캐시에 보존해 재실행 시 재사용한다.
- 사전 독립 검토와 합성 검사 후, 먼저 고정 순서 200편의 ID 조회 및 최대 100문서로
  API·매칭·본문 경계를 점검한다. 검증을 통과하면 같은 설정으로 전체 모집단을 처리한다.
- 누락·충돌·문서 없음·줄거리 없음·QID 불일치를 구분한다. 영어/한국어/양쪽 가용량,
  개봉연대·원어·TMDB 투표 수 구간별 수집률과 길이를 보고한다. 평가 점수로 표본을 고르지 않는다.
- 독립 결과 검토자는 원응답의 ID/본문 경계/버전과 집계를 다시 확인한다.
  매칭·수집 성공은 추천 성능이나 2026년 한국 서비스 유효성의 증거가 아니다.
- 코드·설계·소형 집계만 Git 대상이다. 원문·캐시·Parquet은 outputs/에 보존한다.
  이번 요청으로 commit/push/Jira/MR/외부 게시를 수행하지 않는다.

## 근거

- [Wikidata 데이터 접근](https://www.wikidata.org/wiki/Wikidata:Data_access): 공개 식별자 조회,
  CC0 데이터, 요청 제한과 캐시 원칙.
- [MediaWiki API 예절](https://www.mediawiki.org/wiki/API:Etiquette): 요청 묶음·maxlag·재시도.
- [Wikipedia 영화 문서 줄거리](https://en.wikipedia.org/wiki/Wikipedia:Manual_of_Style/Film#Plot):
  줄거리 절과 스포일러를 포함한 사건 요약. 개별 문서의 완결성은 별도 문제다.

실행 명령의 기준은 docs/runbook/local-development.md의 Wikipedia 수집 절이다.

## 소규모 검토에서 수정한 추출기

첫 pilot에서 self-closing 각주가 이후 닫는 각주까지 이어지는 것으로 해석되어 한 문서의
줄거리에 후속 절이 섞였다. 전체 수집 전에 발견했으며 첫 원문·코드·결과는
outputs/recommendation-evidence/wikipedia-plots에 보존한다. v2는 같은 원응답을 해시 확인해
재사용하고 수정된 추출 결과를 별도 wikipedia-plots-v2에 저장한다. 오류가 있던 첫 결과는
모델 입력으로 사용하지 않는다. 수정 코드와 실제 재추출 결과를 다시 독립 검토한다.

v2 전수 ID 조회 완료 후 본문 수집에서 HTTP429가 반복됐다. 모든 Retry-After를 지켰으며
수집을 잠시 중단했다. 2026년 공식 요청 규칙에 맞춰 User-Agent에 실제 공개 연구 저장소
https://github.com/itcksgud/FEELM 주소를 추가한다. 다른 브라우저·사용자로 위장하지 않는다.
Action API 읽기는 GET을 우선하고 인코딩 URL이 7,000자를 넘을 때만 읽기 전용 POST를
사용한다. 같은 action=query의 이전 POST/GET 원응답은 실제 요청 정보를 유지한 채 재사용한다.
v3 출력은 wikipedia-plots-v3에 보존하고 v1/v2 캐시는 해시를 확인해 읽기만 한다.
연결·본문 추출 규칙은 v2와 같으며 모델 사용을 허용하는 변경이 아니다. 대기·재시도 규칙은 유지한다.
근거: [2026년 요청 제한](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits),
[읽기 요청의 GET 권고](https://www.mediawiki.org/wiki/API:Etiquette#POST_requests).
