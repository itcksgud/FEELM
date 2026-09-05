# REC-EV-022A — K·별점 입력 표현 Stage 1 사전등록

> 상태: `INVALIDATED_IMPLEMENTATION_FIREWALL_AWAITING_CLEAN_RERUN`
>
> 독립 설계 감사: `PASS` (`01a06eeb-df86-7252-ac77-afc8d6ec24f7`)
>
> 제품 정책·Locked Test: 변경·접근 없음

## 목적부터 다시 정의

FEELM 추천이 풀어야 하는 실제 문제는 두 가지다.

1. MovieLens 행동 데이터는 유명 외국 영화에 과도하게 집중돼 한국 영화와 데이터셋 이후 영화의 협업 신호가 부족하다.
2. 실제 서비스 사용자의 시청·평가·추천 피드백을 충분히 모아 학습·평가·검증하는 것은 현재 불가능하다.

두 번째 문제에 대해서는 MovieLens 별점을 오프라인 학습·평가·검증용 행동 근거로 사용한다. 첫 번째 문제는 TMDB 구조 특징과 E5를 포함한 콘텐츠 전이가 실제로 한국·최신·cold-item 구간에서 이득을 내는지 별도 Stage 2에서 증명한다. 한국 행동 데이터가 충분하다고 가정하거나 MovieLens 사용자를 한국 사용자라고 해석하지 않는다.

REC-EV-022A는 그 전에 필요한 더 좁은 질문만 다룬다.

> 같은 사용자가 이미 평가한 영화 중 K개만 입력으로 관측했을 때, K=0..30과 `이진 부호 / 사용자 상대 강도 / 사용자 내부 순서`가 남은 20편의 관측된 선호 순서를 얼마나 복원하는가?

따라서 이 실험 하나로 실제 좋아요 UI와 별점 UI 중 하나를 승인하거나, 추천 모델 champion·한국 영화 보완·최신 영화 보완·제품 안전성을 결론내리지 않는다.

## 왜 기존 실험을 그대로 잇지 않는가

REC-EV-019C의 K=5와 K=10은 사용자와 평가창이 달라 K 효과로 해석할 수 없었다. 019D는 같은 사용자·창으로 고쳤지만 K=5와 10만 비교했고, 019E는 같은 자료에서 만든 사후 routing, 019F는 사용자 독립 확인이 아니어서 최종 `INCONCLUSIVE`였다.

또한 인기도 B0는 사용자별 모델이 아니다. K별로 달라졌던 것은 점수가 아니라 본 영화 제외 집합이었다. 022A의 주 분석은 이미 평가된 동일 `JUDGED20`만 정렬하므로 후보 제외 변화 없이 입력 정보량을 비교한다.

## 핵심 설계

- 기존 승인 split의 Locked Test bucket 60–99는 원본 행 처리 전에 모두 제외한다.
- 남은 사용자도 `TRAIN 60% / Stage1 20% / Stage2 12% / Final 8%`로 전역 분리한다.
- 모든 prior, B0, ItemKNN, support와 이후 learned model은 TRAIN 사용자만 사용한다.
- 사용자별 영화 순서는 별점과 timestamp를 보지 않는 고정 SHA-256 순서다.
- `COMMON30`은 공통 scoreable 영화가 50편 이상인 Stage1 사용자다. 앞 30편은 중첩 입력, 다음 20편은 모든 K가 공유하는 평가 집합이다.
- 사용자별 별점 기준 차이는 현재 K개와 TRAIN prior만 사용하는 smoothed percentile로 반영한다. 감춘 20편이나 이후 입력은 encoding에 쓰지 않는다.
- 비교 anchor는 동결된 TMDB 구조 cosine과 TRAIN-user-disjoint ItemKNN이다. E5와 학습 모델 비교는 Stage 2로 미룬다.
- 서비스가 두 편씩 보여주므로 선택 지표는 첫 두 편의 평균 선호 percentile과 둘 중 낮은 선호의 손실이다. 4..20은 짝수 누적 descriptive 결과만 낸다.
- 미평가는 싫어요나 안전으로 세지 않는다.

수식·salt·SHA-256 byte order·공통 영화 집합·fallback·tie-break·10,000회 max-T bootstrap·K 후보 규칙은 [실행 계약](../contracts/rec-ev-022a-k-input-encoding-stage1.json)에 고정했다.

## 기계적 후보 결정

