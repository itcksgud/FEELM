# 발견 추천 v2 요구·검증 연결

상태: DRAFT — 로컬 연구 검증표. 실제 결과 PASS 여부는 독립 결과 검토와 `FINAL-DECISION.md`를 따른다.

| 사용자가 판단할 내용 | 구현·실행 근거 | 실제 검증 자료 |
|---|---|---|
| 최상위8맛 ID/이름/기준 유지 | `dv2_prepare.py`, 원본 bundle hash | prepare 전237,817행 비교; cluster/final 독립 검산 |
| 맛당128·256까지 세분화 | `dv2_cluster.py`, 고정6개K/48fit | cluster fits/groups/representative diagnostics; 모든K 보존 |
| 작은 그룹도 실제 비교 | min100 제외규칙 없음 | 회원 수/후보0/unsupported 별도 집계 |
| 실제 사용자 벡터만 개인화로 계산 | `dv2_common.profile`, 원본cap별점과 매핑벡터 분리 | profile states; cap0/30; tiny·cancel·unmapped 합성 반례 |
| 덜 본 하위 그룹을 먼저 한정 | `dv2_retrieve.Engine.state` | viewed 전수 count/share 및 E_u와 candidate 원본 재구성 |
| 이후 개인 취향으로 그룹 정렬 | mean/norm/medoid/multi4 | same hierarchy/space, source dot count, user swap/sign reverse |
| 평가 수 보정 강화 | 고정 C/m300, m100/300/1000 비교 | Q 전행/정렬 검산; 최종Top1/노출 민감도 |
| 후보 품질과 개인 예상 별점 구분 | Q prefix 후 same selected predictor | 동일후보 원래/보정 predictor 및 Q-order 비교 |
| 실제 작은 후보집합만 예측 | retrieved rows 직접 head 호출 | movie budget와 ALS/GBT 계산 행/호출/CPU·wall 별도 기록 |
| 부족한 후보는 짧게 반환 | E_u prefix 순회 후 종료 | no vector/empty/exhausted, viewed/중복/globalfill0 |
| 실제 추천 결과 비교 | sweep 후 동결 check 및 reference | DIRECT-PREFIX/ALL-CONTENT/E_u·global full predictor/Q/random 비교 |
| 인기·학습 근거·언어 편향 점검 | descriptive reporting | TMDb와ML train분리, Top1/slots/unique/users, ko/KR/시대 |
| 신규·삭제에 기준이 흔들리지 않음 | `dv2_runtime.FrozenCatalog` | append/tombstone/reverse/rebatch/reload/null/OOV; prefix 변화는 별도 기록 |
| 영화로 설명 가능한 결과 | 고정 source names + 실제Q/이력 연결 | 33개 source 사례와 실제 개발사용자 사례; 독립 masked metadata 검토 |
| 기준을 보고 바꾸지 않음 | 사전 EXECUTION/config/code fingerprint | 검토 사본/출력 seals, selection과check분리·재선정 없음 |
| 사람의 발견 경험과 혼동하지 않음 | 기술·예측·관측·만족도 상태 분리 | 모든 최종 문서/manifest에 인간 만족도 UNMEASURED |

API·DB·배포는 이번 개인 연구의 구현 대상이 아니다. 결과물은 준비된 metadata와 명시적 rating/viewed 입력을 받는 로컬 함수이며, 제품의 전역 계약을 임의로 변경하지 않는다. 요구된 정상·빈·입력 오류 상태는 runtime/tests에 연결하고, 서비스 권한/외부 장애 응답을 구현한 것으로 표현하지 않는다.
