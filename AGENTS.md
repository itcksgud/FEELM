# FEELM 개인 개발·연구 저장소 작업 규칙

## 저장소 역할과 작업 경계

- 이 저장소는 FEELM의 개인 개발·연구 환경이다. 제품 구현, 추천 실험, 재현 코드와 자료를
  함께 보관하지만 팀 저장소 `S15P21E106`의 현재 계약을 대신하지 않는다.
- Jira와 연결된 팀 작업은 현재 Jira 내용과 최신 `S15P21E106`의 계약·코드를 우선한다.
  이 저장소의 과거 서비스 설계나 실험 결과로 팀 계약을 되돌리지 않는다.
- 시작·완료 시 실제 branch, worktree, `git status --short --branch`를 확인한다. 다른 작업의
  미커밋 변경을 보존하고, 동시에 진행하는 업무는 별도 branch/worktree로 분리한다.
- 강제 checkout·reset, 무단 stash 삭제, 다른 작업자의 변경 삭제·덮어쓰기를 하지 않는다.
- commit·push·배포·Jira/MR 작성과 외부 계정 변경은 현재 대화의 사용자 요청 범위에서만
  수행한다. 병합과 운영 활성화는 사람이 결정한다.
- 저장소 밖 원본 데이터와 팀 산출물을 복사해 암묵적 기준으로 만들지 않는다. 필요한 파일은
  출처, revision, bytes, SHA-256, 이용 범위와 공유 상태를 manifest에 기록한다.

## 계약 우선순위와 읽을 범위

충돌할 때는 다음 순서로 판단한다.

1. 현재 사용자가 이 작업에 확정한 지시
2. 현재 Jira와 최신 `S15P21E106`의 팀 계약
3. 이 저장소의 `APPROVED` 제품 범위와 업무 규칙
4. OpenAPI, DB schema/migration, 화면·인수 기준
5. 아키텍처, 추천·데이터 계약과 승인 ADR
6. `DRAFT` 연구·비교·발표 문서와 과거 기록

제품 구현의 승인 범위는 `docs/spec/approved-slices.json`, 전체 준비 상태와 남은 gate는
`docs/planning/project-completion-gates.yaml`을 기준으로 한다.
`docs/planning/llm-autonomous-development-readiness.md`는 인계 검토 자료이며 현재 제품 계약의
단일 기준으로 사용하지 않는다. 문서 상태는 `DRAFT`(탐색), `APPROVED`(구현 기준),
`SUPERSEDED`(후속 문서로 대체)를 구분한다.

| 작업 | 먼저 확인할 자료 |
| --- | --- |
| Jira와 연결된 추천 개발 | 현재 Jira, 최신 `S15P21E106/AGENTS.md`, `pipeline/docs/service-v1/` |
| 현재 추천 방향·선정안 설명 | 이 문서의 ‘현재 추천 개발 방향’, `docs/recommendation/active-experiment.md`의 최신 절과 직접 근거 |
| 새 추천 실험·코드·실행 | `docs/recommendation/research-workflow.md`, `docs/recommendation/README.md`, 해당 고정 설계·config |
| 제품 기능·API·DB·테스트 | `docs/ai-workflow/implementation.md`, `docs/README.md`의 해당 계약 |
| 문서 정리 | 변경 대상과 그 원문, `docs/ai-workflow/writing.md` |
| 실행·재현 | `docs/runbook/local-development.md`의 해당 절 |

`docs/recommendation/active-experiment.md`는 최신 절과 과거 연구 이력을 함께 가진 색인이다.
파일 전체의 오래된 지시를 현재 계약으로 합치지 말고, 질문에 필요한 최신 절과 봉인된 근거만
읽는다.

## 현재 추천 개발 방향

다음은 2026-09-18 기준 GBT·FM 실험과 서비스 연결의 작업 방향이다. 팀에 적용할 때는
S15P21E106-620~623의 현재 내용과 최신 팀 계약을 다시 확인한다.

- 서비스 입력은 사용자의 실제 평가 **0~N개**를 지원한다. 0개는 별도 cold-start·대중성
  경로로 처리하고, 1개 이상은 같은 모델 계열에서 입력 근거가 늘수록 신뢰도와 품질이 어떻게
  변하는지 평가한다. 과거 실험의 ‘최신 10편’을 서비스 필수 입력 개수로 고정하지 않는다.
- 평가량 구간을 0/1/2/3/5/10/20+처럼 나누어 정확도, 순위 품질, 지원률, 다양성, 최악 사용자
  구간과 confidence를 함께 본다. 표본 잡음 때문에 수치를 억지로 단조 증가시키지 않으며,
  평가량 증가 시 반복되는 성능 악화가 있으면 특징·보정·학습 표본 문제로 기록한다.
- GBT와 FM은 공통 원천 데이터와 비교 split을 공유하되, 모델의 장점을 없애는 하나의 표현을
  강제하지 않는다. GBT에는 결측 표시와 안정된 집계·비선형 분할용 수치 특징을, FM에는 희소
  원속성과 사용자 반응의 쌍별 상호작용을 보존하는 표현을 우선 검토한다. 공통 특징 대조와
  모델별 특징의 ablation을 분리해 어떤 정보와 상호작용이 이득을 냈는지 확인한다.
