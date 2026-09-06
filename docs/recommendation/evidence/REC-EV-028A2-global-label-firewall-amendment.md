# REC-EV-028A2 전역 정답 방화벽 수정안

> 상태: `PROPOSED_FOR_INDEPENDENT_EXACT_AUDIT`
>
> 평점·타임스탬프·future reserve membership 접근: 없음

## 왜 다시 수정하는가

REC-EV-028A1은 목표 영화 노출 집중을 해결했지만, 일곱 outer를 한 번에 봉인하는 계약과
프로필 선택 규칙 사이에 교차 누출이 있었다. 동일한 사용자·영화가 한 outer의 target이고 다른
outer의 profile인 경우가 67,505쌍이었다. 이대로 profile 평점을 읽으면 전역 점수 봉인 전에
target 평점을 읽게 된다.

## 수정 규칙

1. A1의 균형 target을 평점 없이 그대로 재생성한다.
2. 사용자별 일곱 outer target 합집합을 만든다.
3. 각 outer의 warm 이력에서 그 합집합 전체를 제외한 뒤 기존 해시 순서로 profile 12편을 고른다.
4. 12편을 만들 수 없는 user-outer 행만 제거하며 target은 다시 배분하지 않는다.
5. 남은 행으로 donor와 coverage를 다시 계산한다.

## 평점 없는 가능성 확인

| outer | 사용자 | 유효 target 영화 | 기준 |
|---|---:|---:|---:|
| R0 | 3,035 | 903.49 | 500 |
| R1 | 2,776 | 1,098.00 | 500 |
| R2 | 2,772 | 975.20 | 500 |
| R3 | 3,030 | 994.35 | 500 |
| R4 | 2,667 | 955.87 | 500 |
| KR | 248 | 55.03 | 50 |
| RECENT | 361 | 557.23 | 100 |

모든 사용자의 전체 profile 합집합과 target 합집합 교집합은 0이었다. 이는 실험 결과가 아니라
수정 규칙을 적용할 수 있다는 사전 확인일 뿐이다.

## 함께 강화하는 것

- protocol lock에서 출력 경로·모든 방화벽 flag·계약 및 전이 의존 구현 해시를 확인한다.
- membership 자체에서 7개 outer, cardinality, donor, coverage와 전역 교집합 0을 재계산한다.
- `--resume`은 파일 존재만 보지 않고 status, canonical outer/seed/system/cell, 행 수와 key 유일성을
  재귀적으로 확인한다.

모델, K/입력 정책, 지표, 동시추론, 판정 기준은 바꾸지 않는다.
