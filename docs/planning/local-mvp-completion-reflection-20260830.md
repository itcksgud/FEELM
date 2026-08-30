# FEELM standalone local-MVP 완성 단계 고찰 — 2026-08-30

> 판정: `LOCAL_MVP_IMPLEMENTED_AWAITING_REVISION_REPRODUCTION`  
> 제품 범위: `APPROVED_LOCAL_MVP`  
> 운영 준비도: `BLOCKED`

## 무엇이 완료됐는가

사용자는 팀 저장소와 분리된 개인 프로젝트에서 보수적인 기능을 먼저 구현하고 검증하라고 명시했다.
이 지시는 [제품 승인 기록](./product-owner-approval-request-20260830.md)의 22개 결정과 4개 교차 Gate가
정한 범위에서 local 제품 승인으로 작동했다. C2B·C3·C4·C5에는 계약, main OpenAPI operation,
PostgreSQL migration, backend·React 구현과 자동화 test source가 존재한다.

| slice | 승인된 local vertical | 명시적 제외 |
| --- | --- | --- |
| C2B | popularity baseline 3편, 누적 append, 평가 완료·명시적 dismiss 이탈 | 개인화, XAI, 제품 예상 별점, 만족도 추론, exposure/action public operation |
| C3 | 2~4명 local party lifecycle, aggregate member 상태, KR OTT 비교와 실제 영화 전체 목록 | public Party 추천 champion, typed-signal weighting, production invitation/auth |
| C4 | email 가입, Mailpit local 인증, login·refresh·logout, profile, onboarding, KR OTT 구독 | OAuth, restart, password recovery/change/delete, production email·key·origin |
| C5 | 반기 factual report, local PDF, PRIVATE opt-in profile, fragment share, in-app notification | account lifecycle, expected star, satisfaction, taste diagnosis/compare, production provider/public URL |
| C6 local experiment | REC-EV-003B 예상 별점, REC-EV-015 quantized-midrank ECDF v2 상대 효용, 표본 수/신뢰도를 드러낸 취향 관측 근거 | 제품 노출, 실사용자 만족도 주장, C2B star head 활성, navigation 노출 |

이 상태는 `기능이 working tree에 구현됨`을 뜻한다. commit, push, deploy 또는 production readiness를
뜻하지 않는다.

## 실제 연결 검증

C2B fresh Compose browser E2E와 C3→C4→C5→C6 isolated Compose browser E2E를 각각 실제 실행해
`LOCAL_MVP_COMPOSE_E2E_PASS`를 확인했다. 실행 project, 확인 범위와 volume 보존 증거는
[local-MVP Compose E2E 증거](../testing/local-mvp-compose-e2e-20260830.md)에 기록했다.

## 아직 닫지 않은 완료 조건

1. 사용자가 검토 가능한 revision 생성을 별도로 승인한다.
2. 새 clean checkout에서 dependency install, contract, backend, frontend, Compose E2E와 security 검증을
   같은 revision으로 재현한다.
3. fixed revision의 dependency audit와 git-history secret scan을 남긴다.

따라서 root 완료 상태는 계속 `IN_PROGRESS`이고 각 local slice는
`IMPLEMENTED_AWAITING_REVISION_REPRODUCTION`이다.

## 운영 경계

- localhost fixture와 Mailpit capture를 실제 운영 identity, email delivery 또는 provider 연동 증거로 쓰지 않는다.
- MovieLens offline 지표와 local Spark 1→2 worker 측정을 실제 사용자 만족도나 multi-host capacity로 바꾸지 않는다.
- C6는 expected-star·relative utility·taste evidence를 로컬에서 비교하는 판단 자료일 뿐이다. REC-EV-015가 개선한 것은 이산 별점 경계에서 숨겨진 실제 평점의 개인 내 위치를 일관되게 복원하는 정도이며, satisfaction을 관측한 것이 아니다. 제품 expected-star, satisfaction/taste diagnosis, Party public champion은 별도 결정 전 활성화하지 않는다.
- operational credential, production origin, external storage/notification, 배포는 별도 사용자 승인 대상이다.
- 이 문서와 제품 승인은 commit·push·MR 권한을 부여하지 않는다.

## Machine evidence

- 상태 기준: `docs/planning/project-completion-gates.yaml`
- Gate validator: `scripts/validate-completion-gates.mjs`
- validator mutation: `scripts/test-completion-gate-validator.mjs`
- isolated Compose E2E PASS: `docs/testing/local-mvp-compose-e2e-20260830.md`
- 전체 재현 명령: `npm run verify:reproduce`

Compose E2E는 working tree에서 통과했다. clean revision evidence가 생기기 전에는 revision 재현 완료나
production readiness를 주장하지 않는다.
