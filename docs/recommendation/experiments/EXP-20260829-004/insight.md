# EXP-20260829-004 통찰

- 현재 C2A HTTP latency는 Rating K별 Fold-in 성능이 아니라 Popularity serialization과 후보 수의 영향이다.
- 계산 코어가 빠르다는 사실은 expected-star 품질이나 개인화 ranking 채택 근거가 아니다.
- 전체 result checksum은 실행 시각과 관측값 때문에 바뀐다. 동일한 조건 비교에는 별도 protocol hash를 쓴다.
- local margin이 커도 운영 hop을 포함하지 않았으므로 750/3000 ms는 배포 전 재검증할 기술 후보이다.

