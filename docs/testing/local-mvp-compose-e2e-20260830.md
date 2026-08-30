# Local MVP fresh Compose browser evidence — 2026-08-30

> 판정: `LOCAL_MVP_COMPOSE_E2E_PASS`  
> 범위: localhost fixture C2B·C3·C4·C5·C6 local experiment  
> production readiness: `NO`

## 실행 결과

| 흐름 | 격리 Compose project | 결과 |
| --- | --- | --- |
| C2B 누적 추천 collection | `feelm-c2b-e2e-20260830020313-30360` | `C2B_REAL_COMPOSE_BROWSER_E2E_PASS` |
| C3 Party/OTT → C4 membership → C5 report/profile/share → C6 interpretation lab | `feelm-local-mvp-e2e-20260830033147-32512` | Playwright `1/1 PASS` (C6 예상 별점·quantized-midrank ECDF v2 상대 효용·취향 근거 경계 포함) |

C2B는 최초 3편, 추가 후 누적 5편, 명시적 관심 없음 제거, viewing-only 유지, rating 완료 제거와
최종 active 3편을 실제 PostgreSQL·Spring·React·recommender 구성에서 확인했다.

C3~C6 흐름은 다음을 한 브라우저 시나리오에서 확인했다.

1. C6 로컬 실험 route의 예상 별점, `C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2` 개인 기준 기대 효용,
   `displayEligible=false`, 자기보고 만족도가 아님을 알리는 경계 문구와 취향 관측 근거 section
2. C3 KR OTT 비교의 실제 전체 영화 navigation과 local Party baseline
3. C4 가입 → Mailpit 인증 link의 fragment 제거 → login → onboarding skip → OTT/profile → logout
4. C5 EMPTY 반기 report → PDF download → privacy opt-in/public profile → fragment share exchange/read →
   revoke 뒤 기존 viewer 404 → providerless notification setting

## 격리·안전 증거

- 각 실행은 새 고유 project와 고정된 별도 loopback port를 사용했다.
- 성공 실행 종료 뒤 해당 project container는 0개였다.
- 격리 PostgreSQL/recommender volume은 사후 분석을 위해 삭제하지 않았다.
- 기본 개발 volume `feelm-standalone_feelm-postgres-data`,
  `feelm-standalone_feelm-recommender-artifacts`는 C3~C6 실행 전후 동일했다.
- browser trace·screenshot·video와 raw signup/share/viewer secret을 artifact로 남기지 않았다.
- `docker compose config --quiet`와 loopback topology test도 통과했다.

## 재현 명령

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-c2b-e2e.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File e2e\local-mvp\run-local-mvp-e2e.ps1
```

이 증거는 현재 working tree의 local 기능 연결을 검증한다. commit SHA에 귀속된 clean-checkout 재현,
production origin/auth/email/object storage/provider 또는 운영 성능을 증명하지 않는다.
