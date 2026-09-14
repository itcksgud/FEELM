# 서비스 추천 v1 r4 실행 판정

상태: **SUPERSEDED — 실행 금지, 기록 보존**  
판정일: 2026-09-14  
후속 계획: [서비스 추천 v1 r5 2노드 Spark/HDFS 계획](service-v1-r5-two-node-spark-hdfs.md)

## 판정

`service-v1-b1-r4-server-fit-recovery.md`와 그 코드·테스트·런타임 파일은 단일 서버 장애
복구 연구 기록으로 보존한다. 해당 계획으로 delivery를 만들거나 서버에 전송하거나 8시간
fit·score·evaluation을 실행하지 않는다.

중단 근거는 다음과 같다.

1. r4는 `local[5]`, 단일 Docker, 로컬 파일 입출력을 전제로 한다. 팀의 채택된 ADR은
   2노드 Spark Standalone과 HDFS/Parquet 분산 처리를 요구한다.
2. 팀의 현재 Spark 클러스터는 Worker 2개가 각각 2 Core/4 GiB를 광고하고 공식 ML 이미지는
   Spark 4.1.3, Python 3.10.12, NumPy 2.2.6이다. r4는 단일 5 Core/20 GiB와 별도 Python 3.12,
   NumPy 1.26.4 이미지를 전제로 한다.
3. r4 runner의 `local[5]`와 공유 worker의 `local[4]` 강제가 충돌한다. 실행 전 SparkSession
   검증에서 실패한다.
4. r4가 고정한 팀 revision은 `96a4b27d0c...`이고, 마지막으로 로컬에 fetch되어 있는
   `origin/develop`은 `0f0983c691...`이다. 현재 gate는 revision 불일치로 실패한다.
5. r4의 B1은 230개 특징을 쓰는 개발 비교 모델이다. 서비스 v1 목표 B3 245개 특징은 KOBIS
   연결·row-lineage·입력 검증이 끝나지 않아 아직 만들 수 없다.
6. 팀의 채택 ADR은 현재 ALS+콘텐츠 Hybrid다. 로컬 v1의 GBT 기본안은 팀 계약을 자동으로
   바꾸지 않으므로 B1 성공을 서비스 활성화로 해석할 수 없다.

## 보존 범위

- r3 실패와 r4 설계·구현·테스트는 당시 단일 서버 복구 시도의 감사 자료로 유지한다.
- 기존 파일의 frozen hash나 과거 결과를 고쳐 새 계획처럼 만들지 않는다.
- r4 run ID, delivery/receipt/publication 이름을 r5에서 재사용하지 않는다.
- r4 결과가 없으므로 Jira 완료, 추천 v1 완료, 운영 모델 생성으로 기록하지 않는다.

## 다시 사용할 수 있는 것

r4의 입력 해시 확인, no-overwrite, 단계별 실패 봉인, evaluator의 지표 정의와 자원 관측 항목은
r5 설계의 참고 자료로만 재사용한다. 실행 master, 이미지, 자원, 입력·출력 URI, 배포·게시 경계,
모델 권위와 완료 조건은 r5에서 새로 정의한다.
