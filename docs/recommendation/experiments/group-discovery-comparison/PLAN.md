# 그룹 기반 발견 추천: 방법 1 / 방법 2-A / 방법 2-B

상태: DRAFT — 독립 설계 검토용. 2026-09-11. 범위: DESIGN_ONLY.

## 요청과 판단할 질문

사용자는 그룹별 영화 순위를 미리 저장하고 사용자에게 맞는 그룹의 상위 후보만 개인화하는 방법 1과, 좋아한 영화가 속한 그룹 내부에서 먼 경계 후보를 찾는 방법 2를 비교하고자 한다. 후속 지시로 방법 2의 거리를 (A) 기존 선호 영화와의 거리, (B) 해당 그룹 중심과의 거리로 나누어 비교한다.

이번 완료 범위는 세 방법 및 각 단계의 설계 → 다른 검토자에게 검토 → 지적 수정 → 재검토 → 통과/미통과 보고다. 앞선 사용자 지시 “통과되면 하지는 말고 말해줘”를 유지한다. **코드 작성, 새로운 데이터 집계, 군집 학습, 후보 계산, 평점 채점, 서비스 변경은 실행하지 않는다.** 실행 전 조건을 명시하는 것과 실행 승인은 다르다.

방법 2의 ‘멀면서 경계’는 주안에서 AND로 해석한다. 사용자에게 선택 질문을 보냈으며 아직 명시 답변은 없다. 단일 조건 진단도 미리 정의하지만 결과를 보고 주안을 바꾸지 않는다. ‘그룹 중앙값’은 K-means 중심점인 산술평균 centroid로 해석한다고 사용자에게 알렸다. 좌표별 median이나 대표 영화 medoid와 다르다. 다른 뜻의 답변이 오면 해당 설계를 수정하고 재검토한다.

| 방법 | 그룹 선택의 조건 | 그룹 내부 영화 조건 |
|---|---|---|
| M1 | 알려진 과거 기록에서 경험이 적고 긍정 입력과 연결됨 | 공통 사전 순위에서 상위 여러 편 |
| M2-A | 긍정 입력 영화가 적어도 한 편 속한 그룹 | 모든 긍정 입력 중 가장 가까운 영화와도 상대적으로 멀고, 그룹 경계에 가까움 |
| M2-B | M2-A와 정확히 같은 그룹 | 자기 그룹 중심에서 상대적으로 멀고, M2-A와 같은 경계 조건 |

세 방법은 동일 콘텐츠 벡터, 동일 군집, 동일 사전 순위, 동일 그룹/영화 예산, 동일 FM, 동일 사용자/후보 universe를 사용한다. M2-A 대 M2-B는 거리 기준의 교체 효과다. M1 대 M2는 그룹 선택과 내부 필터를 포함한 **전체 방법의 차이**이며 단일 요인의 인과효과가 아니다. M1의 경험이 적은 그룹에 긍정 입력이 있을 수도 있으므로 두 방법의 후보가 반드시 배타적인 것은 아니다.

## 기존 설계 및 현재 서비스 논의와의 관계

이 문서는 이웃 `discovery-classifier`의 지도 다중 라벨 분류 설계를 수정하거나 그 승인 범위를 이어받지 않는다. 새 비교의 그룹은 콘텐츠 벡터의 기하학적 군집이며 의미 정답을 학습한 분류기가 아니다. LLM, 의미 라벨 생성, 새로운 지도 분류 학습은 포함하지 않는다. 의미 라벨의 부족과 군집 계산 가능성을 혼동하지 않는다.

원본 `docs/recommendation/active-experiment.md`의 맞춤/발견 보류 및 두 편씩 추가 추천 논의는 유지한다. 본 연구는 추천 한 슬롯을 비교하는 개인 연구이며 서비스의 2+1 노출 정책을 확정하지 않는다. 팀 저장소, 원본 active 문서, API/DB, Jira/GitLab에 쓰지 않는다. 이 worktree의 새 디렉터리만 root가 작성하고 검토자는 읽기만 한다.

## 기존 자료와 한계

원본 루트는 `C:/higher/projects/FEELM-standalone`이다. 다음 경로는 이 루트 기준이다. 실제 source 해시는 향후 실행 준비 단계에서 기존 seal과 대조해 고정해야 한다.

| 자료 | 재사용 목적 | 제한 |
|---|---|---|
| `outputs/recommendation-evidence/rec-ev-045/metadata.parquet` | 85,517편 원 장르·키워드, 평가용 인물 필드 | 2026년 수집본. 과거 시점의 콘텐츠 정보 재현 아님 |
| `outputs/recommendation-evidence/text339/catalog.parquet` | 공통 movie_id 축, train_count, blocked | blocked의 기존 의미를 임의로 후보 삭제에 추가하지 않음 |
| `outputs/recommendation-evidence/text339/texts.parquet` | 기존 카탈로그 공개일 필터 | 줄거리 임베딩을 이 비교 입력에 사용하지 않음 |
| `outputs/recommendation-evidence/text339/contexts.json` | H10의 oi/stars/viewed/ei 및 평가 offset | 이미 사용한 개발 사용자. O와 viewed 범위 구별 |
| `outputs/recommendation-evidence/text339/ratings.parquet` | 학습 사용자 전용 영화 공통 순위 | 평가 사용자/미래 목표 별점 금지, 기존 시간 조건 확인 |
| `outputs/recommendation-evidence/foundation340/predictions.npz` | 관측 후보 고정 FM B 점수 | 새로운 일반화 test가 아니며 B는 서비스 채택 모델 아님 |
| `outputs/recommendation-evidence/combination340/catalog-cache/{uid}.npz` | 전체 후보 ei와 B 점수 | 축 및 foundation B와의 동일성 확인. Top10 파일로 대체 금지 |
| `outputs/recommendation-evidence/text339/labels.parquet` | S4의 uid/movie_id별 실제 rating | S1~S3에는 값 열람 금지. 순위 봉인 뒤 기존 evaluation seal과 대조 |
| 기존 prepared/fit/catalog/evaluation seals, `policy341` 실행/결과 검토 | 출처 연결·후보/평가 구현 참고 | 과거 승인은 새 정책 코드의 승인이 아님 |

