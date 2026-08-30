# 승인 제품 Slice 권위

> 상태: `APPROVED` — C0 Catalog base + C1 Rating·Film extension  
> Canonical registry: `docs/spec/approved-slices.json`

공개 제품 구현 계약은 registry의 `publicProductSlices`에 있는 항목만 승인된 것으로 본다.
현재 공개 승인 범위는 다음 두 slice의 합성이다.

1. `C0_CATALOG`: `docs/spec`, `docs/ui`, `docs/data`, `docs/testing`,
   `docs/traceability`에 있는 Catalog 기반 계약
2. `C1_RATING_FILM`: 안정 링크를 유지하는 `docs/c1-draft`의 승인 확장 계약

C1 확장은 C0 파일을 복제하거나 대체하지 않는다. C0 문서의 “C0에서 제외” 문구는 Catalog slice
단독 경계를 뜻하며, registry에서 승인된 C1 기능을 전체 제품에서 제외한다는 뜻이 아니다. 동일 개념이
충돌하면 공통 C0 정의를 기반으로 삼고, C1 Rating·Film 범위에서는 C1 확장의 더 구체적인 규칙을 적용한다.

`docs/c2-recommendation`은 registry의 `internalSlices`에 별도 상태로 기록한다. 현재 C2A 내부
Popularity-only 계약은 공개 API·화면·예상 별점 제품 승인을 의미하지 않으며 C0+C1 공개 제품 권위에
합성하지 않는다.

## 영역별 Canonical 문서

| 영역 | C0 base | C1 extension |
| --- | --- | --- |
| 제품·규칙·상태 | `docs/spec/00-product-scope.md`~`03-state-machines.md` | `docs/c1-draft/00-product-scope.md`~`03-state-machines.md` |
| 화면·내비게이션 | `docs/ui/` | `docs/c1-draft/ui/` |
| 데이터·ERD | `docs/data/` | `docs/c1-draft/data/` |
| Acceptance·fixture | `docs/testing/` | `docs/c1-draft/testing/` |
| 추적성 | `docs/traceability/requirements.csv` | `docs/c1-draft/traceability/requirements.csv` |
| 공개 API | `docs/api/openapi.yaml` | 같은 공개 API의 C1 operation 의미는 `docs/c1-draft/api/openapi.fragment.yaml`로 추적 |

경로 이름 `c1-draft`는 기존 링크 안정성 때문에 유지할 뿐 문서 상태를 뜻하지 않는다. C1의 실제 상태는
registry와 각 문서의 `APPROVED` 머리말이 함께 결정한다.
