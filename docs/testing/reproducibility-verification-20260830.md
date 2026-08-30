# Working-tree reproducibility verification — 2026-08-30

> 판정: `LOCAL_WORKING_TREE_PASS_REVISION_PENDING`  
> 범위: 승인된 C0~C5 localhost MVP, C6 local experiment와 계약/결정 validator. production readiness를 뜻하지 않는다.

## 재현성 경계

- Docker/Compose 외부 이미지 7개는 tag와 manifest SHA-256을 함께 고정했다.
- Compose 실행 architecture는 `linux/amd64`로 고정했다.
- GitHub Actions 4종 19개 사용 위치는 audited 40자리 revision으로 고정했다.
- CI runner는 `ubuntu-24.04`, Python은 `3.12.5`, Temurin은 `17.0.20+8`, Node는 `22.14.0`이다.
- Gradle 8.14.3 binary ZIP과 공식 wrapper JAR을 각각 SHA-256으로 검증한다.
- Python data, recommender runtime/test, audit/build/lock 도구의 6개 lock은 모든 package artifact
  SHA-256을 포함한다.
- React/npm은 lockfile integrity를 사용하며 frontend Docker build는 digest-pinned Node 이미지에
  포함된 npm 10.9.2만 실행한다.

이 경계는 package registry 서명이나 image publisher 서명을 검증한다는 뜻이 아니다. 고정한 digest,
checksum, lock artifact가 같은지를 검증하는 로컬·CI 재현성 경계다.

## 실행 결과

| 검증 | 결과 |
| --- | --- |
| `npm run verify:reproduce` | 현재 revision readiness가 `BLOCKED_REVISION_REQUIRED`이므로 최종 확장본에는 미실행. 사용자가 revision을 승인한 뒤 clean checkout에서 실행할 Gate |
| `npm run ci:workflow:check` | actionlint 1.7.12 공식 archive checksum 검증 + GitHub Actions workflow lint PASS |
| `npm run supply-chain:check` | PASS — image 7, action 4종, Python lock 6, Gradle ZIP+wrapper |
| `npm run security:history:check` | Gitleaks 8.29.1 checksum·positive control·현재 HEAD history PASS, project revision pending |
| 완료 Gate validator | 9 gate canonical PASS, 증거/승인/revision/authority/C6 제품 확장·v2 근거 누락 mutant 13개 거부 |
| Python 6개 lock `pip-audit 2.9.0` | 알려진 취약점 0 |
| data lock clean venv | pipeline 8/8 PASS |
| recommender test lock clean venv | 73/73 PASS |
| data lock 기반 recommendation evidence | protocol 42+7, REC-EV-004/004B/006/007/008/011/013/015 PASS |
| recommender Linux Docker hash install/build | PASS |
| frontend Node 22.14/npm 10.9.2 Docker build | PASS, npm audit 0 |
| backend digest-pinned clean Docker build | strict Gradle verification과 `bootJar` PASS |
| `npm run verify` | C0~C6 계약/결정, secret high-confidence finding 0, Java runtime OSV 55 packages/0 issues, backend 115, React 59, data 8, recommender 73 PASS |
| C2B isolated Compose E2E | `C2B_REAL_COMPOSE_BROWSER_E2E_PASS` — 최초 3편, 누적 추가, 관심 없음/평가 완료 제거와 기존 추천 유지 검증 |
| C3→C4→C5→C6 isolated Compose E2E | fresh image build와 실제 PostgreSQL·Spring·React·Mailpit·recommender에서 Playwright 1/1 PASS |
| C6 production bundle boundary | 일반 `frontend build`의 HTML/JS/CSS 3개에 local experiment route·UI copy 없음; local flag build만 별도 chunk 포함 |
| YAML/Compose/diff 검증 | workflow와 completion gate parse, `docker compose config`, `git diff --check` PASS |

최종 C2B와 C3→C6 E2E는 고유 Compose project와 loopback port를 사용해 기본 개발 container·volume을
건드리지 않았다. 실행 중 postgres, recommender, backend, frontend와 Mailpit health를 확인했고 성공 뒤
container/network를 정리했다. 상세 실행 ID와 검증 흐름은
`docs/testing/local-mvp-compose-e2e-20260830.md`에 기록했다.

## 아직 완료로 판정하지 않는 이유

1. 현재 산출물 대부분은 untracked working tree다. 사용자 승인 없이 commit하지 않으므로 revision
   SHA를 기준으로 한 clean checkout CI, history secret scan, blind handoff를 아직 수행할 수 없다.
2. C2A production topology/auth, 운영 email/OAuth credential, 배포, multi-host 성능 주장은 별도 범위다.
3. C6에서 예상 별점·개인 상대 효용·취향 관측 근거를 local-only 판단 자료로만 열었다. REC-EV-015의
   MovieLens Validation에서 quantized-midrank ECDF v2가 K1·K3·K5·K10·K20 모두 사전 Gate를 통과해
   로컬 실험 계산 정책으로만 채택됐으며, C1 정수 척도나 실사용자 만족도 근거로 확대하지 않았다.
   제품 노출·실사용자 만족도 주장, party public champion, OAuth, 실제 provider/object storage는
   의도적으로 활성화하지 않았다.

제품 승인 입력은 `docs/planning/product-owner-approval-request-20260830.md`를 단일 기준으로 사용했고,
22개 권장안과 4개 교차 Gate의 localhost 범위만 기록했다. commit·push·MR·배포 권한은 그 제품 승인에
포함되지 않는다.

`npm run revision:readiness:check`는 이 차단 상태를 성공/실패와 분리해 JSON으로 보고한다. 현재 HEAD에는
필수 프로젝트 경로 13개가 하나도 포함되지 않았으므로 `BLOCKED_REVISION_REQUIRED`이며, CI에서는 같은
검사를 enforcing mode로 실행해 revision 밖 working-tree 증거를 허용하지 않는다.
