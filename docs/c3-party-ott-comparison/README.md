# C3 Party + KR OTT 비교 계약

> 상태: `APPROVED`  
> 구현 권위: `LOCAL_MVP_ONLY`  
> main OpenAPI 병합: `COMPLETE`; production 배포: `BLOCKED`

## 승인된 수직 기능

1. loopback 서버에서 allowlist fake actor로 Party를 만들고 초대·수락한다. owner 포함 최대 4명이다.
2. Party가 고른 KR `FLATRATE` provider에서 볼 수 있는 실제 C0 영화를 결정적 catalog baseline으로 보여준다.
3. 2~4개 KR `FLATRATE` provider를 비교하고 provider별 실제 영화 전체를 stable cursor로 조회한다.

Party baseline은 `CATALOG_POPULARITY_KR_FLATRATE_V1`이며 provider coverage와 C0 popularity만 사용한다.
REC-EV-005의 Average/Balanced, MovieLens 개인화, 예상 별점, 만족도·공정성 추정은 사용하지 않는다.

## 권위 경계

- local 승인: `DN-C3-001`~`004`
- deferred: `DN-C3-005` 행동 attribution/taste analysis
- 외부 C4A 인증/nickname은 production Gate이며 local fake actor의 선행 조건이 아니다.
- fragment의 11개 operation은 main `docs/api/openapi.yaml`에 C3 prefix schema로 병합됐다.
- React local vertical과 generated schema는 구현됐다. backend 구현과 production 배포는 별도 task다.

## 문서 지도

| 파일 | 역할 |
| --- | --- |
| `decision-needed.md` | local 승인과 production 미결정 |
| `product-decision-packet.md` | evidence 감사, 정책·operation·rollback |
| `00-product-scope.md` | local 포함/제외·scenario·Gate |
| `01-glossary-and-policies.md` | baseline/cursor/fake actor 의미 |
| `02-business-rules.md` | local 불변식과 deferred production 규칙 |
| `03-state-machines.md` | Party·invite·comparison 상태 |
| `api/openapi.fragment.yaml` | local-only public wire 계약 |
| `data/logical-erd.md`, `data/data-dictionary.md` | 물리 source of truth |
| `ui/*` | local React navigation/screen 계약 |
| `testing/*` | fixture와 승인 AC |
| `tasks/implementation-backlog.yaml` | 구현 DAG |
| `traceability/requirements.csv` | requirement→operation→entity→AC→task |

## 검증

```powershell
python -B docs/c3-party-ott-comparison/validate_contract.py
python -B docs/c3-party-ott-comparison/validate_product_decision_packet.py
npx redocly lint docs/c3-party-ott-comparison/api/openapi.fragment.yaml
```
