# REC-EV-019C — 모델 비교 실행 계약 준비 결과

> 상태: `CONTRACT_AND_PREFLIGHT_PASS`
> 현재 허용: 실제 Validation runner 구현과 실행 승인 검토
> 현재 금지: 실제 Validation 학습·점수 계산, Locked Test 열람, champion·제품 정책 변경

## 한 줄 결론

모델을 돌리기 전에 “어떤 데이터로 무엇을 몇 번 비교하고, 중단되면 어떻게 이어서 돌릴지”를 JSON 계약과
자동 검증기로 고정했다. runner helper의 합성 검사 15개와 LightFM Linux 의존성 검사 9개도 통과했다.
다음 작업은 실제 Validation runner 구현과 실행 승인 검토다.

## 왜 바로 모델을 돌리지 않았나

019A를 완료한 뒤에도 두 문제가 남아 있었다.

1. Validation과 Locked Test가 같은 Parquet에 들어 있어 Validation 코드가 Test 행을 물리적으로 읽을 수 있었다.
2. 모델 이름과 큰 탐색 범위만 있었고, 점수 결합·fallback·체크포인트·선택 순서가 정확히 고정되지 않았다.

첫 문제는 019A에 역할별 파일을 추가해 해결했다. 앞으로 Validation runner는 다음 두 파일만 읽는다.

- `validation-binary-prefixes.parquet`
- `validation-evaluation-windows.parquet`

전체 역할 혼합 파일, Router 파일, Locked Test 파일, 원본 `test.parquet`은 입력 allowlist 밖이라 파일을 열기
전에 실패해야 한다.

## 고정한 공통 비교 조건

| 항목 | 고정 값 |
| --- | --- |
| 실제 공통 후보 | 41,625편 |
| Validation 사용자 | K0 1,674명 / K5 1,614명 / K10 1,479명 |
| 사용자 입력 | 최초 관측 binary proxy, K0·K5·K10 |
| 추천 깊이 | 전체 후보 scan → Top-500 → Top-10 |
| 노출 진단 | Top-2 Harm·Miss·BothGood·SafeHit |
| primary | 사용자 동일가중 NDCG@10 |
| 사용자에게 이미 보인 영화 | 해당 K prefix에 들어 있는 영화만 제외 |
| 미평가·중립 | negative로 만들지 않음 |
| 모델 특징이 없는 영화 | 후보에서 제거하지 않고 B0 fallback |
| 동점 | 유효 점수 내림차순 → MovieLens movie ID 오름차순 |

69,603편 TMDB 특징 집합은 후보 권한이 없다. 019A의 cutoff-safe 42,123편과 019B 신원 allowlist를 합친
41,625편만 모든 모델이 같이 사용한다.

## 비교 모델과 탐색 횟수

| 모델 | 용도 | 고정 trial 수 |
| --- | --- | ---: |
| B0 Bayesian rating | 비개인화 기준선·fallback | 3 |
| B2 signed binary ItemKNN | 설명 가능한 공동 선호 | 9 |
| B4 observed like/dislike BPR | pairwise 개인화 | 4 |
| B6 TMDB structured content | 장르·언어·연대·인물·키워드 | 4 |
| B7 TMDB text content | 고정 E5 embedding | 1 |
| B8 LightFM hybrid | 관측 LIKE/DISLIKE logistic + TMDB 특징 | 4 |
| B9 RRF | CF·Content·전체 head 순위 결합 | 9 |

모델당 최대 30회보다 모두 작다. 확률 모델은 고정 seed 5개를 trial 안의 반복으로 사용한다. seed를 다른
hyperparameter처럼 골라 좋은 결과만 선택하지 않는다.

BPR은 미평가 영화를 가짜 negative로 뽑지 않고 실제 LIKE와 실제 DISLIKE 쌍만 사용한다. LightFM의
BPR/WARP는 미관측 항목을 negative로 샘플링해 이 규칙과 충돌하므로 B8에는 쓰지 않는다. B8은 관측된
LIKE/DISLIKE만 `+1/-1`로 넣는 logistic loss와 frozen-item 사용자 fold-in을 쓴다. Linux/amd64용
`lightfm-next==1.19.0`과 모든 dependency wheel hash를 별도 lock에 고정하고 실제 smoke를 통과했다.

## 점수와 fallback

ALS·BPR·cosine처럼 단위가 다른 원점수를 직접 더하지 않는다. 한 모델 안에서 사용 가능한 영화의 점수를
사용자별 순위 percentile로 바꾸고, 해당 모델 특징이 없는 영화만 B0 percentile을 사용한다. RRF도 원점수가
아니라 각 head의 순위만 결합한다.

모델 하나가 실패하거나 필수 dependency가 없다고 몰래 건너뛸 수 없다. trial 오류를 기록하고 전체 선택을
막는다. K0는 개인 입력이 없으므로 B0만 선택 가능하다.

## 중단·재개 계약

- 사용자 최대 64명, 후보 최대 4,096편 단위로 나눠 점수를 계산한다.
- 전체 사용자×영화 점수 행렬은 파일로 저장하지 않는다.
- trial·seed·사용자 batch마다 checkpoint를 남긴다.
- 계약·입력·모델·trial·seed hash가 모두 같을 때만 재개한다.
- hash가 다르면 기존 결과를 삭제하지 않고 재사용을 거부한다.
- trial을 모두 마친 뒤에만 Validation 선택 파일을 만들 수 있다.
- Test 파일은 Validation 선택 lock이 있어도 이 task에서 열 수 없다.

## 현재 판정

| 질문 | 판정 |
| --- | --- |
| 계약이 기계적으로 검증되는가? | PASS |
| runner helper 합성 preflight가 통과했는가? | PASS — 15개 검사 |
| LightFM Linux 의존성 smoke가 통과했는가? | PASS — 9개 검사 |
| 실제 Validation runner 구현을 시작할 수 있는가? | GO |
| 실제 Validation 모델을 지금 돌릴 수 있는가? | NO |
| Locked Test를 열 수 있는가? | NO |
| 개인화 모델을 서비스에 채택할 수 있는가? | NO |

다음 Gate는 실제 runner가 역할별 Validation 파일만 읽고 41,625편을 block scan하며 trial별 checkpoint를
남기는지 코드 검토로 확인하는 것이다. 그 뒤에만 실제 Validation 실행 승인 여부를 다시 판단한다.

## 검증 명령

```powershell
py -3 scripts/validate_rec_ev_019c_contract.py
py -3 -m unittest scripts/tests/test_validate_rec_ev_019c_contract.py
npm run recommendation:019c:synthetic:check
npm run recommendation:019c:dependency:check
```

이 명령은 모델을 학습하거나 실제 Validation·Locked Test 평점을 읽지 않는다.