- MovieLens는 오프라인 동작·비교와 분산 파이프라인 검증용 약한 대리 지표다. MovieLens의
  승자를 한국 서비스 사용자 만족도 승자로 표현하지 않는다. 최종 선택은 모델명을 가린 작은
  실제 사용자 평가와 이후 서비스 노출·선택·평가 로그를 근거로 한다.
- KOBIS의 최소 영화 단위 입력은
  `outputs/recommendation-evidence/kobis-expanded-20260913/linked-v2/confirmed-movie-comparison.parquet`
  2,176행이다. 키는 `kobis_movie_code`, 관객 지표는
  `last_observed_cumulative_admissions`다. 72,700행 연도별 관측은 재생성·감사용 상위 원본이며
  고유 영화 수가 아니다.
- 현재 KOBIS↔TMDB↔서비스 확정 연결은 0개다. 제목·날짜·국가 기반 후보는 제안으로 유지하며,
  검증된 crosswalk 전에는 KOBIS 값을 GBT/FM 학습이나 서비스 영화 점수에 붙이지 않는다.
- Discovery 대중성에서 TMDB 투표와 KOBIS 관객 원값을 직접 비교하지 않는다. 출처별 분포·시점·
  결측 의미를 보존하고, 버전이 고정된 출처별 정규화 후 max를 쓰는 안과 두 채널을 따로 쓰는
  안을 같은 후보·평가 조건에서 비교한 뒤 결정한다.
- 모델 학습은 데스크톱과 노트북의 로컬 단일 장비 실행을 기본으로 한다. 두 PC를 임시 Spark
  클러스터로 묶지 않는다. 작은 fixture와 10% 계측으로 시간·RAM·디스크 spill을 확인한 뒤 전체
  실행으로 확대한다.
- 서버는 기본 학습 장소가 아니다. 선택 모델은 model, ordered feature schema, 전처리·보정,
  dependency/runtime, data/split hash와 CPU 추론 parity를 한 bundle로 만들어 서버에 올린다.
  서버·HDFS의 현재 revision, 자원, 권한, 경로와 무결성을 확인하기 전에는 업로드·활성화하지 않는다.

## 연구·구현 규칙

- 미평가·미노출·결측·OTT 미응답을 싫어요나 0점으로 바꾸지 않는다. MovieLens 사용자와 서비스
  사용자를 구분하고, 학습·보정·최종 평가 사용자를 분리한다.
- 모델 비교 전에 질문, 기준선, 입력·후보, split, seed, 지표, 판정·중단 기준과 자원 한도를
  고정한다. 결과를 본 뒤 gate를 유리하게 바꾸지 않는다.
- 8개 맛·특정 군집 수는 과거 기준선이나 후보 공급 설계로 재현할 수 있지만 데이터 관계와
  사용자 취향의 정답으로 강제하지 않는다. 원 장르·키워드·인물 등 복수 속성을 보존한다.
- 실행 전 별도 검토는 새 모델 채택, 큰 비용의 전체 학습, 데이터 누수 위험, 팀 게시·배포처럼
  결과를 되돌리기 어려운 작업에 적용한다. 작은 문서 수정·진단·fixture는 자체 diff와 자동 검증으로
  끝낼 수 있다. 검토 범위는 위험과 비용에 맞춘다.
- 완료 run의 설정·결과·실패·검토 기록은 덮어쓰지 않는다. 새 해석은 요약 문서에서 봉인된
  artifact를 참조하고, 재실행은 새 run ID를 사용한다.
- 각 run은 코드 revision, 데이터·split·candidate hash, feature version, seed, runtime,
  파라미터, 모델, metric과 artifact 위치를 함께 기록한다. 대용량 모델·원본·Parquet은 Git에서
  제외하되 파일을 실제로 전달하지 않았다면 재현 가능하다고 쓰지 않는다.
- 개인 연구의 선정안은 팀 채택·서비스 구현·배포 완료가 아니다. API, DB schema, 공통 타입,
  환경 변수와 인프라 변경은 최신 팀 계약과 영향 범위를 확인한 별도 Jira에서 수행한다.

## 보안·검증·보고

- 실제 토큰은 `.env.local` 또는 실행 환경 secret으로만 주입한다. 로그·문서·fixture·명령 출력에
  노출하지 않는다. `.env.example`에는 이름과 설명만 둔다.
- 자격증명 없이 만든 fake/adapter를 실제 연동 완료로 표현하지 않는다. TMDB, MovieLens,
  KOBIS, JustWatch의 이용 조건과 attribution을 보존한다.
- 변경한 영역의 테스트·정적 검사·재현 명령을 실행하고 diff, 새 파일, 불필요한 대용량 파일과
  secret을 확인한다. 실행하지 못한 검증과 남은 위험을 숨기지 않는다.
- 시작할 때 목적·범위·검증 방법과 공통 계약 위험을 짧게 알린다. 완료 시 변경 파일, 핵심 결과,
  검증, 남은 제약, branch·commit·push 상태와 후속 Jira 필요 여부를 보고한다.
