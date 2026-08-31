# FEELM 요구사항 분석

Notion의 `요구사항 정의서`를 읽기 전용으로 전사하고, API 설계에 사용할 수 있도록 분해한 문서 모음이다.

## 문서

- [00-source.md](./00-source.md): Notion 원문 구조화 전사본
- [01-decomposition.md](./01-decomposition.md): 행위자, 도메인, 엔티티, 불변식, 상태 흐름 분해
- [02-requirements-matrix.csv](./02-requirements-matrix.csv): 요구사항별 단계·도메인·API 관련성 매트릭스
- [03-api-candidates.md](./03-api-candidates.md): OpenAPI 작성 전 엔드포인트 후보와 내부 작업
- [04-open-questions.md](./04-open-questions.md): 명세 확정 전에 결정해야 할 질문과 원문 불일치
- [05-wireframe-decisions.md](./05-wireframe-decisions.md): 와이어프레임 충돌과 차수별 팀 결정 기록
- [06-additional-requirements.md](./06-additional-requirements.md): 기능 묶음 `AR-01`~`AR-12`와 분리된 원자 요구사항·추가 일자·차수

## 결정 및 변경 문서

기존 문서 목록과 합치지 않고, 팀 합의로 새로 만들었거나 이번 결정에 따라 수정한 문서를 별도로 관리한다.

| 문서 | 구분 | 이번 반영 내용 |
|---|---|---|
| [05-wireframe-decisions.md](./05-wireframe-decisions.md) | 결정 기록 | 와이어프레임 충돌 답변, 확정·잠정 정책, 후속 결정 표 |
| [06-additional-requirements.md](./06-additional-requirements.md) | 추가 요구사항 | 복합 기능을 `AR-01.1` 형식의 원자 요구사항으로 분리하고 추가·확정 날짜·차수 부여 |
| [04-open-questions.md](./04-open-questions.md) | 기존 문서 수정 | 기존 질문 목록과 분리된 확정·잠정 답변표 추가 |
| [01-decomposition.md](./01-decomposition.md) | 기존 문서 수정 | 지연 평가 상태, 파티 초대, 추천 구성, 외부 리포트 공유·다운로드, 공개 설정 반영 |
| [03-api-candidates.md](./03-api-candidates.md) | 기존 문서 수정 | 소셜 로그인, 닉네임, 평가 분리, 파티 초대·수락, 추천 fallback, 리포트 링크·다운로드 API 후보 반영 |
| [02-requirements-matrix.csv](./02-requirements-matrix.csv) | 기존 문서 수정 | 새 `AR-*` 요구사항을 구현 추적 대상으로 추가 |

`00-source.md`는 Notion 원문 전사본이므로 로컬 결정 내용을 합치지 않는다. 기존 `FR-*` 번호도 바꾸지 않으며, 합의로 추가한 기능은 `06-additional-requirements.md`의 `AR-*`로 분리해 추적한다.

## 현재 상태

- 원문 상태: `검토 중`
- 원문 최종 수정일: `2026-08-27`
- 로컬 전사일: `2026-08-27`
- 1차 MVP: 22개 항목 (`FR-01`~`FR-22`)
- 2차 MVP: 5개 항목 (`FR-23`~`FR-27`)
- 확장: 5개 항목 (`FR-28`~`FR-31`, 원문 `FP-32`)
- 비기능: 7개 항목
- 확정 개발 범위: **1차 MVP + 2차 MVP 전체** (총 27개)
- 추가 합의 요구사항: 12개 기능 묶음, 47개 원자 요구사항 (`AR-01.1`~`AR-12.4`, 확정 46개·결정 필요 1개)
- 현재 범위 밖: `FR-28`, `FR-29`, `FR-30`, `FR-31`, 원문 `FP-32`

## 최신 동기화 변경

- `FR-07-2`, `FR-07-3`이 각각 `FR-08`, `FR-09`로 정규화되고 이후 항목이 재번호화됐다.
- AI 탐험 가이드와 캐릭터 인터랙션 항목이 요구사항 정의서에서 제거됐다.
- 탐험 배지는 `FR-29`로 확장 범위에 이동했다.
- 사용자 랭킹은 `FR-28`로 확장 범위에 이동하고 기준 문구가 `팝콘 수` 중심으로 변경됐다.
- Party 관련 표현과 NFR-06의 `그룹` 표현 일부가 정리됐다.
- 2026-08-26 Notion의 1·2차 누적 결정에 평가 전 상태, 온보딩 건너뛰기, 닉네임·이메일 중복 방지, 계정 연결, 파티 종료·차단, 리포트 객체 저장, 비회원 공개 범위가 포함돼 있다. 1차에서 2차로 변경된 상세 이력은 확인되지 않는다.
- 2026-08-27 3차 결정에서 Rating 삭제 시 Frame·Popcorn 삭제와 감상 이력 기반 추천 제외, 소셜 가입·연결 관계, 차단 사용자 평가 숨김, PNG 리포트·단기 URL, 평가 변경 전 추천 결과 유지를 확정했다. 복합 `AR-*`는 47개 원자 요구사항으로 분리했다.

이 단계에서는 API 경로와 스키마를 확정하지 않았다. 현재 핵심 미정 사항은 팝콘 맛 매핑, 추천 3개가 모두 마음에 들지 않을 때의 후속 동작, 반기 리포트 상세 내용이다. 이를 합의한 뒤 1차 MVP와 2차 MVP의 `openapi.yaml`을 작성하는 것이 다음 단계다.
