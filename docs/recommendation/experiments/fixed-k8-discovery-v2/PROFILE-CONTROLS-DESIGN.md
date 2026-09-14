# 프로필 교환·평점 대응 섞기·방향 반전 보완 진단

상태: DRAFT, 실행 전 독립 검토 대상. 2026-09-13. 기존 모델·선정 조건·실험 출력은 변경하지 않는다.

목적은 개인 프로필이 고정 대표의 순위에 영향을 줄 수 있는지, 평점과 영화의 대응을 실제로 사용하는지를 검증하는 것이다. 추천 만족도나 모델 성능 비교가 아니다. 실제 모델 예측, 미래 평점값 파싱, 새 모델/정책 선정은 수행하지 않는다.

## 고정 범위

- 원본 r2 prepare/cluster/final 봉인과 최종 manifest를 사용한다. 최종 exemplar는 `v1-fixed16:group:mean:B50:Q25`로 확인한다.
- 최종 bundle에서 고정 대표와 그룹 배정을 가져오되, 모델 predictor 모듈·가중치는 불러오지 않는다. 대표 배열이 원본 cluster 배열과 같은지 확인한다. 전체 원본 서비스 카탈로그를 기준으로 하며 append/delete 상태는 적용하지 않는다.
- check 역할의 cap10 사용자 중 원래 프로필이 VALID인 uid 오름차순 첫 20명을 사용한다. E_u가 비어 있어도 표본에서 빼지 않는다. 추가 표본 검색이나 결과에 따른 재표본은 하지 않는다.
- 각 수신자의 원래 전체 viewed 목록으로 그룹별 viewed count와 share를 계산한다. `count<=2`, `share<=0.2`이고 Q-eligible unseen prefix가 하나 이상 있는 그룹으로 E_u를 고정한다. prefix는 Q 순서에서 기시청 영화를 먼저 제외한 뒤 Q25를 취한다.
- 비교하는 네 방향 모두 **같은 수신자의 E_u**만 사용한다. donor의 E_u·시청 목록·후보는 전달하지 않는다.

## 네 가지 조건

1. Original: 현재 고정 signed profile 공식. 원래 capped `original_stars` 전체로 anchor를 계산하고, 매핑되어 벡터 지원이 있는 history/stars로 방향을 만든다.
2. Cyclic donor: 위 20명 중 다음 uid 사용자의 원래 방향. 마지막은 첫 번째다. 수신자의 E_u를 그대로 쓴다.
3. Ratings permutation: `numpy.random.default_rng(913+uid).permutation(n_mapped_history)` 한 번으로 **stars만 재배치**하여 영화–평점 대응을 바꾼다. history 영화·viewed·원래 `original_stars` anchor 배열은 유지한다. 동일한 별점이나 우연한 항등 순열을 피하려고 다시 섞지 않는다.
4. Minus direction: 수신자의 원래 단위 방향에 -1을 곱한다. 원래 ratings를 별도 별점으로 바꾸는 실험이 아니다.

대표 점수는 고정 mean 대표와 방향의 내적이며 동점은 작은 그룹 ID 우선이다. 프로필 상쇄/미지원으로 방향이 유효하지 않은 경우 빈 순위를 반환한다. E_u가 비면 네 조건 모두 빈 순위를 반환한다. 이것을 의미 있는 ‘순위 변화 없음’으로 합산하지 않고 별도 상태로 기록한다.

## 합성 검증

- 2D와 3D에 서로 다른 영화 방향, 서로 다른 별점, 서로 다른 대표를 둔 비퇴화 사례를 만든다. 고정 E_u에서 donor, 확정된 비항등 평점 대응 순열, 부호 반전이 예상 top1 순위를 만든다는 것을 수치로 검증한다.
- 동일 별점에서는 **평점 대응 shuffle**이 방향·순위를 바꾸지 않아야 한다. 동일 별점이 donor 교환이나 부호 반전까지 항상 불변이라는 주장은 하지 않는다.
- 대칭/동일 대표로 모든 점수가 동점인 사례는 그룹 ID 동점 규칙을 따르며 변화가 없어도 정상이다. 방향 벡터가 0으로 상쇄되는 사례와 빈 E_u도 검사한다.
- 원래 anchor 배열이 shuffle 후 변하지 않는지, 원래 context가 수정되지 않는지, donor 방향을 써도 recipient E_u가 그대로인지 확인한다.

## 실제 결과와 중단 조건

20명의 네 조건에 대해 E_u ID/크기, 고정 prefix 크기, 방향 상태/anchor/길이, donor uid, permutation, top1/top5 순서를 기록한다. 각 조건의 그룹별 top1 및 top5 포함 횟수, 비교 가능한 사용자 수, top1 변화/불변, top5 순서 변화/불변과 기준 top5 대비 교집합 비율을 집계한다. 빈 E_u, 동일 평점, 항등/별점값 불변 순열, 상쇄, 방향 불변도 따로 집계한다.

입력·코드·대표·최종 봉인 해시가 바뀌면 중단한다. 실제 순위가 반드시 바뀌어야 한다는 조건은 없다. 변화율·상쇄율을 모델이나 정책을 고르는 기준으로 사용하지 않는다. 이 결과는 원래 실험을 덮어쓰지 않고 own worktree `outputs/dv2-profile-controls-r2`에 저장한다.

실행 전 root의 독립 검토 PASS와 정확한 script/test/design fingerprint, prepare/cluster/final seal pin을 `profile-controls-execution-review.json`으로 기록해야 한다. 기본 호출은 fingerprint만 출력하며 `--run`일 때만 데이터를 연다. prepared JSON의 1,350개 미래 `ratings` 배열은 JSON 파싱 전에 null로 치환한다.
