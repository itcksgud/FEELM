# C5 local 결정 현황

> 상태: `APPROVED_LOCAL_MVP_PROFILE`  
> local 결정: `5/6`; `DN-C5-006`은 명시적 `DEFERRED`  
> 구현 권위: `LOCAL_MVP_ONLY`  
> production 결정: `0/6`

확정 범위와 안전 경계는 [제품 범위](./00-product-scope.md)의 `FIX-C5-*`, `SAFE-C5-*`가 담당한다.
현재 사용자의 “배포는 제외하고 별도 프로젝트를 개발 완성” 지시는 아래 보수적 token에 한해 localhost
구현 권위로 해석한다. 실제 provider credential, 외부 URL, 운영 공개·retention·계정 lifecycle은 승인하지
않는다.

| ID | 주제 | local token | local 의미 | 상태 | production 경계 |
| --- | --- | --- | --- | --- | --- |
| `DN-C5-001` | 반기 report 의미·생성 | `C5_REPORT=CALENDAR_HALF_KST_IMMUTABLE_REVISION_V1` | Asia/Seoul calendar half, +72h watermark, empty/ready/superseded, 실제 period 영화 전체 목록, factual metric만, superseded 400d, C1 source, immutable rollback | `APPROVED_LOCAL_MVP` | 운영 schedule·SLA·retention 재승인 필요 |
| `DN-C5-002` | 다운로드·renderer·retention | `C5_EXPORT=ACCESSIBLE_PDF_ASYNC_24H_V1` | local deterministic async PDF, 3 retries, temp artifact 24h, report revision source | `APPROVED_LOCAL_MVP` | object storage·KMS·renderer sandbox 운영 정책 미승인 |
| `DN-C5-003` | privacy·공개 profile·전체 Film/Popcorn | `C5_PRIVACY=PRIVATE_RESOURCE_OPT_IN_V1` | PRIVATE default, PROFILE/FILM/POPCORN 독립 opt-in, 실제 전체 목록 stable pagination, loopback capability target | `APPROVED_LOCAL_MVP` | discovery·moderation·block·external public URL 미승인; TASTE_COMPARE disabled |
| `DN-C5-004` | report share grant | `C5_SHARE=IMMUTABLE_REPORT_FRAGMENT_EXCHANGE_1CALMONTH_V1` | loopback 한정 immutable report, 256-bit raw-once/hash-only, fragment exchange, calendar 1개월, grant 3개 | `APPROVED_LOCAL_MVP` | 외부 base URL·CDN/log abuse 경계 미승인 |
| `DN-C5-005` | notification 설정 | `C5_NOTIFICATION=IN_APP_PROVIDERLESS_OPT_IN_V1` | provider NONE, in-app WATCH_CONFIRMATION_DUE만, default OFF, source C1 | `APPROVED_LOCAL_MVP` | email/push/SMS/webhook 미승인 |
| `DN-C5-006` | account recovery·change·delete | `C5_ACCOUNT_LIFECYCLE=DEFER_UNTIL_C4_APPROVED` | C5 secret/session mutation 0건 | `DEFERRED` | recovery/change/delete API·data lifecycle·UI 전체 |

## 결정 간 의존성

```text
DN-C5-003 privacy ─┬─> DN-C5-004 public share fields/lifecycle
                  └─> public profile/Film/Popcorn/taste comparison

DN-C5-001 report projection ─> DN-C5-002 export
                            └─> DN-C5-004 shared projection

C4 auth approval ─> DN-C5-005 actor/settings
                 └─> DN-C5-006 recovery/change/delete
```

- privacy decision 없이 public profile/Film/Popcorn/taste compare를 설계하지 않는다.
- report content decision 없이 export나 shared card schema를 설계하지 않는다.
- share security 경계는 고정이지만 공유되는 **field**와 snapshot/live 의미는 privacy/report 결정에 달려 있다.
- account deletion은 share/export revoke와 C1 source retention에 영향을 주므로 독립 soft-delete 한 줄로 끝내지 않는다.

## local-only 불변식

- C5 report는 C1의 viewing/rating/frame/popcorn source만 읽고 만족도·예상 별점·취향 진단을 만들지 않는다.
- public resource는 모두 PRIVATE로 시작하고 resource별 명시적 opt-in 뒤에만 loopback에서 읽힌다.
- 공유 raw token은 최초 1회 응답 외 저장·로그하지 않고 hash만 보존한다.
- PDF는 local temp storage에만 두고 24시간 이내 만료·삭제한다.
- 알림은 in-app projection뿐이며 외부 network/provider 호출은 0건이어야 한다.
- production profile은 public base URL·storage·provider·signing 조건이 없으면 capability를 fail-closed한다.
- `DN-C5-006`과 TASTE_COMPARE는 구현하지 않는다.
