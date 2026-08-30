# FEELM 요구사항 분석

Notion의 `요구사항 정의서`를 읽기 전용으로 전사하고, API 설계에 사용할 수 있도록 분해한 문서 모음이다.

## 문서

- [00-source.md](./00-source.md): Notion 원문 구조화 전사본
- [01-decomposition.md](./01-decomposition.md): 행위자, 도메인, 엔티티, 불변식, 상태 흐름 분해
- [02-requirements-matrix.csv](./02-requirements-matrix.csv): 요구사항별 단계·도메인·API 관련성 매트릭스
- [03-api-candidates.md](./03-api-candidates.md): OpenAPI 작성 전 엔드포인트 후보와 내부 작업
- [04-open-questions.md](./04-open-questions.md): 명세 확정 전에 결정해야 할 질문과 원문 불일치

## 결정 및 변경 문서

기존 문서 목록과 합치지 않고, 팀 합의로 새로 만들었거나 이번 결정에 따라 수정한 문서를 별도로 관리한다.

| 문서 | 구분 | 이번 반영 내용 |
|---|---|---|
| [05-wireframe-decisions.md](./05-wireframe-decisions.md) | 결정 기록 | 와이어프레임 충돌 답변, 확정·잠정 정책, 후속 결정 표 |
| [04-open-questions.md](./04-open-questions.md) | 기존 문서 수정 | 기존 질문 목록과 분리된 확정·잠정 답변표 추가 |
| [01-decomposition.md](./01-decomposition.md) | 기존 문서 수정 | 지연 평가 상태, 파티 초대, 추천 구성, 외부 리포트 공유·다운로드, 공개 설정 반영 |
| [03-api-candidates.md](./03-api-candidates.md) | 기존 문서 수정 | 소셜 로그인, 닉네임, 평가 분리, 파티 초대·수락, 추천 fallback, 리포트 링크·다운로드 API 후보 반영 |

`00-source.md`는 Notion 원문 전사본이므로 이번 로컬 결정 내용을 합치지 않았다. `02-requirements-matrix.csv`도 원문 요구사항 ID 기준 파생표이므로 신규 ID가 확정되기 전에는 수정하지 않는다.

## 현재 상태

- 원문 상태: `검토 중`
- 원문 최종 수정일: `2026-08-27`
- 로컬 전사일: `2026-08-27`
- 1차 MVP: 22개 항목 (`FR-01`~`FR-22`)
- 2차 MVP: 5개 항목 (`FR-23`~`FR-27`)
- 확장: 5개 항목 (`FR-28`~`FR-31`, 원문 `FP-32`)
- 비기능: 7개 항목
- 확정 개발 범위: **1차 MVP + 2차 MVP 전체** (총 27개)
- 현재 범위 밖: `FR-28`, `FR-29`, `FR-30`, `FR-31`, 원문 `FP-32`

## 최신 동기화 변경

- `FR-07-2`, `FR-07-3`이 각각 `FR-08`, `FR-09`로 정규화되고 이후 항목이 재번호화됐다.
- AI 탐험 가이드와 캐릭터 인터랙션 항목이 요구사항 정의서에서 제거됐다.
- 탐험 배지는 `FR-29`로 확장 범위에 이동했다.
- 사용자 랭킹은 `FR-28`로 확장 범위에 이동하고 기준 문구가 `팝콘 수` 중심으로 변경됐다.
- Party 관련 표현과 NFR-06의 `그룹` 표현 일부가 정리됐다.

## 독립 프로젝트 계약과의 관계

이 디렉터리는 원문 전사와 분석 이력을 보존하는 참고 자료다. 독립 프로젝트의 C0 Catalog
수직 기능은 2026-08-29에 별도 승인 계약으로 정규화되었다. 충돌할 때는 다음 순서를 따른다.

1. 저장소 루트 `AGENTS.md`
2. `docs/spec`, `docs/decisions`, `docs/ui`의 `APPROVED` 계약
3. `docs/api/openapi.yaml`과 `docs/data`
4. 이 디렉터리의 원문·분석 자료

C0의 확정 API는 [OpenAPI 계약](../api/openapi.yaml), 데이터 구조는
[논리 ERD](../data/logical-erd.md), 구현 순서는
[작업 백로그](../tasks/implementation-backlog.yaml)를 기준으로 한다. Catalog 밖의 범위는 기존
원문 상태를 유지하며 별도 수직 기능 계약이 승인되기 전까지 구현 의미를 추측하지 않는다.
