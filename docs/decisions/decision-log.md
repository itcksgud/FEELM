# FEELM 독립 프로젝트 결정 기록

> 상태: `APPROVED` — 아래 개별 결정 상태를 따른다.  
> 팀 GitLab 합의가 아니라 개인 standalone 구현 기준이다.

## Catalog 결정

| ID | 상태 | 결정 | 이유·결과 |
| --- | --- | --- | --- |
| `DEC-CAT-001` | APPROVED | 검색·상세·유사·OTT 조회를 비회원에게 공개한다. | 첫 사용 전 영화 탐색 가치를 제공하고 Catalog blind test를 인증과 분리한다. |
| `DEC-CAT-002` | APPROVED | 공개 resource ID는 UUID, 외부 ID는 별도 식별자로 저장한다. | MovieLens/TMDB 교체와 stale ID 복구가 URL을 바꾸지 않게 한다. |
| `DEC-CAT-003` | APPROVED | 사용자 요청은 로컬 PostgreSQL read model만 조회한다. | TMDB 장애·rate limit을 사용자 경로에서 격리한다. |
| `DEC-CAT-004` | APPROVED | C0는 영화만 다루고 TMDB TV 결과를 제외한다. | 현재 제품 범위와 MovieLens type mismatch 감사 결과를 반영한다. |
| `DEC-CAT-005` | APPROVED | 텍스트 fallback은 한국어→영어→원문이다. | 무작위 표본의 낮은 한국어 충족률과 높은 영어 충족률을 함께 처리한다. |
| `DEC-CAT-006` | APPROVED | 검색은 제목·감독·배우의 정규화 부분 일치를 지원하고 초성·오타 보정은 제외한다. | 첫 구현의 검증 가능성을 유지하고 검색 고도화를 별도 실험으로 둔다. |
| `DEC-CAT-007` | APPROVED | filter category 간 AND, category 내 OR를 사용한다. | 사용자가 여러 장르·OTT를 고를 때 예측 가능한 규칙을 제공한다. |
| `DEC-CAT-008` | APPROVED | 외부 평점, FEELM 평균, 개인 예상 별점은 출처·척도와 함께 분리한다. | 최종 목업의 10점·5점 혼용을 제거한다. |
| `DEC-CAT-009` | APPROVED | OTT region은 KR, 유형은 flatrate/rent/buy/free/ads로 분리한다. | 구독형과 구매·대여를 혼동하지 않는다. |
| `DEC-CAT-010` | APPROVED | TMDB availability URL은 aggregator로 표시하고 직접 재생 링크로 부르지 않는다. | TMDB API가 provider별 완전한 deep link를 보장하지 않는다. |
| `DEC-CAT-011` | APPROVED | OTT 성공 snapshot은 24시간 fresh, 7일까지 stale serve한다. | 데이터 변동성과 외부 장애 격리를 함께 처리하는 초기 운영값이다. |
| `DEC-CAT-012` | APPROVED | `NONE_LISTED`와 `UNKNOWN`을 분리한다. | 데이터 없음과 실제 미등록을 구분한다. |
| `DEC-CAT-013` | APPROVED | 검색 홈의 ‘많이 찾는 영화’는 C0에서 ‘인기 영화’로 수정한다. | 검색 이벤트 기반 trend는 FR-27이며 C0에 집계 원천이 없다. |
| `DEC-CAT-014` | APPROVED | AI 요약과 한줄평은 상세 화면 C0에서 숨긴다. | 각각 후순위 기능이며 TMDB overview·Catalog 계약과 구분한다. |
| `DEC-CAT-015` | APPROVED | 공개 movie identity와 versioned Catalog projection을 분리한다. | 수집 갱신·rollback 중에도 같은 영화의 UUID와 URL을 유지한다. |

## 재검토 조건

- 실제 국내 provider direct link를 합법적·안정적으로 제공하는 source가 확정되면 `DEC-CAT-010`을 재검토한다.
- 운영 중 provider 변동 측정 결과가 나오면 `DEC-CAT-011` TTL을 ADR과 부하 시험으로 조정한다.
- 검색 실패 query 로그와 정답 fixture가 쌓이면 `DEC-CAT-006`에 초성·오타 보정을 추가한다.
- 인증 수직 기능이 완성되면 optional authentication의 구독 우선 정렬을 실제 사용자 DB로 교체한다.
