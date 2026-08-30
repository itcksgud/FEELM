# EXP-20260829-005 통찰

- 개인 rating-style 정규화는 raw 평균과 다른 영화 순서를 만들 수 있지만 그 자체가 파티 공정성을
  보장하지 않는다.
- Balanced의 평균·최저 효용·격차 개선 여부는 held-out paired CI로 판단해야 하며 이번 결과는
  모두 불확실하다.
- 4인 공통평가 coverage 약 1%는 심각한 observation bias이므로 공통평가 진단만으로 제품 정책을
  선택할 수 없다.
- MovieLens에는 공동 선택·감상·만족 관측이 없으므로 `party_aggregation` champion을 두지 않는다.
