# 추천·예상 별점 개선 기록 체계

> 상태: `APPROVED` — 모든 추천 실험과 모델 채택에 적용
> 적용 범위: 예상 별점, 개인 추천, 탐험 추천, 유사 추천, 파티 추천

## 1. 목적

좋아진 최종 숫자만 남기지 않고 다음 질문에 다시 답할 수 있게 한다.

- 무엇을 바꿨고 왜 바꿨는가?
- 어느 기준선과 같은 조건에서 비교했는가?
- 전체 평균뿐 아니라 어떤 사용자·영화 구간이 좋아지거나 나빠졌는가?
- 예상 별점, Top-N 정확도, 탐험성, coverage, 비용 사이에 어떤 교환이 있었는가?
- 왜 채택하거나 폐기했으며 어떤 조건에서 결정을 재검토하는가?

예상 별점과 Top-N 추천은 같은 모델을 재사용할 수 있지만 서로 다른 문제로 평가한다.
예상 별점은 MAE·calibration·coverage, Top-N은 NDCG·Recall·실패 노출률을 대표 지표로 쓴다.

## 2. 기록 단위

```text
제품 결정
  → 실험 가설
    → 재현 가능한 Run record
      → 기준선 대비 비교
        → Insight
          → 모델 채택·폐기 결정
            → 서빙 버전과 실제 결과 연결
```

| 기록 | 위치 | 변경 규칙 |
| --- | --- | --- |
| 제품 판단 | `product-decisions-required.md` | 소유자 승인 없이 의미를 발명하지 않음 |
| 설계 교정 | `design-review-log.md` | 사용자 검토·논리적 오류·정책 변경 이력 보존 |
| 실험 정의·결과 | `experiments/<run_id>/` | 완료한 run은 덮어쓰지 않고 새 run 생성 |
| 통찰 | `insight-log.md` | 관찰·해석·한계·후속 실험을 분리 |
| 채택 모델 | `model-registry.yaml` | Gate 근거와 rollback 대상 필수 |
| 입력 신호 계약 | `00-input-signal-contract-vnext.md` | binary 온보딩과 활성 Rating을 분리 |
| 평가 프로토콜 | `01-offline-evaluation-protocol-vnext.md` | split·candidate·SESOI 변경 시 별도 protocol version |
| Top-2 위험 회피 설계 | `02-top2-risk-aware-evaluation-design.md` | 2편 노출의 Harm→Miss→상위 품질 lexicographic Gate |
| protocol 초깃값 | `protocols/rec-eval-vnext.json` | LLM·runner가 동일 상수를 읽도록 고정 |
| Top-2 v4 protocol | `protocols/rec-eval-top2-v4.json` | NATURAL_ALL·검정력 preflight가 필요한 차기 판정 초안 |
| 콘텐츠 cold-item 설계 | `03-content-cold-item-evaluation-design.md` | Train 밀도와 정답 민감도를 분리한 희소·미등장 영화 평가 |
| cold-item v2 protocol | `protocols/rec-eval-content-cold-v2.json` | item firewall·density panel preflight가 필요한 초안 |
| Top-2 v4 실행 계약 | `contracts/rec-ev-020p-artifacts.json` | Validation cohort·slate·paired power 입력 계약 |
| cold-item v2 실행 계약 | `contracts/rec-ev-021p-artifacts.json` | firewall·panel·통계 fixture 계약 |
| Top-2 Validation 결과 | `evidence/REC-EV-020P-top2-v4-validation-preflight.md` | K별 평가 가능 사용자와 남은 paired-power blocker |
| cold-item Validation 결과 | `evidence/REC-EV-021P-content-cold-v2-preflight.md` | 역할 충돌 수정·panel 표본·Validation pilot 준비 완료 |
| 최종 쉬운 보고서 | `FEELM-recommendation-evaluation-final-report.md` | 설계·실행 결과·허용 주장·다음 순서 |
| 문제 해결·AI 활용 정리 | `portfolio-problem-solving-and-ai.md` | 포트폴리오·면접용 경험 서술 |
| 데이터 인사이트 | `data-insights-summary.md` | K·예상 별점·한국 영화·cold 표본 요약 |
| Jira 기록 원고 | `jira-recommendation-evaluation-summary.md` | Epic·하위 이슈·최종 댓글 복사본 |
| 구현 준비도 | `vnext-implementation-readiness.md` | 다음 READY task와 downstream Gate를 구분 |
| 019A artifact 계약 | `contracts/rec-ev-019a-artifacts.json` | cohort 경로·column schema·명령·Gate 고정 |
| 019A cohort 전체 결과 | `evidence/REC-EV-019A-binary-cohort-build.md` | cutoff-safe 후보 42,123편·최종 후보 41,625편·K10 5,476명 Gate |
| 019B artifact 계약 | `contracts/rec-ev-019b-artifacts.json` | TMDB feature·embedding·cache·coverage 계약 고정 |
| 019B 100편 사전검사 | `evidence/REC-EV-019B-tmdb-feature-preflight.md` | 실제 TMDB·cache·identity·384차원 embedding 실행 검증 |
| 019B 전체 실행 결과 | `evidence/REC-EV-019B-tmdb-feature-build.md` | 69,603편 콘텐츠 특성 집합의 identity·structured·text coverage와 실행 실패·복구 기록 |
| 019C 실행 계약 | `contracts/rec-ev-019c-validation-artifacts.json` | 모델·후보·trial·fallback·checkpoint·Validation/Test 방화벽 고정 |
| 019C 계약 준비 결과 | `evidence/REC-EV-019C-contract-readiness.md` | 계약·preflight 상태와 실제 Validation·Test 차단 경계 |
| 019C runner·의존성 preflight | `evidence/REC-EV-019C-runner-and-dependency-preflight.md` | 합성 15개·Linux dependency 9개 PASS, 실제 runner 구현만 GO |
| 제품 서빙 경계 | `serving-contract.md` | 현재 승인 정책과 vNext 후보를 구분 |
| 연구 보고서 | `personalized-hybrid-design-report.md` | 네 추천 Head의 가설·결과·한계를 연결 |

