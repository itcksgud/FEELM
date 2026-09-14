# S1 — 기존 라벨의 준비 상태 조사

상태: DRAFT / 실행 전 설계 검토 대기. 정답별점이나 모델 성능은 읽지 않는다.

## 입력

원본 repo `C:/higher/projects/FEELM-standalone`의 다음 파일을 정확한 SHA-256/바이트 수로 고정한다.

1. `outputs/recommendation-evidence/rec-ev-033/metadata.parquet`: movie_id, tmdb_id, keyword_ids/keyword_names, genre_ids/genre_names, 제목 및 원문 출처.
2. `outputs/recommendation-evidence/rec-ev-045/metadata.parquet`: movie_id, tmdb_id, 원장르, keyword_ids, original_language 및 연대 등 실제 schema에서 존재하는 필드.
3. `outputs/recommendation-evidence/text339/texts.parquet`: movie_id, tmdb_id, overview. 학습·분류 없이 본문 존재·중복·샘플만 조사한다.
4. `docs/recommendation/evidence/manifests/global-time-v1.json`: MovieLens ZIP의 정확한 경로와 기존 train_boundary를 확인한다. ZIP 내 tags.csv만 stream하여 카탈로그 ID 내 자료와 boundary 전/후 가용성을 분리한다. 이 조사에서 태그 기여자의 개인 기록은 출력하지 않는다.
5. 사람 의미 annotation의 조사 범위는 아래 목록으로 고정한다. 존재·출처·문서 설명 또는 schema만 확인하며, 이 목록 밖까지 gold 부재를 주장하지 않는다.
   - 원본 `docs/recommendation/experiments/rec-ev-038/README.md` 및 `outputs/recommendation-evidence/rec-ev-038/judgments.json`: AI 군집 판정, 의미 gold 제외.
   - 원본 `docs/recommendation/experiments/text339-clean/PLAN.md`: 에이전트 본문 삭제 판정의 설계, 의미 gold 제외.
   - 원본 `docs/recommendation/experiments/rec-ev-032/label-extension/README.md`: 실제별점/q 보충 정의, 의미 gold 제외. 실제 rating payload는 읽지 않는다.
   - 이번 worktree `docs/recommendation/experiments/discovery-classifier/semantic-label-registry.json` 및 `outputs/recommendation-evidence/discovery-classifier/semantic-annotations.parquet`: 아직 제공되지 않은 사람 의미 사전/주석의 명시적 수신 경로. 부재면 MISSING으로 기록한다. 존재만으로 승인하지 않고 author_type/human 검수 provenance·POS/NEG/UNKNOWN schema·scope를 별도 검토한다.

S1에는 기존 추천 평가별점·FM 예측·classifier 예측·새 군집 배정은 사용하지 않는다. 입력 파일이 실행 중 바뀌면 실패로 종료한다. 파일 누락/ID 불일치는 보고하고 조용히 교집합으로 줄이지 않는다.

## 조사 알고리즘

