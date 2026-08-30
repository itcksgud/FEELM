# C0 Catalog 자동 검증 지도

> 상태: `APPROVED` — Requirement 추적용 안정 ID
> 승인 확장: `docs/testing/c1-automated-tests.md`, `docs/testing/c1-ac-test-map.csv`  
> Canonical registry: `docs/spec/approved-slices.json`

## 사용자 시나리오

| ID | 시나리오 |
| --- | --- |
| `SCN-CAT-001` | 검색 홈에서 제목·감독·배우로 영화를 찾고 빈 결과를 구분한다. |
| `SCN-CAT-002` | 장르·국가·연도·OTT 필터를 적용하고 URL 상태를 복원한다. |
| `SCN-CAT-003` | 안정 movieId로 상세에 직접 접근하고 locale fallback을 확인한다. |
| `SCN-CAT-004` | KR OTT의 유형·freshness·구독 정렬·외부 링크를 확인한다. |
| `SCN-CAT-005` | versioned 유사 영화와 구조화 이유를 보고 다른 상세로 이동한다. |

## 자동 테스트 묶음

| ID | 상태 | 위치·명령 | 책임 |
| --- | --- | --- | --- |
| `TEST-CONTRACT-CAT-001` | PASS | `npm run contracts:check`, `openapi:lint`, `openapi:mock:check` | 계약 참조, 400 오류, version header |
| `TEST-BE-CAT-001` | PASS | `backend/.../CatalogApiAcceptanceTest.java` | 7 API 정상·빈 상태·validation·auth·fallback |
| `TEST-BE-CAT-002` | PASS | `backend/.../CatalogUnavailableApiTest.java` | DB 장애 503와 traceId |
| `TEST-FE-CAT-001` | PASS | `frontend/src/test/CatalogScreens.test.tsx` | 5개 화면 상태와 API client |
| `TEST-E2E-CAT-001` | PASS | `e2e/tests/catalog.spec.ts` | React→nginx→Spring→PostgreSQL 수직 경로 |
| `TEST-DATA-CAT-001` | PASS | `data-pipeline/tests/test_*.py` | MovieLens/TMDB 정규화와 JSONL v1 |
| `TEST-IMPORT-CAT-001` | PASS | `backend/.../importer/*Test.java` | staging, quality Gate, atomic publish |
| `TEST-PERF-CAT-001` | PASS | `performance/results/latest.md` | 87,585편 API p50/p95/p99 |

상태는 테스트 실행 결과를 뜻한다. `IN_PROGRESS` 항목이 연결된 Requirement는 구현 완료를
선언할 수 없으며 결과가 생기면 이 표와 백로그를 함께 갱신한다.
