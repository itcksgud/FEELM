# REC039 — 장르 기반 두 후보를 추천 입력으로 인계

상태: DRAFT — 개인 연구의 고정 실행 계약. 2026-09-08.

사용자의 ‘다음 ㄱㄱ’에 따라 REC038의 RULE_GENRE와 KM_GENRE를 학습 코드 없이 사용할 수 있는
추론 adapter와 장르 구성 계약으로 만든다. 새 모델 비교나 128편 재채점이 아니다.
전체 단계3의 마지막 인계 작업이며, 이번 종료점은 단계4가 같은 인터페이스로 두 후보를 읽을 수 있는 상태다.

## 입력과 변경하지 않을 배정

REC038 첫 seed20260909의 KM_GENRE raw centroid·raw_cluster_order, 고정 어휘/IDF,
같은85,517편 배정과 REC033 metadata/장르 규칙을 사용한다. config의 파일 bytes/SHA를 확인하고
REC038 숫자/평가 봉인과 독립 검토 PASS를 검증한다. 원본·과거 보고서·봉인은 수정하지 않는다.

기존 REC038 결과검토가 핀한 상위 문서는 이번 실행 전에 해시를 확인하여 outputs의 master-before에 복사한다.
그 뒤 상위 문서를 REC039로 최신화해도 과거 검토가 새 문서를 검토한 것처럼 이전 핀을 바꾸지 않는다.
완료 재사용은 당시 문서 복사본의 해시를 확인하며 현재 상위 문서가 계속 과거 버전이어야 한다고 요구하지 않는다.

## 배정 adapter 계약

패키지 ID REC039_FROM_REC038은 연구 파일 출처 표시다. 공식 서비스의 맛 모델 버전 정책을 도입하지 않는다.
입력은 순서가 보존된 양의 정수 장르 ID 목록이다. bool·실수·문자·0·음수·중첩·None은 거부한다.
중복은 첫 위치만 유지한다. 출력은 방법·연구용 그룹 코드·native label·지원 상태·미지 ID·형식 전용 여부다.
코드는 RULE_01~08, GENRE_KM_01~08이며 각 방법 안에서만 의미가 있다. 원시 label은 연구 내부 파일에만 둔다.
공식 API·맛 코드·표시명·색상은 만들거나 변경하지 않는다.

RULE_GENRE는 첫 내용 장르를 기존 REC033 규칙에 연결한다. TV Movie10770은 건너뛰며,
빈 목록은 NO_GENRE, TV Movie만 있으면 FORMAT_ONLY, 첫 내용 장르가 미지이면 UNMAPPED_GENRE다.
나중에 알려진 장르가 있어도 첫 미지 장르를 몰래 건너뛰지 않는다. 기존 순서 의존성을 유지한다.

KM_GENRE는 고정19좌표의 presence×IDF를 L2 정규화하고 저장 raw centroid와 Euclidean 제곱거리를 비교한다.
동률은 낮은 raw component를 택하고 저장 raw_cluster_order의 역매핑으로 native label에 연결한다.
centroid를 새로 정렬하거나 학습하지 않는다. 순서·중복은 결과에 영향을 주지 않는다.
빈 목록은 NO_GENRE, 아는 장르가 전혀 없으면 NO_KNOWN_GENRE로 미지원이다.
일부 미지 ID는 알려진 좌표로만 계산하되 unknown_genre_ids와 ASSIGNED_PARTIAL_VOCABULARY를 남긴다.
기존19어휘에는10770이 있으므로 TV Movie만 있어도 모델 배정은 가능하지만 format_only=true를 남긴다.
이를 내용 장르를 확보했다는 근거로 쓰지 않는다. 순수 TV12편의 기존 배정을 다른 그룹으로 보정하지 않는다.

미지원은 native=-1/code=null로 보존한다. REC038의 최빈 display 보충을 내보내지 않으며
단계4는 미지원 영화와 형식 전용 입력의 취급을 명시해야 한다. 추천 slot adapter는 이번에 실행하지 않는다.

## 새 산출물과 설명

- 독립 inference-only Python adapter와 봉인된 candidate-package.json.
- 두 방법×85,517편 native-assignments.parquet: movie_id,method,package_id,group_code,native_label,supported,status,format_only.
- 16그룹×19장르의 구성: 그룹 내 그 장르 보유 영화 수/그룹의 지원 영화 수, 전체 해당 장르 영화 수 및 그룹 밖 비율.
- 읽기용 구성표: 단순 규칙의 입력 기준과 각 그룹의 실제 상위 장르 비율. 서사 공통 본질이나 확정 맛명을 만들지 않는다.
- 계약 적용 예시: 기존 목록 일부와 무관한 고정 장르 조합/빈/미지/형식 전용 사례. 신규 영화 품질 표본이 아니다.

복수 장르 때문에 한 그룹의 장르 비율 합은100%를 넘을 수 있다. 한 영화가 여러 그룹으로 배정됐다는 뜻이 아니다.
native 그룹이8개임과 사람 취향이8가지로 잘 구분됨은 다른 주장이다. 구성비·대표성 지표로 우승이나 새 통과선을 정하지 않는다.

## 실행·검증·종료

독립 설계·구현 검토와 정확한 파일 fingerprint PASS 뒤 한 번 실행한다. 시간30분/메모리12GiB 상한,
py -3.12의 기존 NumPy 환경을 사용한다. 새 라이브러리·네트워크·평점·ALS·fit·임베딩 접근은 없다.
원천 봉인 검증 과정의 기존 파일 해시 읽기와 내용을 계산용으로 디코딩하는 일을 구분한다.

전수171,034배정을 REC038 native와 일치시킨다. 이는 adapter 적합성 확인이지 새 군집 성능 증거가 아니다.
장르 구성304행을 독립 집계한다. 입력 순서/중복, RULE 순서 의존, 빈/미지/TV-only/동률/잘못된 모델·입력을 검사한다.
필수 출력 누락·부분 실행을 자동 덮어쓰지 않고 완료 재실행은 핀만 확인한다.
최종 보고서는 독립 검토를 거친다. 연구 저장소에서 root만 파일을 수정한다.

다음 단계4는 같은 raw500·n30·개별 양수 관측 보존 규칙으로 새 후보의 공급을 별도 확인할 수 있다.
REC034의 B2180명 공급·154명 회복·회귀0은 기존 KM_TEXT 결과여서 새 KM_GENRE에 옮기지 않는다.
REC038의44/27편도 이번 장르 설명을 새로 채점한 점수가 아니다. 실제 사용자 품질·감상 전체 이력·최종8맛·K는 미정이다.
이번에는 adapter와 자료 인계까지 끝내고, 모델 탐색·설명 재채점·슬롯 실행을 자동 추가하지 않는다.

공식 근거: S15P21E106의 docs/adr/0015-kmeans-ctfidf-taste-clusters.md,
docs/api/taste.md의 API-044, docs/api/recommendation.md의 API-045.
연구 근거: [REC038 결과](../rec-ev-038/RESULT.md), [전체 순서](../../plans/service-policy-redesign/README.md).
