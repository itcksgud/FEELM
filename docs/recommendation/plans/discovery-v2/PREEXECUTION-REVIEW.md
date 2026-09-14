# 후속 실행 명세 읽기 검토

상태: DRAFT — 2026-09-13. 최초 CHANGES_REQUESTED 이후 아래 범위 한정 재검토를 완료했다. 전체 실행/결과 PASS가 아니다.

검토 대상: 별도 실행 worktree `C:/higher/projects/FEELM-standalone/.codex-tmp/fixed-k8-discovery-v2-20260913`.
최초 읽은 EXECUTION SHA256: `630db5dabf998eef58d40666a76aee5d2631b7b307329c7c748c30cbdbb8caa5`.
config SHA256: `e670cfd2ec889e9eb51b4b23e8b572b8b54e3706b6231098a28ecbb929d4922e`.
코드는 작성 중이므로 이 기록이 후속 해시의 통과를 의미하지 않는다.

## 확인한 방향

상위8 보존, 덜 시청한 세부 그룹 E_u 선제제약, 전역 보충0, TMDb m300 주정렬/100·300·1000 민감도,
동일 Q prefix 전수 비교, 개인화 무효 상태 분리, ML NDCG 참고 범위와 유한 수정회차의 방향은 사용자 지시와 일치한다.
기술 gate는 현지 연구의 명시적 허용치이며 만족도 기준이 아니다. 실제 집계/선정코드는 별도 검토가 남는다.

## 필수 수정 요청

1. **재사용 보정계수와 확인 사용자 중복.** hybrid345는 final344/roles.csv의 calibration90명으로 보정했다.
   v1의 validation90/verification180을 v2에 재사용하면 기존 보정 사용자와 각각33명/57명이 겹친다.
   주 검토자는 CSV/JSON의 사용자 ID만 교집합 계산했고 목표 평점은 열지 않았다.
   실행 전 역할을 기존 calibration90/comparison180에 맞추거나 직접 목표중복 사용자를 독립 확인에서 분리해야 한다.
   기존 개발자료 재사용이라는 표시는 계속 유지한다. 기존 보정이 목표를 사용했는데 'future target이 fitted calibration에 들어가지 않았다'고 쓰면 안 된다.
2. **G-only 벡터 공간 연결.** state의 G 사용자 벡터와 hierarchy 대표의 공간을 명시적으로 맞춰야 한다.
   현재 동일2차원 x/genre fixture는 GKT/G 혼용을 검출하지 못한다. 서로 다른 차원 및 공간 식별 검사가 필요하다.
3. **DIRECT-PREFIX 계산량.** 최초 검토본은 mean/top5용 prefix 내적을 두 번 계산하지만 한 번만 집계했다.
   내적 공유 또는 실제 횟수 기록이 필요하다. DIRECT-PREFIX의 순위역전/동점/동일 quota/B 사례도 검증한다.
4. **시청 기록의 distinct 계약.** viewed의 중복을 허용하면 그룹 횟수와 분모가 달라진다. 경계에서 검증하고 중복 사례를 둔다.

모든 발견사항을 실행 소유 작업에 전달했다. 원본/새 실행 코드의 직접 수정과 실제 추론은 주 검토자가 수행하지 않았다.

## 자료

- 기존 보정 사용자: `C:/higher/projects/FEELM-standalone/outputs/recommendation-evidence/final344/roles.csv`.
- 보정 출처 선언: `C:/higher/projects/FEELM-standalone/docs/recommendation/experiments/hybrid345/config.json`, `sources.roles`.
- 비교 역할: `C:/higher/projects/FEELM-standalone/.codex-tmp/fixed-k8-discovery-20260912/docs/recommendation/experiments/fixed-k8-discovery/selection.json`.
- 코드 읽기 검토: Hooke, 주 에이전트. 실행 이후의 분모·gate·선정·최종순위 수치 검토는 아직 수행하지 않았다.

## 수정본 재검토 — 범위 한정 PASS

주 에이전트가 수정본을 읽고 검색 합성검사12개를 직접 실행해 모두 통과했다. 기존 코드/원본 결과를 수정하거나 실제 영화 예측을 실행하지 않았다.
명령: text339-runtime/venv/Scripts/python.exe -B -m unittest discover -s scripts -p test_dv2_retrieve.py -v (실행 worktree에서).

- EXECUTION과 dv2_prepare가 final344 calibration90→selection, comparison180→check로 정렬하며 출처 해시/역할수/불일치57명 이력을 검증한다.
- 기존 보정 목표 사용과 개발자료 재사용을 명시했다. r1은 입력 준비만 수행한59.04초 실행으로 보존하고 r2에서 준비를 다시 수행한다는 명세를 확인했다.
- G 비교안이 genre_mean을 명시 선택하고 차원 일치를 검사한다. 서로 다른 G/GKT 차원의 합성검사를 통과했다.
- DIRECT-PREFIX의 내적을 재사용하며 순위역전·동점·계산량 합성검사를 통과했다.
- viewed 중복을 거부하며 E_u-first/관람 제외/부족반환/전역보충0 관련 합성검사를 통과했다.

검사 전후 확인한 해시:

| 파일 | SHA256 |
| --- | --- |
| EXECUTION.md | 4f7e4aebf28701d01d77e33457e4b2df9a541e46eef8e610fe7f124ae5c1d93f |
| config.json | 20e624a77ac73c2334eb1730f9df8cee3bfbc305d592b07000b0dd178b509103 |
| dv2_retrieve.py | 840e594b19a395b61b57859d126cbb38810e2c1b1853f1f60fca243c2ae37215 |
| test_dv2_retrieve.py | 75a0b3e98525e378ba07ac18e47481d6793c257294f3ab7865426741a322c85b |
| dv2_common.py | 7075f75930aea0a71008046cb94ae57e652d15d082340e02929476828509df30 |
| dv2_prepare.py (역할 연결 구간 검토) | 384cb2210486ff2389628caf87cb606e78911f85d6251d656a3ca8f71d352590 |

이 PASS는 목표 일치·역할 연결 교정·검색 코어와 해당 합성검사에 한정한다. 전체 입력 준비/클러스터/실제 모델 추론/선정·집계코드/실제 결과의 독립 검토는 실행 작업에서 별도로 완료해야 한다.
