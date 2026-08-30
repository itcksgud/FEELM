# Local MVP isolated browser E2E

`run-local-mvp-e2e.ps1`은 실행마다 UTC timestamp와 PID를 포함한 고유 Compose project 이름과 고정
host port만 사용한다. 기존 기본 Compose project·container·volume은 수정하거나 삭제하지 않으며,
실행 전후 기본 project volume 목록이 같은지도 증거로 출력한다. fresh 검증에서 생성된 고유 project의
container 또는 volume이 이미 있으면 실행하지 않고 실패한다.

검증 port:

- PostgreSQL `55439`
- backend proxy `58080`
- recommender `58000`
- frontend `55173`
- Mailpit SMTP/UI `51025` / `58025`

backend와 frontend nginx는 한 network namespace에서 loopback으로 연결되어 C3/C5/C6 local guard를
우회하지 않는다. 브라우저는 frontend same-origin `/api`만 사용한다. 테스트는 trace, screenshot,
video를 만들지 않으며 Mailpit verification secret과 share/viewer token을 메모리에서만 처리한다.
동일 flow에서 C6 예상 별점·개인 기준 기대 효용·표본 수가 있는 취향 관측을 확인하되 일반 추천
화면에는 이 값을 노출하지 않는다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File e2e/local-mvp/run-local-mvp-e2e.ps1
```

재현 목적이면 안전한 형식의 project 이름을 명시할 수도 있다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File e2e/local-mvp/run-local-mvp-e2e.ps1 `
  -ProjectName feelm-local-mvp-e2e-manual-001
```

종료 시 container/network는 내리지만 생성한 isolated volume은 삭제하지 않는다. 실패 후에도 다음 실행은
새 project 이름을 사용하므로 fresh 상태로 다시 시작할 수 있고, 이전 volume은 원인 분석용으로 보존된다.