1. 85,517편이라는 기존 수치는 기대치로만 사용하고 실제 행 수/고유 movie_id/TMDB 값·정렬 연결을 확인한다. 동일 ID 중복/키워드 ID와 이름 배열 길이 불일치/한 ID의 여러 이름은 명시한다.
2. 키워드별 영화 지원량을 중복 영화×키워드 쌍을 제거해 계산한다. 정확한 이름별 빈도/동일 정규화 문자열 충돌을 기록한다. 정규화는 Unicode NFKC, casefold, 연속 공백 정리만 한다. 의미 동의어 병합·주제/인물/분위기 판단은 자동 수행하지 않는다.
3. 후보 **검수 큐**는 전체 카탈로그 기준 최소200편, 최대20% 지원을 갖는 키워드다. 이는 학습 vocabulary 확정이 아니다. 지원량 내림차순/keyword_id 오름차순 첫40개를 검수표로 출력하고 전체 후보표도 보존한다. 원키워드 유무는 SOURCE_POSITIVE / UNKNOWN으로 유지하며 검증된 NEGATIVE는 만들지 않는다.
4. 전체 overview를 위 NFKC/casefold/공백 정규화 후 SHA-256으로 묶는다. 동일 tmdb_id 또는 동일 비어있지 않은 본문 hash를 가진 영화들을 union-find 연결요소로 합친 뒤, 각 그룹의 최소 movie_id를 `discovery-classifier-s1|<min_movie_id>` hash mod100으로 train<70, validation70~84, test85~99에 배정하는 **분할 제안**을 만든다. 빈 본문은 ID/TMDB 그룹을 보존하되 모델 입력 불가로 표시한다. 동일 TMDB ID/본문이 여러 split에 없음을 확인한다. 제안은 태그 후보 전체분포를 조사한 exploratory split이며 아직 독립 test seal이 아니다. near-duplicate/번역/시리즈 분리는 여기서 증명하지 않는다.
5. 키워드별 split 지원량·언어별 분포와 source support를 저장한다. train>=100/validation>=30/test>=30을 충족하는지 기술하고, 이 조건만으로 의미 라벨 readiness가 PASS되지는 않는다. test별 지원량을 본 이 제안을 신규 미사용 확증 test로 승격하지 않는다.
6. MovieLens tags는 ZIP을 chunk로 읽고 동일 NFKC/casefold/공백만 정규화한다. 전체 및 기존 train_boundary 이전의 원행 수, 카탈로그 행 수, 고유 tag/영화/기여자 수, 상위 표현 지원량을 계산한다. keyword와 tag의 **정규화 문자열이 정확히 같은 경우만** 연결한다. 017의 별도 기여자 제외/500상한/구두점 정규화 결과와 같은 수치라고 주장하지 않는다. 의미가 같은 표현을 추정해 합치지 않는다.
7. 검수표에는 keyword ID/name, split별 source-positive 수, exact 자유태그 일치 지원량, 소수의 결정적 영화 ID/제목/overview 발췌 예시, 빈 `semantic_label_definition`, `decision`, `verified_positive/negative` 필드를 둔다. 인기도 순으로 좋은 예를 고르지 않고 movie_id의 고정 hash 순서로 3개 예시를 고른다. 이것은 사람 검수 입력이지 AI 정답이 아니다.

## 결과와 실행 한도

`outputs/recommendation-evidence/discovery-classifier/s1/`에 input-manifest.json, movie-split-proposal.parquet, keyword-support.csv, tag-support.csv, exact-source-overlap.csv, review-queue.csv/json, readiness.json, result.json을 신규 저장한다. 기존 출력이 있으면 덮어쓰지 않고 종료한다. report는 새 docs 실험 디렉터리에 root가 작성한다.

실행은 기존 Python3.12와 설치된 pandas/pyarrow/numpy를 사용한다. 워커1개, 모델/GPU 학습0회, 네트워크 호출0회. 원본 ZIP을 메모리에 한 번에 풀지 않는다. 프로세스20분/메모리4GiB를 사전 한도로 두고, 초과·오류 때 임의 표본축소 없이 실패와 잔여 출력을 보존한다. 원본 read-only 파일의 입력 SHA는 실행 전후 확인한다.

## 판정

- `S1_EXECUTION_PASS`: 지정 집계·ID·출처·분할 불변조건을 충족하고 독립 결과 검토 통과.
- `STANDARD_SUPERVISED_READY`: 사람이 정의·검수한 라벨사전, 출처·범위가 분명한 POS/NEG/UNKNOWN, train/validation/test의 검증된 양성·음성 지원, S2 상세 기준 및 코드 승인을 모두 갖춘 경우에만 가능. 현재 그러한 자료의 존재를 가정하지 않는다.
- 하나라도 없으면 `BLOCKED_LABEL_READINESS`; 특히 source positive가 많더라도 negative가 없으면 학습하지 않는다. 전체 실험 완료로 표현하지 않는다.

S1 코드 작성은 설계 PASS 후, 실제 데이터 집계 실행은 코드 독립검토 PASS 후 시작한다. 합성 fixture 검증도 코드 검토 후 실행한다. reviewer가 승인한 정확한 문서/설정/코드 해시를 runner가 확인하도록 구현한다.
