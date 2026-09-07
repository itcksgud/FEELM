# 현재 컴퓨터의 경로와 버전 대응

상태: DRAFT — REC-EV-031 재현 자료. 2026-09-07 확인.

| 역할 | 현재 경로 | 버전/검증 방법 |
| --- | --- | --- |
| 공식 팀 문서 | `C:\higher\projects\S15P21E106` | 로컬 develop `cb280bd1f360947646fc9ca22626ec988198ee2e` |
| 공식 원격 갱신 | 동일 repo의 `origin/develop` | 이번 작업 중 fetch 성공, `2c8e96a9d065ccaf91783344773e5325b38109b1`로 2커밋 전진. 로컬 pull/checkout 없음 |
| 개인 연구 | `C:\higher\projects\FEELM-standalone` | `project/standalone-feelm`, HEAD `17600a8f42b2d07b94d70b5e5eaa086b24642599` + 이번 미커밋 문서/코드 |
| 원시 MovieLens32M | `C:\higher\projects\MM\data\raw\ml-32m.zip` | 기존 manifest에서 발견한 읽기 전용 데이터. 238,950,008bytes와 SHA를 실행 전 확인. 해당 위치의 문서는 읽지 않음 |
| 공통 카탈로그/구조 특징 | 연구 repo의 `outputs/recommendation-evidence/rec-ev-027/screen/prepared/` | 기존 universe.npz와 structured-common.npz SHA 고정. 85,517행 |
| 별점 입력과 prior | 연구 repo의 `outputs/recommendation-evidence/rec-ev-029/prelabel/prepared/` | profile-ratings.parquet의 SELECTION/R0와 calibration-prior.npz. bytes/SHA는 config |
| 고정 membership | 연구 repo의 `outputs/recommendation-evidence/rec-ev-029/cache/membership.parquet` | target union 분리 확인용 ID 메타데이터. raw 평가값 없음 |
| 이미 공개된 정답 | 연구 repo의 `outputs/recommendation-evidence/rec-ev-029/postlabel/selection/labels.parquet` | R0 필요한4열만 score 봉인 후 평가. replication과 미개봉 자료 미사용 |
| 새 산출물 | 연구 repo의 `outputs/recommendation-evidence/rec-ev-031/` | 기존 결과와 분리, 단계별 hash 봉인 |
| 재현 코드 | 연구 repo의 `scripts/rec_ev_031_catalog_bridge.py` | 독립 검토에서 고정한 code SHA는 implementation-review.json |

개별 입력의 bytes/SHA는 config.json, 실제 산출물의 bytes/SHA는 artifacts.json을 기준으로 한다.
과거 실험 계약의 절대경로와 원본 결과는 일괄 수정하지 않았다. 이번 명시적 대응표와 파일 identity로
현재 위치를 확인했다. 공식 문서 확인 범위는 팀 저장소, 연구 자료 확인 범위는 개인 연구 저장소다.

fetch로 추가된 파일은 frontend/docs/screens의 README, common-components,
frontend-structure, screen-spec, wireframe-change-requests 5개다. AGENTS와 기존 docs/api,
docs/adr, docs/requirements에는 변경이 없다. 새 화면 문서의 관련 내용은 별도 읽기 감사로 확인했고,
추가된 노출/응답 수·미관측맛·일치%의 경계는 evidence-audit.md에 반영했다. 실험 설계 변경은 필요하지 않았다.
Jira/MR 상태를 조회하거나 변경한 작업은 아니다.