각 입력 표현은 두 anchor 모두에서 K=0 대비 다음 조건을 연속 세 K에서 만족해야 Stage 2 후보가 된다.

1. 둘 중 낮은 선호 영화의 손실 악화가 1 percentile point 이내다.
2. 두 편 평균 선호는 최소 0.005 높다. 이는 다른 한 편이 그대로일 때 한 편을 1 percentile point 높이는 효과다.
3. 동시 신뢰구간의 반폭이 각 판단 margin 이하다.

최초 연속 통과 K를 `K-minimum`으로 정한다. K=30과 통계적으로 동등한 최초 K는 `K-plateau` 후보가 될 수 있지만, 반드시 먼저 K=0 대비 유용성을 통과해야 한다. 입력 표현별 최대 두 K, 전체 최대 여섯 조합만 Stage 2로 넘긴다. K=30은 실험 상한일 뿐 자동 후보가 아니다.

## 해석 경계

- `JUDGED20`은 관측 선호 판별 과제이지 full-catalog 검색 성능이 아니다.
- 사용자가 자유롭게 평가한 MovieLens 영화의 무작위 부분관측 proxy이지 실제 질문 UI·응답률 실험이 아니다.
- `COMMON30`은 평점을 많이 남긴 사용자 집단이다. 저활동 신규 사용자로 일반화하지 않고 cohort funnel과 활동량·가입시기·선호 entropy·인기도 친화 구간을 따로 보고한다.
- Final reserve도 022A의 Stage1/2로부터만 격리된 집합이다. 저장소의 과거 모든 분석으로부터 완전히 untouched라고 주장하지 않는다.
- Stage 2에서 선택 K만 사용해 여러 모델, 한국 제작/나머지, 출시시기, cold-item, TMDB 구조/E5 ablation을 검증한다.

## 실행 결과

아래 최초 실행 수치는 통계적으로 재현됐지만 결과 승인에 사용하지 않는다. 독립 구현 감사에서 CSV parser가 모든 역할의 열을 먼저 파싱한 뒤 filter했고 resume cache SHA 검증도 빠진 사실이 확인됐다. 최초 산출물은 `rec-ev-022a-invalid-prefilter-resume-boundary-20260905`에 보존했다. userId만 먼저 읽는 pre-filter와 SHA/run-signature 검증을 추가한 clean rerun이 완료되기 전까지 상태는 `INVALIDATED`다.

- `COMMON30`: 15,178명
- 공통 `I*`: 36,195편
- 사용자-셀: 2,792,752행, 사용자별 184행 고정, 중복 0
- simultaneous max-T: 10,000회, 700 contrasts, critical value `3.726432`
- 결론: 세 encoding 모두 `K-minimum` 없음, Stage 2 후보 0개

K30의 K0 대비 결과는 다음과 같다.

| 입력 | anchor | Δ Pair1 평균 선호 | Δ worst-item loss | 해석 |
| --- | --- | ---: | ---: | --- |
| 사용자 상대 강도 | ItemKNN | +0.0422 | -0.0571 | 개선 |
| 사용자 내부 순서 | ItemKNN | +0.0416 | -0.0563 | 개선 |
| 이진 부호 | ItemKNN | +0.0263 | -0.0391 | 개선 폭이 더 작음 |
| 사용자 상대 강도 | TMDB 구조 | -0.0510 | +0.0618 | B0보다 악화 |
| 사용자 내부 순서 | TMDB 구조 | -0.0523 | +0.0629 | B0보다 악화 |
| 이진 부호 | TMDB 구조 | -0.0645 | +0.0768 | B0보다 악화 |

빈 후보의 원인은 “K가 무의미해서”가 아니다. ItemKNN에서는 K와 연속/순서 정보가 관측 선호 판별을 분명히 개선했지만, 구조 콘텐츠 단독 scorer가 B0보다 약해 두 anchor 절대개선 Gate를 veto했다. 또한 700개 전체 family에서 K0 효용 contrast의 동시 CI 반폭은 최저 약 0.0058로 0.005 precision Gate보다 컸다.

따라서 022A 계약을 결과에 맞춰 완화하지 않는다. 이 결과는 `모델 절대 품질`과 `같은 모델 안에서 K가 추가하는 정보량`을 한 Gate에 묶으면 K·입력 선별이 약한 anchor의 품질에 종속된다는 실패 증거로 보존한다. 후속 022B는 아직 열지 않은 Stage2 사용자에서 두 질문을 분리해 확인한다.
