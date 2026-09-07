# 새 설계에 필요한 자료의 준비 상태

상태: DRAFT — 문서·계약·manifest·파일목록 기반 감사. 실데이터 성능 실행 없음.
검색 범위는 FEELM-standalone의 docs/recommendation/contracts, evidence의 문서/manifest,
outputs의 파일명과 해당 계약이 가리킨 경로다. 찾지 못했다는 것은 이 범위에서 확인하지 못했다는 뜻이다.

| 자료/행동 | 현재 확인 | 가능한 일 | 선행 준비 또는 한계 |
| --- | --- | --- | --- |
| 외부8맛의 공식 영화별배정·고정centroid·매핑 | 공식 명세는 있으나 이 검색범위에서 실물 근거 미확인 | 역할/상태 합성 검증과 배정스키마 설계 | 실제 모델·좌표계·외부코드·해시 확보. 목업8장르나 임의클러스터를 확정맛으로 대체 금지 |
| MovieLens 시간순 자료 | global-time-v1의 train/validation/test와019A prefix/window의 존재·manifest 확인 | 허용역할·기간·열의 목록 설계 | 존재만으로 열람허용 아님. Locked Test·혼합역할 접근제한 유지 |
| 현재까지 전체 관측평가 | 019A는 비중립K5/10 선택행,019C는K-prefix만seen | 기존 입력 제한의 근거 확인 | 새맛경험카운트에 필요한 cutoff 이전 중립포함전체관측과선택입력을 분리하는 계약 필요 |
| 평가용 full-history histogram | 026/028B 등에 존재함을 계약으로 확인 | 채점척도 설명 | 전체관측모델입력이나 timestamp 자료로 전용 불가 |
| 기존 개봉된 heldout | 019A/019F role-window 계약과 완료 manifest | 재사용 후보 범위 설계 | 019F는 동일사용자의후행창으로 사용자독립 아님. 새목적의 접근·점수봉인계약 필요 |
| 노출·관심없음·감상확인·실제새로움 | 이 검색범위에서 실제행동로그 근거 미확인 | 합성 상태 계약검증 | MovieLens평점을해당이벤트로가공해실측처럼사용 불가 |
| REC031 profile30/target20 | 이전 진단의 자료와 범위 명확 | 계산/누출 방지 교훈 재사용 | 실제T2/D1품질판정자료로 자동전용하지 않음 |

주요 근거:

- `docs/recommendation/contracts/rec-ev-019a-artifacts.json:17,135`와
  `docs/recommendation/evidence/manifests/rec-ev-019a.json`: 역할별 입력/평가경로·schema·bytes/hash.
- `contracts/rec-ev-019c-validation-artifacts.json:74–75`: K-prefix만시스템seen이며 나머지prefix는접근/seen제외금지.
- `contracts/rec-ev-019f-independent-temporal-routing.json`: Validation bucket50..59만, Locked Test와혼합역할금지.
- `contracts/rec-ev-026-content-cf-alignment-design.json:281`,
  `contracts/rec-ev-028b-label-analysis.json:100–104`: 평가용이력과timestamp접근경계.
- `evidence/REC-DATA-001-temporal-feasibility.md:5`: MovieLens timestamp는평점입력시각이며관람시각이아님.
- `evidence/REC-EV-019P-binary-onboarding-preflight.md:42`: 실제포스터노출이없는대리과제.
- `evidence/REC-EV-008-ui-comparison.md:12`, `evidence/REC-EV-004-exploration-pareto.md:134`:
  실제클릭·만족을측정하지않았으며novelty/diversity는만족정답이아님.

약칭 contracts/evidence는 연구 저장소 docs/recommendation 아래다. 새 실험의 사용자 수,
정답 관측률, 통계 정밀도를 이미 확인했다고 보고하지 않는다. 이번에는 payload를 추출하거나
기존파일의데이터접근권한을확대하지 않았다.

접근기록 예외: 가용성 검토자의 첫 rg 검색에서 하위results 제외glob이 적용되지 않아
Git에추적된기존 `evidence/results/rec-ev-016-user-case-a.json`의 사례값 일부가 우발출력됐다.
연결문서 상태는 COMPLETED_REPRODUCIBLE_CASE_DIAGNOSTIC이다. 해당값은 설계근거로 사용하지
않고 재출력하지 않았으며 이후 .md 및 명시적계약/manifest로 검색을 제한했다. 신규 raw/profile/
label/embedding payload나 미개봉평가셋을 열지 않았다.
