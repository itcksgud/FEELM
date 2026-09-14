# 설계 독립 검토 이력

현재 사용자 범위: DESIGN_ONLY. 통과 후에도 실행하지 않고 보고한다.

## 1차 검토

검토 문서 SHA-256:
- PLAN.md: 59348bae66f4be84418b49136d58e94475e1351f020dbdd4ab152c8fbe24025f
- STAGE1.md: 0d1a7fa2c6979a30f39c949622841976d141b49aa990cd13f7659e1de514a02b
- STAGES2-4.md: 5fc9301724f232c45775e9c79bc5295ddc85b407a139931ed6a9a0fa19745880

검토자 `/root/discovery_review`: CHANGES_REQUIRED (전체·라벨).
검토자 `/root/reuse_clusters`: CHANGES_REQUIRED (평가·누수).
검토자 `/root/reuse_text_data`: PASS (S1 설계 한정, 실행 미승인).

| 지적 | 수정 |
|---|---|
| 사람 annotation 조사 범위가 불명확 | S1에 원본/수신 경로6개와 제외 사유 및 부재주장 범위를 고정 |
| 양·음성 수를 맞춘 gold에서 precision을 계산할 위험 | 키워드 등록 여부와 무관한 콘텐츠그룹 대표의 고정 표집, 최소수는 quota가 아닌 readiness, 검수 UNKNOWN 잔존시 학습 보류 |
| full 모델 통과가 OOF 출력까지 승인하는 문제 | 실제 사용할6개 모델route·각threshold를 test전에 봉인하고 모두 성능검증 |
| reference novelty·영벡터·결측 처리 불명확 | 동일reference축·cosine·입력프로필·첫3편 수식, all-negative/UNKNOWN 구분, 공통158명 분모와 paired delta[-1,1] bounds·보정bootstrap 명시 |
| 빈본문/전처리 영벡터 처리 | train 최소지원은 제외 후 계산, validation/test는 삭제없이 abstain·recall/coverage에 반영 |

## 재검토

수정 문서를 같은 세 독립 검토자에게 재검토했고 모두 담당 범위에서 PASS를 받았다. `/root/discovery_review`와 `/root/reuse_clusters`는 전체와 네 단계, `/root/reuse_text_data`는 S1 데이터 설계 범위를 통과시켰다. 최신 사용자 지시를 반영한 PLAN의 최종 변경도 세 검토자가 다시 읽고 해시를 확인했다. 모든 실행 승인은 없으며 설계 타당성만 판정했다. 최종 판정과 정확한 문서 해시는 design-review.json에 기록했다.