시간 기준은 기존 `docs/recommendation/experiments/text339/config.json`의 origin=1672531200(2023-01-01 UTC), CATALOG 공개일 상한은 foundation340 config의 catalog_snapshot_timestamp=1788998400이다. 관측 pool과 현재 카탈로그 pool은 시간·대상 범위가 다르며 서로 같은 성능 표본으로 해석하지 않는다. 확인할 필드는 metadata의 genre_ids/keyword_ids/director_ids/top5_cast_ids, ratings의 uid/movie_id/rating/timestamp, contexts의 cap/h/oi/stars/viewed/ei/start/stop이다. 정확한 출처·형·축은 S1에서 seal과 함께 확인한다.

REC038의 8군집과 특징/거리 구현은 참고 자료다. 8개를 기본값으로 고정하거나 당시 군집을 의미 정답으로 사용하지 않는다. 새 군집의 적합성이 준비되지 않으면 임의로 옛 8군집을 대입하지 않는다.

기존 REC042는 다른 ALS·8그룹 조건에서 발견 교체의 관측 선호 손실을 보였다. 새 방법의 성공/실패를 그 결과로 확정하지 않는다. diagnostic342는 기존 FM B에서 투표 근거가 작은 영화의 높은 점수·노출 쏠림을 확인했다. 따라서 높은 예측값은 좋아함의 증명이 아니며 실제 별점, 낮은 별점, 근거량별 노출을 함께 평가한다. 이 비교는 FM을 재선정하거나 보정하지 않는다.

## 향후 단계와 중단 조건

| 단계 | 조건부 수행 내용 | 통과 기준/중단 |
|---|---|---|
| S1 입력 고정 | source hash/스키마/ID/시간·사용자 역할/기존 점수 동일성/환경 확인, 공통 cohort 고정 | 누락·해시 불일치·정답 누출·축 오류면 중단. 임의 대체 데이터 금지 |
| S2 그룹·사전 순위 | 공통 벡터와 Q 순위, K 후보 및 안정성, 중심·반경·경계, 그룹 manifest 생성 | GROUPS-METHODS의 기하/안정성 gate. 아무 K도 안 되면 GROUPING_NOT_READY |
| S3 정책 | 동일 원후보에 M1/M2-A/M2-B 및 고정 진단 적용, 후보/순위/이유/비용 봉인 | 미래 정답 미열람, 규칙 불일치/후보 누락/비결정성/예산 위반이면 중단 |
| S4 평가 | 순위 봉인 뒤 실제 별점·별도 특징 새로움·공급·회수 손실·비용을 독립 검산 | EVALUATION에 따른 개발자료 내 비교. 실제 사용자 발견의 만족도는 미검증 유지 |

향후라도 매 단계에 **사용자의 실행 요청 + 정확한 설계/코드 해시의 독립 검토 + 앞 단계 결과 검토**가 필요하다. 지금 승인된 실행 단계는 빈 목록이다. 코드가 없는 현재 PASS는 설계 PASS일 뿐 실행 준비 완료가 아니다.

실행안의 자원 상한은 S1 20분/4GiB, S2 전체 120분/8GiB, S3 60분/4GiB, S4 30분/4GiB, 동시 실험 1개·수치 스레드 2개다. 예산 초과/수렴 실패는 중단 기록하고 설정을 몰래 축소·변경하지 않는다. 군집/메타 연산용 기존 Python 환경을 사용하며 패키지 설치·외부 다운로드를 이 안에 포함하지 않는다. 모델 재학습과 신규 사용자 실험은 별도 설계/승인 범위다.

## 설계 검증과 완료 기록

검토 범위는 `PLAN.md`, `GROUPS-METHODS.md`, `EVALUATION.md`의 정확한 SHA-256다. 의미·수학, 자산·실행 준비, 평가·비교 공정성의 세 검토자가 독립 판정한다. 모든 blocking 지적을 수정하고 영향을 받는 전체 세 문서를 다시 검토한다. 최종 검토된 파일을 바꾸면 기존 PASS는 무효다. `REVIEW-LOG.md`와 `design-review.json`은 검토 라운드·지적·해시·판정·남은 조건을 기록한다.

설계 비교 질문에 답할 수 있는 구조인지와 방법의 효과가 실제로 좋은지는 분리한다. 어느 방법도 실험하지 않았으므로 이번 결과에서 승자를 쓰지 않는다.
