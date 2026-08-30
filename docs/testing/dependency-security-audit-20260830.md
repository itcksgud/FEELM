# Dependency and working-tree secret audit — 2026-08-30

> Status: `LOCAL_CURRENT_TREE_KNOWN_DEPENDENCY_PASS_REVISION_PENDING`

## 결과

| 대상 | 명령/방법 | 결과 |
| --- | --- | --- |
| Root npm lock | `npm audit --json` | 전체 0 (`prod 1`, 전체 dependency 71) |
| React npm lock | `npm audit --prefix frontend --json` | 전체 0 (`prod 12`, 전체 287) |
| Playwright npm lock | `npm audit --prefix e2e --json` | 전체 0 (`prod 1`, 전체 4) |
| Python data lock | `requirements-data.lock`의 version+artifact hash를 `pip-audit 2.9.0`으로 검사 | remediation 후 알려진 취약점 0 |
| Python recommender locks | runtime/test 전이 의존성과 build backend까지 version+artifact hash 고정 후 `pip-audit 2.9.0` 검사 | remediation 후 알려진 취약점 0 |
| Python 현재 환경 | `py -3.12 -m pip check` | broken requirement 0 |
| Java runtime inventory | Gradle `writeRuntimeCycloneDx` | 실제 main runtime 55개 component를 CycloneDX 1.6으로 생성 |
| Java runtime advisory DB | OSV Scanner `v2.5.1` | remediation 후 알려진 취약점 0 |
| Java artifact checksums | Gradle dependency verification metadata | SHA-256 metadata 생성 및 test에서 검증 |
| Gradle wrapper | `distributionSha256Sum` + wrapper JAR SHA-256 | Gradle 8.14.3 binary ZIP과 공식 wrapper JAR 고정 |
| 컨테이너 이미지 | Dockerfile stage와 PostgreSQL·Mailpit image를 포함한 Compose 6개 service | tag+audited manifest digest, Compose `linux/amd64` 고정 |
| GitHub Actions | checkout/setup-node/setup-python/setup-java | major tag 대신 audited 40자리 revision 고정 |
| 작업 트리 비밀값 | `npm run security:secrets:check` | high-confidence finding 0 |
| Git history 비밀값 | Gitleaks 8.29.1 archive checksum + runtime positive control | 현재 HEAD history finding 0, project revision은 pending |
| GitHub Actions 구문 | actionlint 1.7.12 공식 archive checksum | workflow lint PASS |

## Python remediation

첫 `pip-audit`에서 `pyarrow 20.0.0`에 `PYSEC-2026-113 / CVE-2026-25087` 1건이 발견됐다.
`requirements-data.txt`를 `pyarrow 23.0.1`로 올린 뒤 감사 결과는 0건이 됐고, data pipeline 8개와
추천 evidence 37개, Spark protocol 7개 테스트가 통과했다.

