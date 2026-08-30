# FEELM 승인 제품 범위

> 상태: `APPROVED` — C0 Catalog base + C1 Rating·Film extension  
> 승인 공개 제품 Slice: C0 Catalog + C1 Rating·Film  
> Canonical registry: `docs/spec/approved-slices.json`  
> 기준일: 2026-08-29

## 1. 제품 목표

FEELM은 특정 OTT에 귀속되지 않는 사용자의 영화 평가·취향을 축적하고, 사용자가 좋아할 가능성이
있는 실제 영화와 한국에서 확인 가능한 OTT 시청 옵션을 함께 탐색하도록 돕는 영화 전용 서비스다.

전체 핵심 루프는 다음과 같다.

```text
영화 탐색
→ 영화 상세와 예상 선호·추천 근거 확인
→ OTT 시청 옵션 확인
→ 감상 확인과 평가
→ 필름·팝콘·취향 갱신
→ 다음 개인·발견·파티 추천
```

현재 승인된 공개 제품 범위는 **C0 Catalog**와 **C1 Rating·Film**의 합성이다. C0는 탐색 기반,
C1은 `docs/c1-draft`에 안정 경로로 유지되는 평가·기록 확장이다. 디렉터리 이름의 `draft`는 상태가
아니며 실제 승인은 `docs/spec/approved-slices.json`과 각 문서 머리말로 판정한다.

`docs/c2-recommendation`의 C2A는 내부 Popularity-only 계약 상태를 별도로 유지한다. 공개 추천 API,
예상 별점, 추천 UI가 승인됐다는 뜻이 아니므로 이 공개 제품 범위에 합성하지 않는다.

## 2. 대상 사용자

| Actor | 승인 Slice에서 할 수 있는 일 |
| --- | --- |
| 비회원 | 영화 검색·필터, 영화 상세, 유사 영화, 한국 OTT 시청 옵션 조회 |
| 로그인 사용자 | 비회원 기능 + 구독 OTT 우선 정렬, C1 감상 확인·정수 1~5 평가·Film·Frame·Popcorn·raw Taste 조회 |
| Catalog ingestion job | MovieLens/TMDB 식별자 검증, 영화·현지화·인물·OTT 스냅샷 적재 |

비회원 응답에 개인 정보나 개인 예상 별점을 포함하지 않는다. 로그인 사용자의 개인 예상 별점은
Recommendation 수직 기능에서 계약하며 C0 구현 범위가 아니다.

## 3. C0 Catalog 포함 범위

| Capability | Requirement | 완료 결과 |
| --- | --- | --- |
| 제목·감독·배우 검색 | FR-14 | 한글·영문·원제와 인물명으로 영화 목록 조회 |
| 장르·국가·개봉연도·OTT 필터 | FR-15 | 카테고리 간 AND, 같은 카테고리 안 OR 필터 |
| 영화 상세 | FR-08 일부 | 표시 제목·줄거리·포스터·개봉일·러닝타임·장르·감독·출연진 |
| 유사 영화 | FR-11 | UI 표시 가능한 영화만 콘텐츠 유사도 순으로 조회 |
| 한국 OTT 옵션 | FR-16 | `flatrate/rent/buy/free/ads` 분리, 구독 우선 정렬 |
| 외부 데이터 장애 격리 | NFR-05 | 사용자 요청 중 TMDB 호출 없이 마지막 정상 Catalog 조회 |
| 검색·상세 성능 | NFR-01 | 87,585편 기준 정량 목표와 성능 테스트 조건 |
| 공개 API 안전성 | NFR-07 | 비회원 응답에 사용자 정보가 없고 optional 인증만 사용 |

## 4. C0 화면

| Screen ID | 최종 목업 대응 | 역할 |
| --- | --- | --- |
| `SCR-CAT-001` | `1a ⑩ 검색 홈` | 검색어 입력, 최근 검색어, 인기 영화 진입 |
| `SCR-CAT-002` | `1a ⑩-1 영화 검색 결과` | 결과 목록, 정렬·필터, 상세 이동 |
| `SCR-CAT-003` | 목업 없음 | 필터 bottom sheet 또는 별도 화면 |
| `SCR-CAT-004` | `1a ⑥-2 영화 상세페이지` | 영화 정보, OTT 옵션, 유사 영화 |
| `SCR-CAT-005` | 상세 화면 내 영역 | OTT 옵션 상태와 제공 유형 표시 |

