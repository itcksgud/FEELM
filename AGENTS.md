# FEELM 개인 프로젝트 작업 규칙

## 작업 경계

- 개인 연구 저장소다. 팀 기준 `S15P21E106`의 작업·브랜치·계약과 섞지 않는다.
- 시작·완료 시 실제 브랜치, worktree, `git status --short --branch`를 확인한다. 폴더 이름으로 브랜치를 추측하지 않는다.
- 다른 작업의 미커밋 변경을 보존하고 별도 worktree를 쓴다. 강제 checkout·reset·무단 stash 삭제를 하지 않는다.
- 사용자의 명시 요청 없이 commit·push·배포·외부 계정 변경·Jira/MR 작성을 하지 않는다. 병합은 사람이 한다.
- 저장소 밖 자료는 참고 원본이다. 필요한 계약은 출처·버전을 기록해 이 저장소에 정규화한다.

## 기준과 읽을 범위

우선순위: 현재 사용자 지시 → APPROVED 제품 범위·업무 규칙 → OpenAPI·DB Schema/migration → 화면 계약·인수 기준 → 아키텍처·추천·데이터 계약/승인 ADR → 요구사항 원문·결정 기록 → 연구·발표 자료.
하위 문서·코드로 상위 계약을 바꾸지 않는다. 불일치를 기록하고 상위 기준을 먼저 해결한다.
구현 문서는 `DRAFT`(탐색), `APPROVED`(구현 기준), `SUPERSEDED`(후속 링크로 대체)를 표시한다.
`docs/requirements/00-source.md`와 파생 분석은 최종 계약이 아니다. 확정 계약·준비도는 `docs/planning/llm-autonomous-development-readiness.md`를 따른다.

| 할 일 | 먼저 읽을 문서 |
| --- | --- |
| 현재 연구·선정안 설명 | `docs/recommendation/active-experiment.md`와 질문에 필요한 결과 근거 |
| 새 추천 연구·코드·실행·검토·공유 | `docs/recommendation/research-workflow.md`, `docs/recommendation/README.md`, 현재 범위와 해당 고정 설계 |
| 제품 기능·API·DB·테스트 변경 | `docs/ai-workflow/implementation.md`, `docs/README.md`에서 해당 영역의 계약 |
| 문서만 정리 | 변경 대상, 그 문서가 요약하는 원문, `docs/ai-workflow/writing.md` |
| 실행·재현 명령 확인 | `docs/runbook/local-development.md`에서 해당 절만 |

이미 읽은 동일 버전은 반복 출력하지 않는다. 먼저 파일명·제목을 검색하고 필요한 절을 읽는다.
표의 조건에 해당하는 필수 계약을 생략하지 않으며, 전체 검토를 요청받으면 범위를 좁히지 않는다.

## 연구에서 항상 유지할 의미

- 미평가·결측·OTT 미응답은 싫어요가 아니다. MovieLens 사용자와 서비스 사용자를 구분한다.
- 고정 비교 조건과 사전 판정·중단 기준 없이 모델을 채택하지 않는다. 설계·코드의 실행 전 독립 검토와 결과의 계산·누수·주장 범위 독립 검토를 유지한다.
- 8개 맛·특정 군집 수를 분석에 강제하지 않는다. 원래 복수 속성을 유지하며 최신 범위는 `docs/recommendation/analysis-scope.md`를 따른다.
- 전체 질문에 대한 결론까지 정리한다. 중간 실험 완료를 전체 연구 완료·게시 승인으로 확대하지 않는다.
- 과거 설정·결과·실패·검토 봉인은 보존한다. 요약 문서를 갱신해도 원본 수치를 다시 쓰지 않는다.
- 개인 연구의 선정안은 팀 채택·구현·배포 완료가 아니다. 공통 API·DB·제품 의미의 미정 사항을 임의로 확정하지 않는다.

## 보안·보고

- 실제 토큰은 `.env.local` 또는 환경 secret으로만 주입한다. 로그·문서·fixture·명령 출력에 노출하지 않는다.
- 대용량 모델·원본 데이터·생성 출력은 Git에서 제외하고 필요한 위치·해시·공유 범위를 기록한다.
- 자격증명 없이 구현한 fake/adapter를 실제 연동 완료로 표현하지 않는다. TMDB·MovieLens·JustWatch 라이선스와 attribution을 유지한다.
- 목적·변경 범위·검증 방법을 먼저 알리고, 마지막에는 핵심 결과·검증·남은 제약·파일 위치만 보고한다.
