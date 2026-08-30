# REC-EV-011 Cold-start Fold-in full-catalog ranking

Status: `COMPLETED_FULL_CATALOG_EVIDENCE` (offline candidate; product approval 아님)

## 결론

- REC-EV-003B와 동일한 hash-parity 사용자 분리(selection 1,230 / evaluation 1,323), K1/3/5/10/20, alpha 0.0~1.0 grid를 재사용했다.
- REC-EV-002의 50,977개 Train-known universe를 전부 scan하고 first-K seen을 제외했으며 held-out positive를 주입하지 않았다.
- cohort 전체 이력 누수를 피하려고 REC-EV-003이 REC-EV-002 ALS 설정으로 재학습한 cohort-excluded item/user factor를 사용했다. factor가 없는 item은 bias fallback했다.
- sampled REC-EV-003B는 모든 K에서 alpha 0이었다. full-catalog selection은 K1/3/5/10/20에 각각 0.2/0.2/0.1/0.2/0.3을 잠갔다.
- evaluation에서 K10의 paired NDCG CI가 명확히 0 위였고 K5 하한은 0.000016으로 경계 수준이다. practical 후보 해석은 K10 중심으로 제한하며 ranking champion은 자동 승인하지 않는다.
- expected-star, star scale, 공개 API/UI는 이 실험으로 열지 않는다.

| K | alpha | Popularity candidate R@500 | Blend candidate R@500 | Popularity NDCG@10 | Blend NDCG@10 | paired 95% CI |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 0.2 | 0.257748 | 0.246410 | 0.004393 | 0.004897 | [-0.000392, 0.001575] |
| 3 | 0.2 | 0.257748 | 0.261527 | 0.004415 | 0.005340 | [-0.000172, 0.002323] |
| 5 | 0.1 | 0.257748 | 0.269841 | 0.004703 | 0.005754 | [0.000016, 0.002202] |
| 10 | 0.2 | 0.258503 | 0.278912 | 0.004723 | 0.006154 | [0.000253, 0.002783] |
| 20 | 0.3 | 0.258503 | 0.266818 | 0.004765 | 0.006467 | [-0.000046, 0.003579] |

첫 임시 run은 cohort-excluded 통계의 42,299편만 universe로 잘못 사용해 모든 K에서 alpha 0을 냈다.
요청 계약의 REC-EV-002 Train-known 50,977편을 충족하도록 Popularity/universe 입력을 수정했고 protocol
hash가 `43b36a...`에서 `d877db...`로 바뀌었다. 첫 임시 산출물은 현재 lock/checksum 체인에서 교체됐다.
현재 프로토콜을 코드 변경 없이 두 번 실행한 runtime 제외 canonical hash는 selection
`08d4fa645a436e62cc473de18a4346862cd01f5dc4dd5a372e3a529ac54bbe04`, evaluation
`105bf492b6cf8fa4f64cdc0cc52e19a644e77710ad7b31efba71122c815e06db`로 각각 일치했다.

최종 재실행은 selection 3,448,594,050 score comparisons / 32.772초, evaluation 674,425,710 score
evaluations / 36.393초였다. tracked aggregate에는 raw ID를 남기지 않았다. Test 결과로 alpha를 재선택하지 않았다.

재현은 `scripts/recommendation_cold_start_full_catalog.py selection`으로 lock을 만든 뒤 같은 입력과 lock을 `evaluation` subcommand에 전달한다. verifier는 다음과 같다.

```powershell
$env:PYTHONPATH=(Resolve-Path 'scripts').Path
py -3.12 scripts/verify_recommendation_cold_start_full_catalog.py --manifest docs/recommendation/evidence/manifests/rec-ev-011.json
```
