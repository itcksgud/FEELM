# REC-EV-031 산출물 안내

상태: 완료한 탐색 진단. **제품 정책 제안은 철회됨**. 공식 제품은 변경하지 않았다.

현재 작업 기준은 [요구사항 분류와 재설계](../../plans/service-policy-redesign/README.md)다.
이 실험은 알려진 선호 영화의 후보 회수 진단으로 한정한다. 일반 Top3를 제품의 맞춤2편+
발견1편 대신 사용한 정책 연결은 [정정 기록](POLICY-RETRACTION.md)에 따라 철회했다.

- [추천 정책 제안](policy-proposal.md): 신규부터 누적 사용자까지 입력·후보·목록·fallback·외부8종 연결.
- [실험 결과와 한계](comparison.md), [고정 사례](case-examples.md), [기존 결론의 유지/변경](insight.md).
- [기존 근거 감사](evidence-audit.md), [가설과 미결정 사항](hypotheses.md), [현재 경로/버전](path-map.md).
- [고정 설계](README.md), [설정](config.json), [검토 요청](review-request.md), [검토 기록](review-log.md).
- [구현 검토](implementation-review.json), [결과 검토 요청](result-review-request.md), [결과 검토](result-review.json).
- [재현 방법](reproduce.md), [실행 기록](run.yaml), [전체 지표](metrics.json), [파일 identity](artifacts.json).

원본 추천·사용자별 지표·봉인은 연구 저장소의 `outputs/recommendation-evidence/rec-ev-031`에 있다.
기존 selection을 재사용했으며 미개봉 Test/reserve는 열지 않았다. 실행 후 기준 변경·추가 표본·
새 모델 튜닝 없이 결과를 보고했다. 다음 작업의 우선순위는 위 요구사항 재분류와 슬롯별 설계를 따른다.
