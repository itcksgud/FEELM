# REC-EV-013 Constrained 2+1

Status: `COMPLETED_FULL_CATALOG_EVIDENCE` — 제품·공개 정책 승인 아님

REC-EV-011 K10 offline candidate(alpha 0.2), base manifest SHA-256
`91a2ffd067cc17ffdb0c084b59a92c98677ca5302ccf8b321aee13983a0f02d8`, protocol
`d877db7b8e6ad6e5e6e035930478d3aada44633891e5b27513bbffe4971d71b9`를 base로 잠갔다.
상위 2개는 고정하고 세 번째 한 자리만 natural Top-500에서 교체했다. positive는 주입하지 않았고
unknown genre contribution은 0이다.

Selection 1,230명에서 relevance floor 0.8/0.9/0.95, rank 25/100/500, discovery weight
0.25/0.5/0.75, NDCG@3 loss budget 1%/3%/5%를 비교했다. 가장 relevance 손실이 작은 진단 후보
`floor=.8, rank=100, weight=.75`도 NDCG@3가 0.002846→0.002033으로 28.57% 하락해 모든
budget을 실패했다. 따라서 selection lock은 `selected: null`이다.

Lock 검증 뒤 evaluation 1,323명을 한 번 열었다. 실패 진단 후보 NDCG@3는 base 0.002088 대비
0.000954였고 paired difference는 -0.001134, 95% CI `[-0.002646, 0.0]`였다. 후보의 diversity는
0.720711, list/pair genre coverage는 1.0이었지만 relevance Gate 실패를 뒤집지 않는다.
REC-EV-004B Explore05도 Popularity 대비 약 45.5% NDCG 손실이 있었으므로 discovery reranking의
반례로 유지한다. `two_plus_one`, `discovery_policy`, product approval은 모두 `null/false`다.

Selection runtime 18.961초, evaluation 9.003초. tracked bounded result에는 raw ID가 없다.
