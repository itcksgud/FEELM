# 공개와 재현 경계

이번 추가 게시물은 사용자 승인에 따른 **마감 결정·익명 집계·파일 식별자**다.
공개 저장소만 clone하여 v26 학습/추론/비교를 재현할 수 있다고 주장하지 않는다.

기존 v12 소스와 테스트는 역사 자료로 유지한다. 최신 v26 코드의 import/템플릿/계획
의존성만으로도 수십 개의 추가 파일이 필요하고, 일부 계획은 사용자별 입력을 포함한다. 원본을 수정해
봉인을 깨거나 파일 전체를 무분별하게 공개하지 않았다. v26 실행 소스와 데이터는 로컬 보존이며
626의 GBT-only 운영 번들 준비에서 필요한 코드·특징·runtime을 별도로 인계해야 한다.

## 로컬에서 보존한 근거

| 논리 항목 | 원본 위치(연구 저장소 기준) |
| --- | --- |
| v26 학습 결과 | outputs/model-training/gbt-top2-v26-full-20260923 |
| 입력·파티 과거 구조 감사 | outputs/model-training/gbt-v26-closeout-20260923 |
| 예약 MovieLens 평가 | outputs/model-training/gbt-v26-reserved-eval-20260923 |
| ALS+콘텐츠 대조 | outputs/model-training/als-gbt-v26-comparison-r2-20260923 |
| 테스트 입력 정정 후 원순위 파티 | outputs/model-training/gbt-v26-party-raw-clean322-20260923 |

해시와 크기는 [ARTIFACT-INDEX.json](ARTIFACT-INDEX.json)을 따른다. 링크나 다운로드 권한을
뜻하지 않는다. 소스 revision·입력 snapshot·코드 SHA·설정·실패/검토 기록은 각 로컬 manifest와
관련 실험 문서에 남겼다. 이전328행 및 필터 적용 파티 결과도 당시 조건의 기록으로 보존한다.

## 전체 재실행에 필요한 비공개/별도 자료

- MovieLens 원자료·시간순 이력·사용자 split, v13 학습 행과 selector.
- 검증된 service↔TMDB↔MovieLens 연결과 KOBIS 증거, 카탈로그 snapshot.
- v12 변환기 vendor bundle, v14r2/v22/v24/v25의 해시 고정 중간 아티팩트.
- 권한 있는 FEELM 입력. 테스트6행은 실제 선호·감상 근거에서 분리한다.
- ALS 비교는 팀 Consumer 고정 revision과 별도195파일 번들이 추가로 필요하다.

원평가·개별 사용자/파티 결과·닉네임·영화별 입력 연결·생성 HTML·가중치·NPZ·Parquet·ZIP과
팀 소스는 이 공개 폴더에 복사하지 않는다. 외부 원자료의 이용 조건과 접근 권한 확인 없이
재배포하지 않는다. 과거 hash 대상 파일을 새 환경에 맞춰 조용히 재작성하지 않는다.

## 검증 범위

로컬 신규 raw 파티 합성시험10개, ALS 페이지 관련시험21개 및 상태별 계산 감사가 수행됐다.
기존 입력·예약 평가의 검토 기록은 로컬에 보존한다. 실제 브라우저 file URL 화면 검증은
보안 정책으로 미실시다. 해당 로컬시험이 GitHub CI에서 새로 실행됐다고 쓰지 않는다.
현재 PR의 기존 CI 성공은 이 문서 게시와 기존 저장소 검사 범위이며 운영 재현 승인이 아니다.
