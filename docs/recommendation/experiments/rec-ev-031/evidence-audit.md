# 제품 질문과 기존 근거 감사

상태: DRAFT — 개인 연구 의사결정 자료. 감사일 2026-09-07.
팀 `cb280bd1f360947646fc9ca22626ec988198ee2e`, 연구 `17600a8f42b2d07b94d70b5e5eaa086b24642599`.

## 핵심 교정

기존 실험은 무결론이 아니다. 019C에는 실제 인기도 대비 우위가 있고, 030에는 구조 콘텐츠의
개인화 귀속 근거가 있다. 둘의 입력·후보·정답·대조군이 달라 결론을 이어 붙일 수 없다.
022/030의 입력 변환은 **관측 K개 histogram + calibration prior**이고 채점만 전체 이력
midrank다. 전체 이력 채점을 모델 입력 누출로 오해해 기존 결론을 폐기하지 않는다.
또한 이 계열은 0.5~5.0의 10bin을 보존한다. 별점 간격을 새로 명시했다는 이유만으로 전부 재실행하지 않는다.

## 연결표

아래 경로는 개인 연구 저장소의 `docs/recommendation` 기준이다. 결과 수치는 기존 보고된
aggregate를 감사한 것이며 원본 표본별 재계산 완료를 뜻하지 않는다.

| 제품 질문 | 실제 입력·채점 조건과 기존 근거 | 근거 수준·현재 결론 | 추가 검증과 재실험 범위 |
| --- | --- | --- | --- |
| 온보딩 카드·최소 응답 수 | `evidence/REC-EV-019D-prefix-ablation.md`: 1,053명 동일 미래10행, binary5/10 비교. ΔNDCG +0.026562, Harm upper 0.012346로 당시 안전 기준 실패. 029/030은 hash profile30·가린 rated 후보. | 간접. 입력 증가의 정보 효과는 있으나 실제 카드 인지도·응답률·최소 응답 수의 근거는 아님. K30은 탐색 상한. | 기존 대규모 K sweep 반복 없이 동일 사용자/정답·관측가능 필터에서 연결. 실제 입력 부담은 사용자 연구. |
| binary UI와 0.5 별점 UI | `evidence/REC-EV-008-ui-comparison.md`: 실제 사용자 연구 NOT_RUN. 022/029 binary는 profile percentile의 sign. | UI 우열 미실험. magnitude 대 sign은 내부 인코딩 비교이며 실제 binary 응답이 아님. | 고정 경계 binary 대리 변환과 raw/relative 별점의 정보량 비교, 입력 시간·이탈은 별도. 031은 이 결정을 확정하지 않음. |
| 원점수·상대화·순위 | `evidence/REC-EV-001-rating-style.md`: 평가 스타일별 raw4+ 중앙값 33.3%/79.8%. `REC-EV-015-relative-utility.md`: half-star 동점 midrank 보정. 022/030은 관측 K개 기반 변환. | 일부 직접. 공통4점의 비교 한계와 상대 신호의 유용성은 유지. raw보다 상대화가 추천에 항상 우월하다는 직접 대조는 없음. | 분포·동점 교훈 재사용. raw 대 상대화와 절대/상대 채점 민감도 후속. |
| 누적 별점 전체 활용 | `evidence/REC-EV-018-user-percentile-audit.md`는 전체 Train 이력 진단. 030의 이력량 segment는 입력30과 다름. | 간접. 사용자별 이득 차이는 있으나 전체 누적 활용 정책은 미결정. | 전체 관측 이력 대 제한 prefix를 별도 실험. 031의 K30을 누적 전체로 부르지 않음. |
| 협업·TMDB의 후보/순위 역할 | `REC-EV-011-cold-foldin-full-catalog.md`: K10 ALS blend NDCG .004723→.006154. 019C LightFM은 B0 우위. `REC-EV-027-030-masked-content-personalization-final-report.md`: 구조 OWN/SHUFFLE 귀속 성공. | 역할별 직접. 구조 콘텐츠의 개인 신호는 재사용하되 전체 후보검색 우위는 미확인. E5 증분도 견고하지 않았음. | **031의 첫 연결**: 구조 콘텐츠 단독/인기도/후보 결합을 common catalog에서 비교. ALS/LightFM 재학습은 첫 묶음에서 생략. |
| 내부 k와 외부 팝콘8종 | 추천 근거·계약·실행기에서 실제 K-means k 비교/추천 ablation 근거를 찾지 못함. 기존 K는 입력 수. | 미실험. 외부8종은 표현 제약, 내부8은 품질 확증 아님. | 입력·후보 연결 후 소수 군집 대안과 매핑/안정성/추천효과를 분리. |
| 맞춤·발견과 2+1 | `REC-EV-013-constrained-two-plus-one.md`: Top2 유지+세 번째 교체의 최소 손실 후보도 selection NDCG@3 -28.57%. Explore05 계열 약 -45.5%. | 특정 규칙의 실패는 직접 근거. 모든 2+1을 기각하거나 맛 발견까지 검증한 것은 아님. | 기존 실패 규칙 반복하지 않고 선호 유지·경험 적음의 정의, 슬롯/전체 품질을 후속 검증. |
| 실제 인기도 대비 조건별 이득 | 최종보고서 019C tuning 제외: ΔNDCG K5 +.03331 [.02582,.04114], K10 +.04532 [.03681,.05462]. 관측 positive 약96%가 인기 Q4. 030 대조군은 SHUFFLE/RANDOM. | 특정 조건 직접. 효과를 전체 사용자나 long-tail로 확장하지 않음. | 현재 별점·구조콘텐츠·후보 정책으로 전이되는지 031에서 확인. |

