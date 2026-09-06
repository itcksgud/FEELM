# REC-EV-028A1 label-blind 영화 균형 평가 amendment

> 상태: `PROPOSED_FOR_INDEPENDENT_EXACT_AUDIT`

REC-EV-028A 계약을 통과시킨 뒤 별점 없이 membership만 전수 조사했다. 사용자 수와 고유 영화 수는
충분했지만, 같은 인기 영화가 반복돼 사전에 정한 유효 영화 수 기준을 모든 random fold가 실패했다.
한국 proxy도 유효 영화가 23.1편뿐이었다. 이 상태에서 사용자 수만 믿고 모델을 학습하면 처음 지적한
영화 반복 문제를 다시 만드는 것이므로 별점을 열기 전에 중단했다.

REC-EV-028A1은 모델·지표·통계·Gate를 바꾸지 않는다. 각 사용자의 cold 영화 중 hash 앞 N편을 고르는
부분만 다음의 전역 label-blind 배정으로 바꾼다.

1. 별점 없이 각 cold 영화가 몇 명의 적격 사용자에게 등장하는지 센다.
2. 선택지가 적은 사용자부터 처리한다.
3. 그 사용자의 cold 영화 중 현재까지 덜 배정된 영화, 전체 등장 사용자가 적은 영화, 고정 hash 순서로
   target을 고른다.
4. 선택된 집합의 저장 순서는 기존 target hash 순서로 다시 고정한다.

이 배정은 영화별 평가 기회를 균형 있게 만드는 평가 표본 설계일 뿐 추천 점수나 production 후보 생성에
쓰지 않는다. 모의 계산에서 random fold의 유효 영화 수는 892–1,093편, 한국은 55.0편, 최신은
557.2편으로 기존 기준을 모두 넘었다. 이 수치는 feasibility일 뿐 성능 결과가 아니다.

새 membership과 구현을 hash로 잠그고 모든 track이 기존 coverage 기준을 통과한 뒤에만 profile 별점을
열 수 있다. 하나라도 실패하면 모델 학습 전에 종료한다. Locked Test, final reserve, 후속 모델 reserve는
계속 닫는다.
