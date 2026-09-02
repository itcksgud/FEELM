# REC-EV-019B TMDB 특징 100편 사전검사

> 상태: `PASS_PREFLIGHT`
> 범위: 실행 안전성 확인용 100편 표본이며 전체 69,603편의 최종 coverage 결과는 아니다.
> 제품 영향: 없음. 현재 추천은 `APPROVED_C2A_INTERNAL_POPULARITY_ONLY`를 유지한다.

## 무엇을 확인했나

MovieLens는 사용자가 어떤 영화에 어떤 평점을 남겼는지 알려 주지만 영화 설명·감독·배우·키워드는
충분하지 않다. 이 작업은 MovieLens를 사용자 행동에만 쓰고, 영화 정보는 TMDB에서 받기 위한 준비다.

전체 실행 전에 Base 역할 사용자의 넓은 영화 특성 집합 중 TMDB 링크가 있는 100편을 고정 hash 순서로 뽑아 다음을
실제로 확인했다.

- TMDB 한국어 상세·크레딧·키워드 호출
- 한국어 제목 또는 줄거리가 비면 영어 응답으로 보충
- MovieLens의 TMDB·IMDb 연결값과 실제 응답 identity 대조
- 중단 후 재실행할 때 성공 응답 cache 재사용
- 구조 특징과 384차원 다국어 E5 embedding 생성
- TMDB popularity·평균 평점·평가 수·매출·예산을 취향 특징에서 제외
- API credential이 cache·Parquet·manifest에 남지 않는지 검사

## 결과

| 검사 | 결과 | 판정 |
| --- | ---: | --- |
| 고정 표본 | 100편 | PASS |
| identity 자동 확인 | 99편 / 100편 | 99.0%, 기준 98% 이상 |
| 구조 특징 사용 가능 | 99편 / 99편 | 100%, 기준 95% 이상 |
| 텍스트 특징 사용 가능 | 99편 / 99편 | 100%, 기준 95% 이상 |
| embedding 차원 | 384 | PASS |
| embedding L2 norm | 0.99999988~1.00000012 | 허용 오차 ±0.0001 이내 |
| 중복 영화 행 | 0 | PASS |
| credential 노출 | 0 | PASS |

격리된 1편(`movieId=186127`)은 TMDB 상세 응답의 IMDb ID가 MovieLens 연결값과 달랐다. 어느 쪽이
맞다고 임의로 정하지 않고 `IDENTITY_REVIEW_REQUIRED / IMDB_ID_MISMATCH`로 분리했다. 이 격리는 추천
성능 저하가 아니라 서로 다른 영화를 같은 영화로 학습시키는 오류를 막는 안전장치다.

## 실행 중 발견하고 고친 문제

첫 실행은 100편 모두 HTTP 401이었다. 로컬 credential은 JWT Read Access Token이 아니라 32자 v3 API
key였는데 Bearer header로만 전송했기 때문이다. runner가 두 형식을 자동 구분하도록 고쳤고, 전송용
`api_key`도 cache의 request parameter에는 기록하지 않도록 했다.

또한 첫 실패 결과가 “파일 형식은 맞다”는 이유만으로 verifier를 통과하는 결함을 발견했다. 사전검사에도
identity 98%, 구조 95%, 텍스트 95%의 health threshold를 적용해 0% 결과는 반드시 실패하게 바꿨다.

ONNX 모델은 `intfloat/multilingual-e5-small`의 고정 revision과 파일 SHA-256을 함께 검사한다. 모델이
요구하지만 tokenizer가 내지 않는 `token_type_ids`는 0으로 명시해 runtime 입력 차이도 제거했다.

## 이 결과가 뜻하는 것

이제 TMDB 특징 생성 코드는 100편 범위에서 실제 API·cache·identity·결측·embedding까지 작동한다.
그러나 100편이 잘 됐다고 전체 coverage가 통과했다고 말할 수는 없다. 이후 넓은 특성 집합 69,603편을
같은 코드로 실행했고 전체 Gate도 통과했다. 이 69,603편은 미래 시점 영화가 포함돼 추천 후보 권한은 없다.
실제 후보는 REC-EV-019A의 cutoff-safe 집합과 신원 확인 결과의 교집합을 사용한다.

Locked Test와 추천 모델 비교는 아직 열지 않는다. REC-EV-019C 실행 계약을 먼저 고정한 뒤 Validation에서
인기도 기준선과 한 개의 개인화 후보를 비교한다.

## 재현 명령

```powershell
py -3 -m pip install -r requirements-data.txt
py -3 -m pip install -r requirements-ml.txt
py -3 -m unittest scripts/tests/test_build_rec_ev_019b_features.py
py -3 scripts/build_rec_ev_019b_features.py --contract docs/recommendation/contracts/rec-ev-019b-artifacts.json --preflight --limit 100 --resume
py -3 scripts/verify_rec_ev_019b_features.py --manifest docs/recommendation/evidence/manifests/rec-ev-019b-preflight.json --preflight
```

대용량 cache와 Parquet은 Git에 넣지 않는다. 추적되는 manifest에는 파일 크기와 SHA-256만 남긴다.
