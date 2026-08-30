# C5 local acceptance

`AC-C5-001`~`AC-C5-020`의 authoritative Given/When/Then은
[local 실행 계약](../local-contract.md#6-acceptance-gate)에 있다.

테스트 층:

1. PostgreSQL integration: immutable report/provenance, privacy default/revoke, hash-only grants, notification dedupe.
2. Controller/API: owner/cross-owner/public oracle, cursor binding, idempotency, response forbidden-field scan.
3. Local artifact: PDF `%PDF` signature, text extraction, sha256, owner-only download, 24h expiry/cleanup.
4. React: fragment 선제 제거, raw-once UX, full pagination, accessible loading/empty/error.
5. Compose browser: C4 login→report→privacy→share/revoke→notification→logout.
6. Negative production: local kill switch OFF, external adapter/network call 0, account/taste route 0.

