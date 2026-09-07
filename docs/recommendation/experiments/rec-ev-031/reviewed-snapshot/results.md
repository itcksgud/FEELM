# REC-EV-031 산출물 안내

상태: DRAFT — 완료한 탐색 연구이며 공식 제품 정책은 변경하지 않았다.

현재 입력과 후보 조건에서는 인기도 후보를 유지한다. 구조 콘텐츠 단독과 이번250+250 결합은
알려진 높은 선호 영화의 회수가 두 인기도 기준선보다 낮아 기각한다. 개인 신호는 있지만
상위3 대부분이 미채점이라 실제 만족도나 모든 콘텐츠 활용의 실패를 뜻하지 않는다.

- [추천 정책 제안](policy-proposal.md): 신규부터 누적 사용자까지 입력·후보·목록·fallback·외부8종 연결.
- [실험 결과와 한계](comparison.md), [고정 사례](case-examples.md), [기존 결론의 유지/변경](insight.md).
- [기존 근거 감사](evidence-audit.md), [가설과 미결정 사항](hypotheses.md), [현재 경로/버전](path-map.md).
- [고정 설계](README.md), [설정](config.json), [검토 요청](review-request.md), [검토 기록](review-log.md).
- [구현 검토](implementation-review.json), [결과 검토 요청](result-review-request.md), [결과 검토](result-review.json).
- [재현 방법](reproduce.md), [실행 기록](run.yaml), [전체 지표](metrics.json), [파일 identity](artifacts.json).

원본 추천·사용자별 지표·봉인은 연구 저장소의 `outputs/recommendation-evidence/rec-ev-031`에 있다.
기존 selection을 재사용했으며 미개봉 Test/reserve는 열지 않았다. 실행 후 기준 변경·추가 표본·
새 모델 튜닝 없이 결과를 보고했다. 다음 우선순위는 고정 후보 안의 개인화 재정렬 설계 한 가지다.