## 입력 변환과 채점 구분: 코드로 확인한 연결

- `scripts/run_rec_ev_022a_stage1.py:649`는 profile_ratings[:k]를 전달한다.
- `scripts/rec_ev_022a_core.py:60`은 half-star 검증, :92 이후는 받은 K개 histogram이다.
- `scripts/run_rec_ev_030_confirmation.py:393` 이후는 profile30만 입력한다.
- `scripts/rec_ev_027_core.py:38,48`은 관측 histogram과 prior, :58 이후는 별도 평가 q 계산이다.
- `scripts/run_rec_ev_030_confirmation.py:446` 이후 전체 이력 histogram은 점수 봉인 뒤 채점에만 사용한다.
- 019의 상대 binary는 현재 항목까지 순차 prefix + prior, 채점은 이후10행 창 내부 midrank다.
  022/030의 전체 이력 채점과 같은 정답 정의로 취급하지 않는다.

030의 공개 `analysis/primary-inference.json` aggregate는 기존 manifest SHA와 일치했고,
primary_pass=true 및 masked-rated 범위·Test/reserve 미사용 설명도 일치했다.
해시: `1f44423a22ea1d9a393b8819c44888202d884bcc176b2155d8cfa33ccdd612d6`.

## 공식 계약과 이번 연구의 경계

| 공식 문서(S15P21E106 기준) | 현재 계약/가정 | 연구 취급 |
| --- | --- | --- |
| docs/api/onboarding-ott.md:69 | 인지도·8맛 균등은 가정. API 카드 요청 기본20/최대40과 동시노출·최소응답 정책은 구분 | 실제 노출·응답 정책 미결정 |
| docs/requirements.md:87; docs/api/watch.md:258 | LIKE/DISLIKE 별도 저장, 감상 score integer1~5 | 별점0.5는 현재 연구 지시, 범위0.5~5는 연구 가정. API 변경 없음 |
| docs/adr/0002-als-content-hybrid-recommendation.md:16; docs/adr/0017-python-foldin-spark-trending-split.md:14 | ALS+TMDB, 전체 최신 평가·온보딩 기반 Top500 | 연구에서 품질 가설로 검증. 공식 채택을 실증으로 취급하지 않음 |
| docs/adr/0015-kmeans-ctfidf-taste-clusters.md:16 | 고정 k8·1:1·predict·버전/재학습 없음 | 외부8 유지, 내부 대안은 연구에서만 |
| docs/requirements.md:172 | 2+1와 발견 fallback | 구성·발견 정의의 품질은 후속 검증 |

## 자료 가용성과 재사용 결정

029 SELECTION R0에는 2,180명과 profile30/target20의 공개 cache가 있다. 새 반복 확증 대신
이 표본을 명시적으로 재사용한다. config.json의 expected SHA는 기존 seal에서 가져왔으며
실제 실행에서 입력별로 재검증한다. raw 데이터는 manifest에 기록된 외부 파일을 읽기 전용으로
사용하며 다른 저장소의 문서는 참조하지 않는다.

019C popularity와 027 협업 모델은 029 평가 사용자가 학습에 포함될 가능성이 있어 그대로
비교하지 않는다. 029 calibration에 속한 허용 사용자만 새로 집계한다. 030·미개봉 reserve는 보존한다.
기존 full-catalog용 작은 int16 정렬 인덱스는 재사용하지 않고 85,517편을 담는 int32/int64를 쓴다.

첫 연결이 정리된 뒤에만 입력 UI 대리 비교·전체 누적 입력·군집·발견의 후속 순서를 정한다.
각 질문을 단순 PASS/FAIL로 끝내지 않고 현재 운영 후보와 미결정 이유를 최종 정책 제안에 기록한다.

## 실행 중 확인한 공식 원격 문서 추가

2026-09-07 fetch에서 origin/develop은 `2c8e96a9d065ccaf91783344773e5325b38109b1`로
전진했다. 로컬 HEAD는 변경하지 않았다. 추가된 frontend/docs/screens의 5개 문서를
별도 읽기 검토자가 원격 git object로 확인했다. 기존 AGENTS/API/ADR/요구사항 변경은 없고,
화면 목업/계약 정리 문서여서 REC-EV-031 입력·모델·채점 설계 변경은 필요하지 않았다.

- `screen-spec.md:130–133`: API 카드 기본20·최대40, 동시노출수·최소선택수 미정.
  이를 K=20/40이나 최소 응답 근거로 사용하지 않는다.
- `screen-spec.md:744`, `frontend-structure.md:510`: 공식 개인 별점 정수1~5이며 0.5 불허.
  `wireframe-change-requests.md:64–70`은 평균값 소수를 별도로 유지한다.
- `screen-spec.md:272–279`: TASTE2+DISCOVERY1. 발견 후보가 없으면 TASTE3.
  `wireframe-change-requests.md:312`의 newFlavorThreshold는 미정이다.
- `wireframe-change-requests.md:142–148`: 맛 이름·색상·코드 매핑 검수 미완료.
  목업 장르명8개를 실제 확정 맛으로 재사용하지 않는다.
- `screen-spec.md:651–652`, `wireframe-change-requests.md:180–181`: 맛별 감상 개수와
  평균 별점은 분리하며 미관측은 count0/averageScore null이다. 0개는 불호가 아니다.
- `screen-spec.md:774–780`: 취향 일치/연결 %는 정의/API 필드 부재로 구현 보류다.
  이번 내부 점수·percentile을 만족 확률이나 화면 일치%로 표시할 근거는 없다.

이 단락의 경로·행은 `2c8e96a`의 `frontend/docs/screens/` 기준이다.
