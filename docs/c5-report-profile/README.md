# C5 Report·Profile·Sharing·Notification·Settings 결정 인벤토리

> 상태: `APPROVED_LOCAL_MVP_PROFILE`  
> 결정 패킷: `LOCAL_PROFILE_SELECTED`  
> local 결정: `5/6`, account lifecycle `DEFERRED`  
> 구현 권위: `LOCAL_MVP_ONLY`  
> production·외부 공개 권위: `NO`

## 목적

C5는 `FR-23`~`FR-25`와 추가 합의된 프로필·알림 범위를 localhost에서 검증할 수 있는 보수적
수직 기능으로 구현한다. 사실 기반 반기 report, PRIVATE 기본 privacy, 특정 report revision 공유,
provider 없는 in-app 알림만 local 구현 권위를 가진다. 실제 메일·push·외부 공개 URL·계정 lifecycle,
예상 별점·만족도·취향 진단·taste comparison은 이 승인에 포함되지 않는다.

## 권위

충돌 시 다음 순서로 읽는다.

1. 현재 사용자 지시
2. `docs/requirements/00-source.md`, `01-decomposition.md`, `03-api-candidates.md`,
   `04-open-questions.md`, `05-wireframe-decisions.md`
3. `docs/planning/standalone-project-plan.md`
4. 승인된 C0 Catalog와 C1 Rating·Film 계약
5. 승인된 C4 local actor/session 계약과 C2B local baseline 경계

API 후보 문서는 endpoint 이름의 참고 자료일 뿐 request/response나 제품 의미를 승인하지 않는다.

## 기존 Source of Truth 재사용

| 경계 | 재사용 대상 | C5가 만들지 않는 것 |
| --- | --- | --- |
| C0 Catalog `APPROVED` | 공개 가능한 `UI_READY` 영화 identity·card·metadata | 영화·OTT 사본, TMDB/MovieLens ID를 공개 ID로 쓰는 모델 |
| C1 Rating·Film `APPROVED` | ViewingRecord, active Rating, Frame, Popcorn, Flavor/Taste raw aggregate | 두 번째 Rating/Film/Popcorn 원천, 대표 영화만으로 축약한 Film |
| C4 Membership `APPROVED_LOCAL_PROFILE` | local service actor, nickname/profile, auth/session 경계 | recovery/change/delete 또는 production identity 구현 |
| C2B Recommendation `C2B_LOCAL_BASELINE_DISCOVERY` | versioned delivery/action/completion 경계 | 예상 별점·개인화·발견 성공·만족을 리포트 지표로 사용 |

Film은 C1의 **전체 active Frame 모음**이다. 공개 Film도 권한 필터 뒤의 실제 전체 목록을 안정적인
페이지네이션으로 제공해야 하며 “대표 영화 몇 편”을 Film이라고 부르지 않는다. Popcorn Bucket의
`totalCount`도 전체 active Popcorn 수이고 Film count와 같은 불변식을 유지한다. 반기 리포트 안의 기간
부분집합은 `periodItems` 같은 별도 snapshot 의미이며 Film으로 재정의하지 않는다.

## 문서

| 파일 | 역할 |
| --- | --- |
| `00-product-scope.md` | 확정 범위·고정 안전 경계·명시적 제외 |
| `decision-needed.md` | `DN-C5-001`~`006` local 결정과 production 미결정 경계 |
| `product-decision-packet.md` | 선택된 exact token·수치·source·rollback·DEFER 경계 |
| `local-contract.md` | operation·entity·규칙·상태·화면·AC의 실행 권위 |
| `api/openapi.fragment.yaml` | 19개 local wire 계약 |
| `data/*`, `ui/*`, `testing/*` | ERD/data ownership·screen·fixture·Acceptance |
| `tasks/*`, `traceability/*` | 구현 DAG와 requirement→API→entity→AC→task 추적 |
| `validate_local_contract.py` | local authority·operation·AC·trace·금지 capability drift 방지 |
| `validate_inventory.py` | 과거 draft inventory 감사 보존용; 현재 Gate에서는 실행하지 않음 |

## local 구현 순서

```text
APPROVED_LOCAL_MVP_PROFILE
→ scope/rules/state/screen 동시 작성
→ OpenAPI + ERD/data ownership + Acceptance/trace/task 동시 작성
→ 독립 privacy/security 검토
→ C4 local actor/session 뒤 구현
```

계약 묶음이 일치하기 전에는 backend/frontend를 먼저 만들지 않는다. local 구현은 loopback·kill switch·
production fail-closed를 유지해야 하며 외부 provider 호출 증거가 없어야 한다.

## 검증

```powershell
npm run c5:contracts:check
npm run security:secrets:check
```

기존 inventory validator는 draft 패킷 보존 감사용이다. 현재 Gate는 local contract validator와 Redocly를
실행한다. 어떤 PASS도 production readiness나 외부 공개 승인을 뜻하지 않는다.