화면별 상태와 문구는 `docs/ui/screen-contracts.md`가 기준이다. 20MB 목업 HTML은 시각 참고이며
데이터 의미의 최종 계약이 아니다.

## 5. C1 Rating·Film 승인 확장

C1의 제품 의미는 다음 안정 경로가 canonical이다.

- 범위·정책·규칙·상태: `docs/c1-draft/00-product-scope.md`~`03-state-machines.md`
- 화면: `docs/c1-draft/ui/`
- 데이터: `docs/c1-draft/data/`
- Acceptance·fixture: `docs/c1-draft/testing/`
- 추적성: `docs/c1-draft/traceability/requirements.csv`

C1은 OTT 이동 전 WatchIntent 기록, 감상 여부 확인, 지연 평가, 정수 1~5 Rating lifecycle,
Film·Frame·Popcorn·raw Taste projection과 안전한 행동 기록을 포함한다. 상세 범위를 이 파일에
복제하지 않고 extension을 직접 참조해 계약 drift를 막는다.

## 6. C0 Slice 단독 명시적 제외

아래 목록은 C0 Catalog 단독 구현의 경계다. registry에서 승인된 C1 기능을 전체 제품에서 제외한다는
뜻이 아니며, WatchIntent·감상 확인·평가는 C1 extension에서 승인된다.

- 회원가입·로그인 구현과 OAuth 실제 연동
- 온보딩 OTT 구독 저장 API
- 개인 예상 별점과 추천 설명 계산
- OTT 링크 클릭 기록, WatchIntent, 감상 확인
- 별점·한줄평 작성과 다른 사용자 한줄평
- AI 영화 요약
- 가격·요금제·OTT 가격 대비 가치
- 시리즈·TV 콘텐츠
- 실시간 TMDB proxy와 provider 딥링크 보장
- Elasticsearch, Kafka, Spark, FastAPI

로그인 사용자 구독 우선 정렬은 optional bearer claim에서 제공되는 사용자 식별자를 사용할 수 있는
계약만 정의한다. C0 blind handoff에서는 고정 fake subscription adapter로 검증한다.

## 7. C0 품질 목표

| 항목 | 목표 | 측정 조건 |
| --- | ---: | --- |
| 검색 p95 | 300ms 이하 | 로컬 PostgreSQL, 87,585편, 20개 반환, 200회 warm request |
| 상세 p95 | 200ms 이하 | localization·credit·최신 availability 포함 |
| 목록 결정성 | 100% | 같은 catalogVersion·query·filter·cursor는 같은 순서 |
| 외부 장애 격리 | 100% | TMDB 차단 중에도 마지막 정상 Catalog 검색·상세 성공 |
| 타입 정합성 | TV 노출 0건 | `mediaType=MOVIE`, identity verified만 Catalog 후보 |
| OTT 의미 정합성 | 오류 0건 | 구독·구매·대여를 서로 다른 유형으로 표시 |

성능 목표는 로컬 구현의 초기 Gate다. 운영 인프라가 정해지면 별도 ADR로 재검토한다.

## 8. C0 완료 조건

C0 계약은 다음이 모두 있을 때 구현 가능 상태다.

- 업무 규칙 `BR-CAT-*`에 해석이 필요한 미해결 표현이 없다.
- 5개 화면·영역의 정상, 로딩, 빈 상태, 오류 상태가 정의되어 있다.
- `docs/api/openapi.yaml`의 모든 operation이 acceptance criterion과 연결된다.
- 영화·현지화·인물·OTT 스냅샷의 저장 원천과 불변식이 ERD에 있다.
- 외부 API 없이 실행할 fixture와 fake adapter 요구가 있다.
- `TASK-CAT-*` backlog 한 건만 받아도 새 LLM이 다음 작업을 찾을 수 있다.
- 독립 blind handoff 시험 A가 85점 이상이다.
