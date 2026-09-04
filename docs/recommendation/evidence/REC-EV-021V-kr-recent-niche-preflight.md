# REC-EV-021V — KR Recent/Niche Pooled-Judgment 모집 전 preflight

> 인프라 상태: `PASS_INFRASTRUCTURE_READY`
> 실제 증거 상태: `NO_ACTUAL_TARGET_DOMAIN_EVIDENCE`
> 분석 상태: `INSUFFICIENT_TARGET_DOMAIN_EVIDENCE`

## 결론

catalog license/time manifest, 4-stratum builder, B0/B7/B8/B9 deterministic blind pool, 세 가지 비식별
schema, judgment importer, paired analyzer, independent verifier, participant checkpoint/resume와 budget guard를
구현했다. 4명의 synthetic fixture에서 catalog 80편, blind pool 192행, judgment 192행을 end-to-end로
재구성했고 verifier가 동일 결과를 확인했다.

이는 **사람 모집을 실행했다는 뜻이 아니다**. 공개 원자료를 내려받지 않았고 실제 한국 거주 평가자,
동의, 인센티브, PII, 실제 target judgment는 0이다. 따라서 모집 전 인프라는 준비됐지만 target-domain
evidence는 없으며 결과는 `INSUFFICIENT_TARGET_DOMAIN_EVIDENCE`다.

## 구현된 차단선

- 외부 모드는 승인된 local source path/bytes/SHA-256, license와 고정 popularity rule이 없으면 실패한다.
- K10은 정확히 10편, mapped positive 2개·negative 2개 이상과 중복 0을 요구한다.
- participant/judgment import 전에 PII field와 email/phone 형태 값을 거부하고 invalid 원문은 저장하지 않는다.
- 각 사용자의 blind pool은 네 stratum 12편씩, 총 48편이며 model/source field가 participant-visible 파일에
  들어가지 않는다.
- sealed pool-source는 네 selection source가 사용자당 각 12편이 되도록 균형을 고정한다.
- participant checkpoint를 이용한 resume가 최초 build와 정확히 같은 pool을 만든다.
- fixture 비용은 0원이다. external mode는 1인 인센티브와 승인 budget cap 없이는 진행할 수 없다.
- 14일 actual-watch를 전부 바꿔도 primary 결과가 변하지 않는 단위 테스트가 있다.
- 완료 Gate 미달이면 candidate 수치와 상관없이 `INSUFFICIENT_TARGET_DOMAIN_EVIDENCE`다.

## synthetic dry-run 수치

| 항목 | 값 | 의미 |
| --- | ---: | --- |
| catalog | 80편 | stratum별 20편 fixture |
| catalog mapping/dedup | 100% | fixture pipeline 검사 |
| participants | 4명 | 실제 평가자 아님 |
| pool | 192행 | 4명 × 48편 |
| accepted judgments | 192행 | 실제 judgment 아님 |
| checkpoint resume | 4/4 | exact reconstruction |
| valid-user completion | 4/100 | 미달 |
| judgment completion | 192/4,000 | 미달 |
| 실제 target evidence | 0 | 수집하지 않음 |

## 실제 수집 전 필요한 입력·승인

1. 사용이 승인된 public catalog의 로컬 snapshot, license/attribution, cutoff, popularity threshold
2. owner가 승인한 consent 문구·버전과 privacy/retention/deletion 절차
3. 사람 모집 권한, 1인 인센티브, 총 KRW budget cap
4. 비식별 한국 거주 participant와 K10 mapped input
5. 동일 catalog snapshot에 대한 frozen B0/B7-E5/B8-LightFM/B9-RRF ranking
6. 이름·연락처·IP·device·payment identifier를 export하지 않는 별도 수집 시스템

## 검증 명령

```powershell
npm run recommendation:021v:preflight:run
npm run recommendation:021v:preflight:check
```

승인 입력 이후에도 downloader를 사용하지 않는다. owner가 제공한 local manifest와 비식별 입력으로 다음
builder를 실행하며, budget cap과 participant별 checkpoint를 통과한 뒤에만 blind/sealed pool을 만든다.

```powershell
py -3 scripts/build_rec_ev_021v_catalog_and_pool.py `
  --source-manifest <approved-source-manifest.json> `
  --participants <deidentified-participants.jsonl> `
  --onboarding-inputs <deidentified-k10.jsonl> `
  --frozen-ranking-manifest <b0-b7-b8-b9-ranking-manifest.json> `
  --output-root <approved-output-directory> `
  --approved-budget-krw <cap> `
  --incentive-per-user-krw <amount> `
  --resume
```

독립 verifier는 fixture 원본에서 catalog, blind/sealed pool, normalized judgment와 10,000회 participant
bootstrap 분석을 다시 만들고 tracked artifact checksum을 확인한다.

보호 상태는 `locked_test_used=false`, `champion=null`, `product_policy_updated=false`다.
