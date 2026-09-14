# 추천 연구 안내

> 상태: `APPROVED` — 기존 연구 기록·채택 Gate·제품 연결 규칙을 유지한다. 2026-09-12 변경은 읽기 안내와 중복 없는 파일 표현에 한정한다.

현재 결론은 [현재 연구](active-experiment.md)에서 읽는다. 개인 연구와 제품의 승인 계약을 구분한다.
이 문서는 2026-09-12 문서 정리 요청에 따라 읽기 순서와 기록 형식을 정리했다. 기존 채택 Gate·제품 계약·실험 수치는 바꾸지 않았다.

## 작업별 읽기

- 현재 선정안 설명: 현재 연구 → 필요한 경우 [선정 근거](experiments/hybrid345/PRESENTATION-DECISION.md).
- 새 실험·실험 코드·실행·검토: [연구 절차](research-workflow.md) → [현재 범위](analysis-scope.md) → 해당 설계·config·독립 사전 검토.
- 제품 연결: [제품 결정](product-decisions-required.md), [서빙 계약](serving-contract.md), 변경 대상 API·DB·인수 기준.
- 이전 실험 추적: [진행 이력](active-experiment-history-20260912.md), [과거 기록 지도](record-system-history-20260912.md). 새 작업마다 전체 이력을 읽지 않는다.

## 실험을 기록하는 방법

실험당 [한 페이지 양식](experiment-summary-template.md)으로 질문·결론·핵심 수치·한계·다음 행동을 설명한다.
완료 run의 원본을 덮어쓰지 않는다. 새로운 설명은 요약에 쓰고 봉인된 근거로 연결한다.

| 반드시 남길 정보 | 파일 예시 또는 기존 위치 |
| --- | --- |
| 가설·기준선·고정 조건·판정/중단 기준 | `DESIGN.md` 또는 기존 설계 계약 |
| code/data/split/candidate/seed/parameter 버전 | `config.json`, `run.yaml` 또는 manifest |
| 전체·구간 지표, 분모, 차이·신뢰구간 | 원본 JSON/CSV + 결과 요약 |
| 관찰·해석·반례·trade-off·채택/기각/보류 | `RESULT.md` 또는 기존 비교·통찰 문서 |
| artifact 위치·checksum·공유 범위·재현 명령 | manifest와 해당 runbook 절 |
| 설계·코드 사전 검토 / 결과 독립 검토 | 검토 기록의 경로와 검토한 버전 |

표는 필요한 **정보의 역할**이다. 이미 역할을 충족하는 파일이 있으면 같은 내용을 다른 이름으로 만들지 않는다.
대용량 모델·Parquet은 Git에 넣지 않는다. 실패도 보존한다. 위치·해시만 있고 자료가 공유되지 않았다면 제3자 재현 완료라고 쓰지 않는다.

## 해석·채택 기준

- 같은 split·candidate·seed와 강한 기준선으로 비교한다. 대표 지표 차이와 95% paired 신뢰구간을 기록하며, 개별 실험이 사전에 고정한 다중 비교 등 조건도 유지한다.
- binary 온보딩 `K_b=0/5/10`과 활성 Rating `K_r=0/1/3/5/10/20/30/50`을 구분한다. 별점 오차와 순위 품질을 한 숫자로 합치지 않는다.
- 평균뿐 아니라 사용자·영화 구간과 정확도·탐험성·coverage·latency·비용의 악화를 기록한다. 파티는 최저 구성원·불만 비율도 본다.
- 탐험 정책은 승인된 관련성 손실 예산을 따른다. Test 결과를 본 뒤 Gate나 예산을 바꾸지 않는다.
- 채택 record에는 이전 champion·rollback 대상과 근거를 남긴다. 미평가·미노출은 실패가 아니다.
- 관찰과 원인 해석을 구분한다. 오프라인 결과는 실제 만족도 증명이 아니며 raw 고평점 비율을 만족도 KPI로 바꾸지 않는다.
- 제품 노출의 version·입력·후보·예상 별점 snapshot 및 후속 행동 연결 규칙은 [서빙 계약](serving-contract.md)과 [기존 기록 체계의 제품 결과 연결](record-system-history-20260912.md#6-제품-결과-연결)을 유지한다.
