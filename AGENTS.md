# FEELM standalone agent contract

## 1. Repository scope

- 이 저장소는 개인용 FEELM 독립 프로젝트다. 팀 GitLab 기준 저장소 `S15P21E106`과 작업·브랜치·계약을 섞지 않는다.
- 현재 작업 위치는 별도 worktree와 `project/standalone-feelm` 브랜치다.
- 사용자가 명시적으로 요청하지 않으면 commit, push, 배포, 외부 계정 변경을 하지 않는다.
- 저장소 밖의 파일은 참고 원본으로만 읽는다. 프로젝트가 의존하는 자료는 출처와 버전을 기록한 뒤 저장소 안의 계약 문서로 정규화한다.

## 2. Specification authority

충돌이 있으면 다음 순서로 판단한다.

1. 현재 사용자의 명시적 지시
2. `APPROVED` 상태의 제품 범위·업무 규칙 문서
3. `docs/api/openapi.yaml`과 DB Schema·migration
4. 화면 계약과 acceptance criteria
5. 아키텍처·추천·데이터 계약과 승인된 ADR
6. 요구사항 원문 전사와 결정 기록
7. 연구·비교·발표 문서

하위 문서가 상위 계약과 다르면 코드를 기준으로 계약을 바꾸지 않는다. 불일치를 기록하고 상위 계약을 먼저 수정한다.

## 3. Document status

구현 판단에 쓰는 문서는 머리말에 다음 상태 중 하나를 표시한다.

- `DRAFT`: 탐색용이며 구현 계약이 아니다.
- `APPROVED`: 구현 기준으로 사용할 수 있다.
- `SUPERSEDED`: 새 기준 문서 링크를 남기고 사용하지 않는다.

현재 `docs/requirements/00-source.md`와 그 파생 분석은 요구사항 출처지만 그 자체가 최종 구현 계약은 아니다. 확정 계약 세트와 자율개발 준비도는 `docs/planning/llm-autonomous-development-readiness.md`에서 추적한다.

## 4. Implementation rule

기능 구현 전에 다음 연결이 존재하는지 확인한다.

```text
Requirement ID
→ Screen/state or internal use case
→ API operation
→ Entity/source of truth
→ Acceptance criterion
→ Automated test
```

- 위 연결 중 하나가 없으면 먼저 계약을 보완한다.
- 제품 의미를 바꾸는 미정 사항을 임의로 결정하지 않는다.
- 되돌리기 쉬운 기술 선택은 ADR에 가정·대안·검증 방법을 기록하고 진행할 수 있다.
- 한 작업은 하나의 수직 기능 또는 하나의 인프라 목적만 포함한다.
- API·DB·이벤트·추천 출력 변경은 소비자와 migration 영향을 함께 수정한다.

## 5. Definition of done

완료라고 보고하려면 최소한 다음을 충족한다.

- 연결된 Requirement와 acceptance criterion이 있다.
- 정상·빈 상태·권한 오류·외부 장애 중 관련 상태를 처리한다.
- 단위 또는 계약·통합 테스트가 있으며 로컬 명령으로 통과한다.
- OpenAPI, migration, 예시 데이터와 구현이 일치한다.
- 새 환경 변수는 `.env.example`과 로컬 실행 문서에 설명한다.
- 비밀값, 원본 대용량 데이터, 생성 결과를 commit 대상에 넣지 않는다.
- 관련 문서와 추적 매트릭스를 같은 변경에서 갱신한다.

## 6. Data and recommendation constraints

- MovieLens 사용자는 서비스 사용자와 별개다.
- MovieLens 평점은 상호작용 학습·오프라인 평가에, TMDB는 카탈로그·콘텐츠 특징에 사용한다.
- 미평가는 싫어요가 아니며, 누락 메타데이터와 OTT 미응답은 부정 선호 신호가 아니다.
- 현재 `ml-32m.zip`에는 Tag Genome이 없다. 자유 태그를 Tag Genome으로 취급하지 않는다.
- 한국 OTT 구독 비교는 `KR`의 `flatrate`를 구매·대여와 구분하고 스냅샷 시각을 저장한다.
- 추천 모델은 고정 split·candidate·seed와 기준선 비교 없이 채택하지 않는다.

세부 기준은 다음 문서를 따른다.

- `docs/research/movielens-tmdb-data-audit.md`
- `docs/research/movielens-recommendation-evaluation-design.md`

## 7. Secrets and external services

- 실제 토큰은 `.env.local` 또는 실행 환경 secret으로만 주입한다.
- 로그, 문서, 테스트 fixture, 명령 출력에 토큰을 노출하지 않는다.
- OAuth, 이메일 발송, 배포 계정처럼 사용자의 외부 자격증명이 없으면 로컬 fake와 adapter 경계까지만 구현하고 실제 연동 완료로 표현하지 않는다.
- TMDB, MovieLens, JustWatch의 라이선스와 attribution 조건을 유지한다.

## 8. Verification and handoff

- 로컬 실행·검증 명령은 `docs/runbook/local-development.md`가 생성된 이후 그 문서를 단일 기준으로 사용한다.
- 자율개발 가능 여부는 새 대화·새 작업 환경에서 기존 대화 이력 없이 수행하는 blind handoff로 검증한다.
- 검증 실패를 프롬프트 추가로 숨기지 말고 누락된 계약, fixture, 명령 또는 acceptance criterion으로 환원해 저장소에 반영한다.

