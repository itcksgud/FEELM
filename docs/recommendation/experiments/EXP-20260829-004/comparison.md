# EXP-20260829-004 비교

모든 사전 local Gate를 통과했다. 실제 HTTP 경로의 후보≤100 worst p95는 4.1012 ms이고,
별도 비활성 Fold-in+score 코어의 후보 1000 worst p95는 2.1281 ms였다. 두 수치는 서로 다른
경로이므로 합치지 않는다.

결과에 사후 맞춘 SLA 대신 사전 규칙을 적용해 timeout 750 ms, healthy freshness 3000 ms를
local 후보로 선택했다. 네트워크·Spring·DB·container contention 미포함 때문에 운영값 채택은 보류한다.

