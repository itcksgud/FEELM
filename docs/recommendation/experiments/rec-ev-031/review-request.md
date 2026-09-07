# 독립 검토 요청과 접근 범위

상태: DRAFT — 제품 승인이 아닌 연구 검증이다.

설계와 구현 1차 검토는 각각 `fork_turns=none`인 독립 검토 에이전트가 수행했다.
검토자는 이 작업의 대화나 결론을 상속하지 않고 다음 자료로 판단한다.

- 팀 문서: `C:\higher\projects\S15P21E106`의 AGENTS, 관련 공식 계약. 읽기 전용.
- 연구 문서/코드: `C:\higher\projects\FEELM-standalone`의 AGENTS, 이 폴더와
  `scripts/rec_ev_031_catalog_bridge.py`, 합성 테스트, 연결된 기존 계약/실행기.
- 다른 저장소 문서, 미개봉 Locked Test·reserve·새 정답은 읽지 않는다.
- 같은 worktree는 루트만 수정한다. 검토자는 판정·위치·근거·최소 수정안을 반환한다.

설계 검토 질문: 이 실험이 제품 질문에 답하는가, 모델이 제공되지 않은 정보를 쓰는가,
비교가 공정한가, 이 자료가 답할 수 없는 주장이 섞였는가, 결과별 실제 결정이 있는가.
설계 단계에서는 raw 평점·profile/membership/label payload를 열지 않는다.

구현 검토 질문: 실제 projection/predicate와 UID 선필터, 입력 변환, 동률/배열 인덱스,
고유500 후보와 seen 필터, donor 순환, macro/paired 통계, 미채점 처리, 봉인 의존성과
실행 제한을 확인한다. 합성 테스트만 실행한다. 허가할 경우
`PASS_FOR_EXPLORATORY_EXECUTION`과 code/config/design SHA256을 반환한다.
이를 근거로 루트가 implementation-review.json을 작성한다. 해시가 달라지면 실행이 차단된다.

결과 검토 질문: 봉인·저장된 추천·사용자별 지표·집계/CI를 독립 재계산하고 결론이
사전 기준 및 관측 범위와 맞는지 판단한다. 이번 점수 봉인 후 이미 개봉된 SELECTION R0
라벨의 outer,user_key,target_movie_ids,target_q만 사용할 수 있다. raw 전체 재채점이나
replication·다른 outer·새 표본 개봉은 하지 않는다. 정책 제안도 근거 범위를 검토한다.