Apache Arrow 23.0.1은 IPC file reader 보안 수정이 포함된 공식 patch release다.
[Apache Arrow 23.0.1 release](https://arrow.apache.org/blog/2026/02/16/23.0.1-release/),
[GHSA-rgxp-2hwp-jwgg](https://github.com/advisories/GHSA-rgxp-2hwp-jwgg)

현재 Python 경로는 주로 Parquet이며 Python binding에서 해당 pre-buffer API가 노출되지 않는다는
제한이 있어도, 고정된 취약 버전을 그대로 둘 이유가 없으므로 수정 버전으로 올렸다.

hash lock 도입 후 data, recommender runtime/test, audit/build/lock-generation tool의 여섯 lock을 각각
`pip-audit 2.9.0`으로 다시 검사했고 모두 알려진 취약점 0건이었다.

추천 서비스 감사에서는 `fastapi 0.115.12`가 resolve한 `starlette 0.46.2`에 2026년 advisory 7건이
발견됐다. FastAPI를 `0.140.7`, Starlette를 `1.6.0`으로 함께 pin해 기존 환경에 취약 Starlette가
남지 않게 했고, recommender 63개 테스트와 재감사를 통과했다. FastAPI 0.140.7의 배포 metadata도
Starlette `>=0.46.0` 호환을 선언한다. [FastAPI 0.140.7 PyPI](https://pypi.org/project/fastapi/0.140.7/)
재빌드한 Compose recommender container에서도 실제 import version `0.140.7 / 1.6.0`과 C2A 통합
Gate 전체 PASS를 확인했다.

추천 runtime과 test 환경은 각각 `recommender/requirements.lock`과
`recommender/requirements-test.lock`에 모든 전이 의존성 및 `setuptools` build backend의 artifact
SHA-256까지 기록한다. Docker와 CI는 먼저 `pip install --require-hashes`로 이 환경을 만들고, 로컬
package는 `--no-deps --no-build-isolation`으로 설치해 pyproject 재해석이나 격리 build dependency
다운로드가 실행 경로에 끼어들지 않게 한다. 데이터 실험 환경도 `requirements-data.txt`를 갱신 입력으로
두되 실제 설치는 pinned build tools를 먼저 설치한 뒤 `--no-build-isolation`과
`requirements-data.lock`을 사용한다.

잠금 생성 도구 자체는 `scripts/requirements-lock-tools.lock`으로 `pip-tools 7.6.1`과 전이 의존성,
pip/setuptools/wheel까지 해시 고정한다. 의도적인 의존성 갱신은
`scripts/refresh-python-locks.ps1`로 수행하고 생성 결과 diff와 advisory 검사를 함께 검토한다.
CI의 `pip-audit`와 editable package build 도구도 각각 별도 hash lock을 사용한다. 프론트 컨테이너는
digest-pinned Node 이미지에 포함된 npm을 사용하며 build 중 별도 global npm package를 받지 않는다.

## Java remediation과 스캔 범위

Spring Boot `3.5.5` 기준의 첫 Gradle verification metadata 스캔에서는 Spring Framework, Tomcat,
Jackson, Logback, Micrometer, PostgreSQL JDBC 등 59개 advisory match가 나왔다. 같은 3.5 계열의 마지막
OSS patch인 Spring Boot `3.5.16`으로 올린 뒤 실제 runtime에 남은 세 계열은 다음처럼 수정 버전을
명시했다.

| 계열 | runtime 수정 전 | runtime 수정 후 | 근거 advisory의 최소 수정 버전 |
| --- | --- | --- | --- |
| Jackson Databind | `2.21.4` | `2.21.5` | `2.21.5` |
| Log4j API | `2.24.3` | `2.25.5` | `2.25.5` |
| PostgreSQL JDBC | `42.7.11` | `42.7.12` | `42.7.12` |
| Bouncy Castle provider | `1.81` | `1.85.2` | `1.82` / advisory별 수정 버전 |

후속 전체 검사에서 직접 사용하던 Bouncy Castle provider `1.81`에 GOST CTR keystream reuse
(`GHSA-574f-3g2m-x479`)와 LDAP injection(`GHSA-c3fc-8qff-9hwx`) advisory가 발견됐다. 공식 현재
artifact `bcprov-jdk18on 1.85.2`로 올리고 Gradle verification SHA-256 metadata를 다시 생성했다.
백엔드 102개 테스트, fresh Compose의 회원가입·이메일 인증·로그인·로그아웃 브라우저 흐름과 OSV
재검사를 통과했다. [Bouncy Castle Java downloads](https://www.bouncycastle.org/download/bouncy-castle-java/),
[GHSA-574f-3g2m-x479](https://github.com/advisories/GHSA-574f-3g2m-x479),
[GHSA-c3fc-8qff-9hwx](https://github.com/advisories/GHSA-c3fc-8qff-9hwx)

Gradle task `writeRuntimeCycloneDx`는 `runtimeClasspath`의 실제 resolved artifact만 담은 55-component
CycloneDX 1.6 inventory를 만든다. OSV Scanner v2.5.1로 이 inventory를 다시 스캔한 결과는
`No issues found`, exit code `0`이다. 변경 후 backend test도 통과했다. `--refresh-dependencies`로
verification metadata를 다시 생성한 뒤 캐시 없는 Linux Docker build에서도 strict checksum 검증과
`bootJar`가 통과했고, 새 이미지로 Compose health 및 Playwright 10/10을 확인했다.

전체 `verification-metadata.xml`은 runtime뿐 아니라 Gradle plugin과 test/build 경로, 교체되기 전의
요청 좌표까지 241개 package를 담는다. 그 파일을 그대로 OSV에 넣으면 runtime에는 없는 Jackson
`2.21.4`, Commons Lang `3.16/3.17`, HttpClient `5.5.2`, HttpCore `5.3.6`이 8건으로 남는다. 따라서
“서비스 runtime known CVE 0” 판정은 resolved runtime CycloneDX만을 대상으로 하며, 전체 metadata는
의존 artifact checksum 고정과 build/test inventory로 별도 유지한다. 이 구분은 취약 package를
ignore하지 않고 실행 경계를 명시하기 위한 것이다.

사용한 scanner는 공식 OSV Scanner release `v2.5.1`이며 Windows amd64 binary SHA-256
`25e42f5ef6711fd8c0fb45390972205891dd44c6bd02ac93f0f63e8e98d9bfb6`, CI의 Linux amd64 binary
SHA-256 `f9f25499a2c8cc367b3af45df2ea7eeca7fbccceab9c35079968f4b3652194be`를 고정 검증한다.
[OSV Scanner v2.5.1 release](https://github.com/google/osv-scanner/releases/tag/v2.5.1),
[OSV Java lockfile support](https://google.github.io/osv-scanner/supported-languages-and-lockfiles/),
[Spring Boot 3.5.16 release](https://spring.io/blog/2026/06/25/spring-boot-3-5-16-available-now/)

로컬 재검증은 `npm run security:java:check` 한 명령으로 scanner 다운로드·checksum 확인·runtime SBOM
생성·OSV scan을 순서대로 실행한다. scanner와 scan output은 ignored `.codex-tmp/`에만 둔다.

## Secret scan 범위

검증기는 `git ls-files --cached --others --exclude-standard`로 현재 commit 대상과 untracked source를
함께 열거한다. ignored `.env.local`, `outputs/`, 대용량·binary 파일은 읽지 않는다. private key header,
AWS access key, GitHub token, `sk-` 형식 key, JWT 형태를 찾되 match 값은 출력하지 않는다.

이 검사는 history scan이나 entropy scanner를 대체하지 않는다. 현재 저장소는 revision이 고정되지 않아
현재 untracked 프로젝트를 Git history Gate로 완료 판정할 수 없다. 실제 TMDB token은 계속 ignored
`.env.local`에만 둔다.

별도 `npm run security:history:check`는 Gitleaks 8.29.1 Windows archive SHA-256을 확인하고, 실행마다
임시 high-entropy GitHub 형태 token을 탐지하는 positive control이 성공해야만 현재 Git history를
검사한다. 현재 결과는 `PASS_HISTORY_ONLY_REVISION_PENDING`이다. CI는 checkout `fetch-depth: 0`, Linux
archive checksum, 같은 positive control을 사용한다. 8.30.1은 default rule regression과 Windows archive
checksum 문제가 보고돼 채택하지 않았다.
[Gitleaks v8.29.1 release](https://github.com/gitleaks/gitleaks/releases/tag/v8.29.1),
[v8.30.1 default-rule regression](https://github.com/gitleaks/gitleaks/issues/2170),
[v8.30.1 Windows checksum issue](https://github.com/gitleaks/gitleaks/issues/2164)

`npm run ci:workflow:check`는 actionlint 1.7.12 Windows archive를 공식 SHA-256으로 검증해 로컬에서
실행한다. CI도 Linux archive를 별도 공식 checksum으로 검증하며, 바이너리와 archive는 저장소가 아닌
`RUNNER_TEMP`에 풀어 revision-readiness 검사를 오염시키지 않는다.
[actionlint v1.7.12 release](https://github.com/rhysd/actionlint/releases/tag/v1.7.12)

## 남은 제한

- Java 판정은 OSV가 현재 알고 있는 advisory와 실제 main runtime dependency에 대한 판정이다. Gradle
  plugin/test-only 경로 8건을 서비스 runtime 0건과 혼동하지 않으며, 해당 경로를 실행하는 CI 권한과
  입력은 계속 최소화해야 한다.
- `npm audit`와 `pip-audit` 결과는 2026-08-30 당시 advisory DB snapshot이며 새 advisory가 생기면 달라진다.
- image digest와 GitHub Action revision은 내용을 고정하지만 자동 보안 업데이트를 하지 않는다. 갱신 시
  upstream tag/release를 다시 확인하고 validator의 audited 값과 문서를 함께 변경해야 한다.
- commit이 생긴 뒤 clean checkout에서 secret history scan과 모든 lock/pin 감사를 다시 실행한다.

CI의 `dependency-audit` job은 세 npm lockfile, Python data/runtime/test hash lock,
Java runtime CycloneDX를 매 push/MR 시 현재 advisory DB로 다시 검사한다. OSV binary 자체도 고정
checksum을 확인한다. 새 advisory로 실패하는 것은 재현성 오류가 아니라 의존성 재검토 신호다.