## 3. 실험 디렉터리 계약

각 실험은 아래 파일을 가진다.

```text
docs/recommendation/experiments/<run_id>/
├─ run.yaml              # 가설, code/data/split/seed/parameter version
├─ metrics.json          # 전체·segment 지표와 신뢰구간
├─ comparison.md         # 기준선 대비 변화와 trade-off
├─ insight.md            # 관찰, 해석, 반례, 후속 실험
└─ artifacts.json        # model/report/parquet 경로와 checksum
```

대용량 모델·Parquet은 Git에 넣지 않고 `artifacts.json`에 위치와 checksum만 기록한다. 실패한 실험도
삭제하지 않는다. 실패 원인과 배제한 가설은 중복 작업을 막는 프로젝트 자산이다.

## 4. 통찰 작성 규칙

통찰은 반드시 아래를 구분한다.

1. **관찰:** 어떤 고정 조건에서 어떤 수치가 얼마나 변했는가.
2. **해석:** 왜 변했다고 보는가. 인과로 확정하지 않는다.
3. **구간:** K0~K20, 활동량, 영화 인기도, 시대·장르 중 영향받은 구간.
4. **대가:** 정확도, 탐험성, coverage, latency, 비용 중 악화된 값.
5. **한계:** MovieLens 노출 편향, 합성 파티, 온라인 만족도 부재 등.
6. **결정:** 채택·폐기·추가 실험 중 하나와 그 이유.

`NDCG가 올랐다`만으로는 통찰이 아니다. 예를 들어 “전체 NDCG는 증가했으나 K3 이하 coverage가
감소해 onboarding 사용자에게는 회귀이며, content fallback 결합을 다음 가설로 둔다”처럼 쓴다.

## 5. 채택 Gate

- 같은 split·candidate·seed에서 강한 기준선과 비교한다.
- 대표 지표 차이와 95% paired 신뢰구간을 기록한다.
- binary 온보딩 `K_b=0/5/10`과 활성 Rating `K_r=0/1/3/5/10/20/30/50`을 구분한다.
- 예상 별점 개선과 추천 순위 개선을 한 숫자로 합치지 않는다.
- 탐험 추천은 소유자가 승인한 관련성 손실 예산 안에서만 채택한다.
- 파티 추천은 평균뿐 아니라 최저 구성원·불만 비율·coverage를 함께 본다.
- 채택 record에는 이전 champion과 rollback 명령 또는 artifact를 남긴다.
- Test 결과를 본 뒤 Gate나 손실 예산을 바꾸지 않는다.

## 6. 제품 결과 연결

서비스에서는 추천 노출 당시 `recommendationVersion`, `modelVersion`, `inputVersion`, 후보 위치,
예상 별점·신뢰 상태를 snapshot으로 보존한다. 이후 상세 진입, OTT 옵션 확인, 감상 확인, 실제
Rating을 같은 노출과 연결한다. 노출되지 않은 영화의 미평가를 실패나 싫어요로 해석하지 않는다.

오프라인 결과는 배포 후보를 고르는 근거이고, 실제 만족을 증명하는 최종 근거는 아니다. 온라인
사건이 쌓이면 개인별 rating scale을 반영한 예상 별점 오차와 추천 행동 funnel을 연결해
`estimatedRecommendationUtility`를 version별로 비교하고 다음 실험 가설로 되돌린다. raw `4~5점
비율`을 추천 만족도 KPI로 사용하지 않는다. 자동 추론 결과는 직접 관측한 감정이 아니므로
`추천 만족도`가 아니라 `추천 결과 효용 추정치`로 명명한다.

집계 지표만으로 알고리즘 행동을 숨기지 않도록
[REC-EV-016 사용자 A 사례](./evidence/REC-EV-016-user-case-a.md)는 결과를 보기 전 고정 해시로 선택한
동일 MovieLens 사용자의 Popularity·Content·Hybrid·ALS·Explore·K10 Fold-in 실제 영화 Top-10과
들어온/빠진 제목을 기록한다. 단일 사례는 실패 설명과 회귀 진단에만 쓰며 champion 선택 권한은 없다.

[REC-EV-017](./evidence/REC-EV-017-relational-tag-ablation.md)은 이를 영화→영화 공동 선호,
장르→장르 조건부 lift, Train 시점 자유 태그 의미로 확장했다. Tag alpha 0.1은 전체 NDCG를
높였지만 P2 인기도 구간을 명확히 악화시키고 long-tail을 개선하지 못해 일반 ranking 후보로
채택하지 않았다. TMDB 구조·텍스트 ablation은 50,977편 전수 feature artifact 전까지 차단한다.
