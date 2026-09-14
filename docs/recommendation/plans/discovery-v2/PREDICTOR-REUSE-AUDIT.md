# 예상 별점 모델 재사용 읽기 감사

상태: DRAFT — 2026-09-13. Mendel의 독립 읽기 감사. 새 서비스축 추론/성능 검증 완료를 뜻하지 않는다.

결론: 검토된 RH230 GBT와 보정 ALS Router 구성요소는 재사용 후보지만 237,817편용 완성 adapter는 없다.
새 서비스 ID축 메타데이터 연결과 특징 parity를 독립 검증해야 한다. 기존85,517행 저장 예측을 행 위치로 이어 붙이지 않는다.

기준 루트: `C:/higher/projects/FEELM-standalone`.

| 구성요소 | 루트 아래 경로 / 호출 |
| --- | --- |
| RH230 특징 | scripts/final344_adapter.py: MetadataAdapter(metadata).transform(history_ids, stars, candidate_ids) |
| GBT 추론 | scripts/combination340_models.py: Trees(model_path, range(230)).predict(features) |
| 선정 GBT native | outputs/recommendation-evidence/final344/GBT120_s339/model/native |
| ALS factor | outputs/recommendation-evidence/combination340/ALS/item-factors |
| ALS fold-in | scripts/combination340_models.py: direct_als(..., reg=.1) |
| 보정 | outputs/recommendation-evidence/hybrid345/calibration.json |
| 조립 | scripts/hybrid345_catalog.py: calibration_map / combine_policy_scores |
| 선택/감사 | outputs/recommendation-evidence/hybrid345/selection.json / result-audit.json |
| 모델 봉인 | outputs/recommendation-evidence/final344/GBT120_s339-seal.json / model-audit-seal.json |

## 입력과 척도

- 별점은0.5~5.0의0.5간격, 최대30편. cap10 계수의 검증 범위는 입력상한10이다.
- 보정은 a+b*raw. cap10 ALS: a=1.9517195032011836, b=0.5019366554403232.
- cap10 GBT120_s339: a=0.8483345855557736, b=0.7856949097272242.
- 기존 순위는 affine 보정값, 별점 오차는 [0.5,5] clip이다. 순위에도 clip하면 동점이 늘어 기존 정책과 달라진다.
- 선정 Router는 ACTUAL_ALS 가능시 보정 ALS, 그 외 보정 GBT, Qwen 혼합0. Qwen/E5/줄거리 임베딩은 이 경로에 필요없다.
- RH230 내부 TMDb 보정 C=6.173088067675869, m=48은 고정한다. 새 후보 정렬 Q의 C/m을 이 특징에 대입하면 입력이 달라진다.

필수 메타데이터는 adapter 계약과 일치해야 한다.

```text
movie_id
genre_ids, keyword_ids, production_country_codes
director_ids, top5_cast_ids, production_company_ids, collection_ids
original_language, release_year, runtime_minutes
tmdb_vote_average, tmdb_vote_count, tmdb_popularity
```

현재 k8 catalog에 없는 인물/제작/시간 정보를 원본 details/credits에서 구성해야 한다.
토큰 타입, 배우 순서, 숫자 변환, 누락 처리와 RH230 열 순서를 맞추고, 결측을 정상 지원이라고 표시하지 않는다.
scripts/test_final344_adapter.py의 독립 산식·새ID·순서/추가 불변 검사를 참고하되 새 서비스축 검증은 별도 실행한다.
GBT의 movie_id는 특징에 직접 포함되지 않지만 ALS factor는 MovieLens ID축이다. service↔TMDb↔MovieLens를 검증하고 모호한35개 연결은 ALS 미지원으로 유지한다.

## 읽기 감사에서 확인된 봉인

검토자는 adapter·의존 코드7개와 검토 핀, GBT native12개 파일과 모델 봉인의 일치를 확인했다.
기존 저장 parity의 native 최대 오차는8.88e-15, 경계fixture는1.33e-15였다. 이것은 새서비스축의 parity 결과가 아니다.

| 파일 | SHA256 |
| --- | --- |
| scripts/final344_adapter.py | 15b6a53c5be9e6207cb215857058570304b61c17c5aa88198341efd55f688894 |
| scripts/combination340_models.py | a519586b25d81574a96e1847dc6a4d8d08d3ded276deff64c5d82ff0bcdecd25 |
| final344/GBT120_s339-seal.json | ba9be1a4769d9cd77a8d8b0a11b437ada10254a030656e98cfa7f7f07d7ffee7 |
| hybrid345/calibration.json | 6b2baf1dd27997cffe7f69fdc1e69e9fe80d26a29f467ee53958c2dc6f46e562 |
| hybrid345/selection.json | cc22115e3b8f5e216bcd5a8f77a1c3484e4e8e9ee5831e471278e5997f5d51a0 |

## 남는 위험

ACTUAL_ALS의 factor존재+지원이력>=1 조건은 영화의 학습 평가 수가 충분하다는 조건이 아니다.
양의 기울기 affine 보정은 ALS 내부 순위를 바꾸지 않으므로, 저지원 영화가 상단에 오는 위험도 자동 해결하지 않는다.
TMDb지원 수와 ML학습지원 수를 분리하여 최종 Top1/전체 슬롯을 감사하고 동일 후보의 순위 대조를 수행해야 한다.
이 구성요소 재사용 근거를 발견 만족도의 정답이나 성능 보증으로 쓰지 않는다.
