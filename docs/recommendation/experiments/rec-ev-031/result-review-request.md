# REC-EV-031 결과 독립 검토 요청

상태: DRAFT — 검토 입력 문서. 결과 검토 판정은 result-review.json에 별도 기록한다.

현재 대화 이력 없이 다음 자료로 독립 판단해 주세요. 파일 수정 없이 판정과 재계산 근거를
반환합니다. 원본 결과를 수정하거나 모델·임계값을 튜닝하지 않습니다.

1. `C:\higher\projects\S15P21E106\AGENTS.md`와
   `C:\higher\projects\FEELM-standalone\AGENTS.md`를 읽습니다. 전자는 읽기 전용입니다.
2. 연구 저장소의 `docs/recommendation/experiments/rec-ev-031` 설계, config,
   구현 검토, evidence-audit와 `scripts/rec_ev_031_catalog_bridge.py`를 확인합니다.
3. `outputs/recommendation-evidence/rec-ev-031`의 완료 봉인과 bytes/SHA 의존성을
   검사합니다. 저장된 rankings, prepared, profiles, user-metrics, metrics, cases를 읽을 수 있습니다.
4. 재채점에 필요한 이미 공개된 정답은 config의 REC029 postlabel/selection/labels.parquet
   한 파일입니다. outer==R0, 열은 outer,user_key,target_movie_ids,target_q만 허용합니다.
   다른 outer·replication·미개봉 reserve·raw 평점은 열지 않습니다.
5. runner의 채점 함수를 그대로 호출하는 데 그치지 말고 독립 계산으로 macro Recall@500,
   상위3의 judged/known-bad 집계와 주요 paired bootstrap 4개를 확인합니다. 사용자 정렬과
   같은 사용자 pairing, NaN 분모, binary IDCG, 고유500·관측 K seen 제거도 확인합니다.
6. 사전 기준 그대로 내려진 결정인지, 선택 재사용·고활동 표본·biased target20·미채점 영화의
   한계를 넘어서는지 확인합니다. 0.5점·외부8종·공식 계약 차이가 정책 제안에 유지되는지 확인합니다.
7. 효과·CI·이득/손해 비율·비용·사례·최종 제안에서 수정이 필요한 점을 반환합니다.
   계산 검토 후 루트가 작성한 comparison.md, insight.md, policy-proposal.md의 해석도 확인합니다.

최종 출력: PASS 또는 수정 필요, 검증한 항목과 실제 수치, 남은 한계, 정확한 지적 위치.
검토 중 별도의 사용자 소유 작업이나 새 실험을 만들지 않습니다.
