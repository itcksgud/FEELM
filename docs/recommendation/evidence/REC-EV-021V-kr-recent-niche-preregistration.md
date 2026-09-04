# REC-EV-021V — KR Recent/Niche Pooled-Judgment Validation preregistration

상태: `APPROVED_FOR_RECRUITMENT_PREFLIGHT_ONLY`

## 질문과 증거 경계

REC-EV-021V는 MovieLens 행동 검증에서 거의 측정되지 않은 **한국 거주 사용자의 2024~2026 한국-origin
저인기 영화 판단**을 직접 수집하도록 설계한다. MovieLens cohort, future window, Locked Test와 합치지
않는다. synthetic fixture와 dry-run은 인프라 증거일 뿐 실제 target-domain evidence가 아니다.

현재 허가는 계약·schema·builder·importer·analyzer·verifier와 zero-cost fixture 실행까지다. 공개 데이터
다운로드, 사람 모집, 동의 수집, 인센티브 지급, PII 저장은 허가되지 않았다. 제품 채택 권한도 없으므로
모든 산출물에서 `locked_test_used=false`, `champion=null`, `product_policy_updated=false`를 유지한다.

## 모집과 K10 입력

- 한국 거주 평가자 목표는 100명이고 valid user 100명 미만이면 완료가 아니다.
- 입력은 사용자당 정확히 K10이며 canonical movie로 매핑된 `POSITIVE` 2개 이상과 `NEGATIVE` 2개 이상이
  필요하다. 미매핑은 negative로 바꾸지 않는다.
- participant ID는 `p_` 뒤 16자리 hex인 비식별 token만 허용한다.
- name, email, phone, address, IP, device/advertising ID, 주민등록번호, 결제·계좌 정보와 자유 텍스트는
  importer가 저장 전에 거부한다.
- 버전이 있는 활성 consent와 수집 시각이 필요하며 철회된 참여자는 분석에서 제외한다.

## catalog와 동결 모델

catalog cutoff는 2026-09-05다. target은 `2024-01-01..2026-09-05`, `KR` origin, 사전 동결한 public
source popularity 규칙의 low-pop 영화다. 비교 stratum은 older Korean low-pop, recent non-Korean
low-pop control, popular control이다. source manifest에는 snapshot 시각·버전, local path/bytes/SHA-256,
license ID/URL, 연구 이용·재배포 상태, attribution, popularity threshold를 judgment 전에 고정한다.

모델은 다음 네 개만 쓴다.

- B0 `B0_MOVIELENS_BAYESIAN_RATING-T003`
- B7 `B7_TMDB_TEXT_CONTENT-T001`, E5 revision
  `614241f622f53c4eeff9890bdc4f31cfecc418b3`
- B8 `B8_LIGHTFM-T003`, seed 17, fit/refit 금지
- B9 `B9_RRF-T003`, rank-only fusion

실제 실행 전 catalog snapshot과 사용자 K10에 대한 네 ranking을 따로 freeze하고 checksum을 기록한다.
preflight는 모델을 새로 학습하거나 threshold를 탐색하지 않는다.

## deterministic blind pool과 judgment

각 stratum에서 네 model rank를 balanced round-robin으로 순회하며 dedup한 12편을 뽑는다. 사용자당
`12 × 4 = 48`편이 최대이자 정상 완료 크기다. source/model provenance와 각 model rank는 sealed 파일에만
남기고 참가자에게는 deterministic shuffle된 영화 정보와 blind item token만 보여준다.

각 영화에 대해 다음을 수집한다.

- `SEEN` 또는 `UNSEEN`
- viewing intent 0~4
- expected satisfaction 0~4
- intent 0~1이면 고정 enum의 uninterested reason
- 14일 actual-watch `YES/NO/UNKNOWN/null`

14일 actual-watch는 secondary이며 primary, 완료 Gate와 성공 Gate에서 제외한다.

## primary, 완료와 성공 규칙

compatibility는 `(viewing intent + expected satisfaction) / 8`로 0~1에 고정하고 `>=0.75`를 positive로
정의한다. 사용자별 unfamiliar(`UNSEEN`) recent-Korean-low-pop 공통 judged set에서 B0/B7/B8/B9를 각
frozen rank로 정렬한 linear-gain NDCG@10을 계산한다. primary eligibility는 unfamiliar target 5편 이상과
target positive 1편 이상이다.

완료 Gate는 모두 충족해야 한다.

- valid users `>=100`
- accepted unique judgments `>=4000`
- unfamiliar recent-Korean-low-pop positives `>=300`
- mapping/dedup rate `>=95%`

하나라도 미달이면 효과 수치와 무관하게 `INSUFFICIENT_TARGET_DOMAIN_EVIDENCE`다.

B7/B8/B9 각 candidate는 같은 사용자의 B0 대비 다음을 모두 충족해야 성공한다.

1. paired NDCG@10 bootstrap two-sided 95% CI lower `>0`
2. B0 mean 대비 relative improvement `>=5%`
3. Top-2에 compatibility positive가 하나도 없는 event의 paired delta one-sided 95% upper `<=2%p`
4. B0/B7/B8/B9 각 selection source를 하나씩 뺀 네 분석 모두 mean delta `>0`, relative `>=5%`

bootstrap unit은 participant, iterations 10,000, seed 20260925다. 하나 이상의 candidate가 모든 Gate를
통과하면 상태는 `PASS_TARGET_DOMAIN_POOLED_JUDGMENT_REQUIRES_SEPARATE_PRODUCT_REVIEW`다. 이는 champion
선택이나 제품 정책 변경이 아니다.

## 모집 전 차단 조건

public source가 로컬에 없으면 다운로드하지 않고 실패한다. 실제 모집 전에는 license/attribution 승인,
동의문과 privacy·retention·deletion 절차, 모집 권한, 1인 인센티브와 KRW budget cap, frozen ranking
artifact를 사람이 승인·제공해야 한다. 계획 비용이 승인 cap을 넘으면 pool을 external-ready로 만들지 않는다.
