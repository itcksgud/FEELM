# 기능 구현·검증 규칙

기능·API·DB·테스트를 변경하기 전에 읽는다. 경로는 저장소 루트 기준이다. 기존 AGENTS의 구현 규칙을 옮겼으며 계약 의미는 같다.

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

## 8. Verification and handoff

- 로컬 실행·검증 명령은 `docs/runbook/local-development.md`가 생성된 이후 그 문서를 단일 기준으로 사용한다.
- 자율개발 가능 여부는 새 대화·새 작업 환경에서 기존 대화 이력 없이 수행하는 blind handoff로 검증한다.
- 검증 실패를 프롬프트 추가로 숨기지 말고 누락된 계약, fixture, 명령 또는 acceptance criterion으로 환원해 저장소에 반영한다.
