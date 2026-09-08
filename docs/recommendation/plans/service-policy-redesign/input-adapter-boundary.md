# 실제 온보딩 입력을 연결하기 전의 타입 경계

상태: DRAFT. 연구 명세와 합성 입력 검증이며 실제 서비스 adapter 구현/채택이 아니다. 2026-09-08.

**LIKE=+1을 원별점1.0으로 오해해 raw ALS에 넘기지 않도록 입력 종류부터 분리한다.**
숫자 범위 검사만으로는 이 오류를 막을 수 없다. 반별점·실제 이진 응답·MovieLens 이진 proxy는 출처와 타입을 함께 기록한다.

공식 연결은 Kafka 이벤트를 받은 Python Consumer가 PostgreSQL의 현재 값을 다시 읽는 방식이다.
대체된 internal.md의 FastAPI 초안을 구현하지 않는다. 이번 모듈은 DB/Kafka에 접속하지 않고 현재 snapshot을 인자로 받는다.
이 합성 경계는 event_version만 인자로 받으며 과거 이벤트의 평점 payload로 현재 snapshot을 덮어쓰지 않는다.
공식 이벤트에 다른 payload가 없다는 뜻은 아니다.

## 현재 snapshot 계약

- input_version:0이상 정수. 읽은 현재 snapshot이 이벤트 버전보다 오래되면 거부한다.
- id_namespace: TMDB 또는 MOVIELENS. adapter가 기대하는 namespace와 다르면 거부한다.
- origin: ACTUAL_FEELM, MOVIELENS_RAW_PROXY, MOVIELENS_BINARY_PROXY 중 하나.
- ratings: movie_id와 현재 원별점0.5~5.0·간격0.5. 숫자문자열·bool·반올림은 허용하지 않는다.
- onboarding: movie_id와 실제 LIKE/DISLIKE enum. SKIP·모름은 응답 행이 없는 상태다. 숫자1/-1은 저장 입력으로 받지 않는다.
- watch_states: 영화별 공식 RATED/WATCHED_UNRATED/LINK_CLICKED/EXPIRED. RATED와 현재ratings는 정확히 대응한다.

뜻이 불분명한 임의 필드를 받지 않는다. 숨긴 별점·정답·histogram·개인화 점수를 함께 넣으면 거부한다.
같은 종류 안의 영화 중복은 거부한다. ratings와 onboarding의 같은 영화는 허용하되 평점 하나를 유효 입력으로 우선한다.
온보딩 원응답은 별도 보존한다. 평점 삭제 뒤에는 남은 이진 응답이 복원되고 WATCHED_UNRATED/추천 제외는 유지된다.
고유 유효 영화 수는 기록하되 서로 다른 입력 방식의 정보량이 같다는 주장이나 K계산에 쓰지 않는다.

## 호출 경계

| 입력 packet | 처리 |
| --- | --- |
| RAW_RATING만 있음 | 지정한 raw-rating adapter에 원별점 tuple을 그대로 전달 |
| BINARY만 있음 | BINARY 전용 adapter가 주어진 경우에만 그 enum tuple을 전달. 없으면 UNRESOLVED_BINARY_RANKING |
| 두 종류 모두 있음 | UNRESOLVED_MIXED_RANKING. 임의 점수 평균/RRF/별점 환산을 하지 않음 |
| 둘 다 없음 | NO_INPUT. 이 모듈이 인기 정책·K·추천 슬롯을 결정하지 않음 |
| EXPIRED 존재 | EXPIRED_POLICY_UNRESOLVED. 재추천/제외를 이 모듈에서 임의 확정하지 않음 |

온보딩 응답 영화의 별도 연구 제외는 감상 경험/팝콘과 구분한다. 이 모듈은 입력 출처를 정규화하며,
신규 사용자/LIKE-only/혼합 입력에서 어떤 추천 모델이 가장 나은지는 선택하지 않는다.
현재 E5/B7의 signed profile 등 과거 proxy 연구가 있다는 이유로 실제 이진 adapter 품질 통과라고 선언하지 않는다.
특히 과거 REC019P 수축10/±0.15, REC021V 정확히10개·양쪽2개 조건은 현 온보딩 제약으로 상속하지 않는다.

## 완료라고 할 범위

현재값 우선·반별점 보존·enum 엄격 검사·중복 우선순위·삭제 복원·namespace/출처 구분·타입별 호출 차단을 합성 검증한다.
정규화 경로뿐 아니라 직접 만든 packet도 호출 직전에 다시 검증한다. 불변 tuple 구조·원천/버전 일치·현재 감상 상태·유효 개수까지 확인한다.
callback은 합성 spy이며 실제 ALS/콘텐츠 모델·PG/Kafka를 호출하지 않는다.
이진/혼합 랭킹 구현·품질·전환 K는 후속 계약에 남는다. 공식 API/DB/UI는 수정하지 않는다.
