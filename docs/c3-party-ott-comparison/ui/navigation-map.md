# C3 Local React Navigation Map

> 상태: `APPROVED` — router 구현은 후속 task

```text
/dev/actors → local actor 선택
  ├ /me/ott-comparisons/new → /me/ott-comparisons/:comparisonId
  │                           └ /providers/:providerId/movies
  └ /me/parties → /me/parties/new → /parties/:partyId
                                      ├ /invitations
                                      └ /baseline-recommendations

/me/party-invitations → accept → /parties/:partyId
```

| Screen | route | local actor |
| --- | --- | --- |
| `SCR-C3-001` | `/me/ott-comparisons/new` | required |
| `SCR-C3-002` | `/me/ott-comparisons/:comparisonId` | owner only |
| `SCR-C3-003` | `/me/ott-comparisons/:comparisonId/providers/:providerId/movies` | owner only |
| `SCR-C3-004` | `/me/parties` | owner/accepted member |
| `SCR-C3-005` | `/me/parties/new`, `/parties/:partyId/invitations` | owner |
| `SCR-C3-006` | `/me/party-invitations` | recipient |
| `SCR-C3-008` | `/parties/:partyId/baseline-recommendations` | owner/accepted member |

`SCR-C3-007` taste analysis와 production login route는 없다. Header는 local dev control이 설정한 actor UUID를
API client에 주입하며 URL/query/localStorage에 actor ID를 싣지 않는다.

