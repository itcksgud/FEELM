# REC-EV-019C — runner 합성 검사와 LightFM 의존성 검사

> 상태: `SYNTHETIC_AND_DEPENDENCY_PREFLIGHT_PASS`
> 실행일: 2026-09-02
> 현재 허용: 실제 Validation runner 구현과 실행 승인 검토
> 현재 금지: 실제 Validation 학습·점수 계산, Locked Test 열람, champion·제품 정책 변경

## 한 줄 결론

작은 가짜 데이터에서 runner 안전장치 15개를 통과했고, 고정 Linux Docker 환경에서 LightFM 의존성
안전장치 9개를 통과했다. 이 결과는 추천 성능이 좋다는 뜻이 아니다. 이제 실제 데이터 runner를 구현해도
된다는 뜻이며, 실제 Validation 실행은 구현 검토 뒤 별도로 열어야 한다.

## 합성 runner 검사 결과

| 검사 묶음 | 결과 | 확인한 내용 |
| --- | --- | --- |
| 입력 파일 방화벽 | PASS | 허용 파일만 통과, 혼합 역할·Test·미등록 파일은 open 전 거부 |
| 동일 후보 | PASS | 모델 특징이 없어도 후보 유지 |
| 본 영화 제외 | PASS | K prefix 영화만 점수 계산 전 제외 |
| positive injection | PASS | 평가용 positive를 상단에 강제로 넣지 않음 |
| 동점 | PASS | 점수 내림차순 뒤 movie ID 오름차순 |
| 결측 fallback | PASS | 특징 없는 영화는 B0 percentile 사용 |
| RRF | PASS | 서로 다른 원점수가 아니라 rank만 결합 |
| trial | PASS | B0 3, B2 9, B4 4, B6 4, B7 1, B8 4, B9 9 |
| checkpoint | PASS | 같은 signature는 byte-equivalent 재개, 다른 hash는 기존 파일 보존 후 거부 |
| 실행 경계 | PASS | 실제 Validation과 Locked Test는 열지 않음 |

합성 fixture는 8편뿐이다. 따라서 위 PASS는 코드 의미와 실패 방식을 검증할 뿐 모델 품질이나 처리 시간을
대표하지 않는다.

## LightFM 설계를 실행 전에 고친 이유

처음 계약은 B8 LightFM에서 `bpr`와 `warp`를 비교하려 했다. 하지만 LightFM 공식 문서는 두 loss가
positive-only implicit feedback용이며, 관측하지 않은 항목을 negative로 샘플링한다고 설명한다. 이는
“미평가는 싫어요가 아니다”와 “negative는 실제 DISLIKE만 사용한다”는 FEELM 계약과 충돌한다.
[LightFM loss 설명](https://making.lyst.com/lightfm/docs/lightfm.html),
[WARP 동작 설명](https://making.lyst.com/lightfm/docs/examples/warp_loss.html)

그래서 실제 결과를 보기 전에 B8을 다음처럼 교정했다.

- Base 학습: 관측 LIKE는 `+1`, 관측 DISLIKE는 `-1`인 `logistic` loss
- sample weight: label 부호가 아니라 0 이상 confidence만 사용
- 미평가 영화: 학습 negative로 만들지 않음
- 신규 사용자: 학습된 LightFM item·content 표현을 고정하고 사용자 vector만 별도 logistic fold-in
- 비교 조합: dimension 64/128 × adagrad/adadelta, 총 4개

LightFM 문서도 logistic loss는 positive와 negative가 함께 있는 경우에 사용한다고 명시한다.
[LightFM API](https://making.lyst.com/lightfm/docs/lightfm.html)

## 패키지 선택과 재현 환경

원본 `lightfm 1.17`은 2023년 source distribution만 제공하며 Python 3.12 설치 문제가 보고돼 있다.
[원본 PyPI](https://pypi.org/project/lightfm/),
[Python 3.12 이슈](https://github.com/lyst/lightfm/issues/709)

실험 전용으로 다음을 잠갔다.

| 항목 | 값 |
| --- | --- |
| distribution | `lightfm-next==1.19.0` |
| import | `lightfm` |
| 실행 환경 | CPython 3.12 / Linux amd64 |
| 이미지 | `python:3.12.5-slim-bookworm` + 고정 digest |
| LightFM wheel SHA-256 | `84d81163ef06b21a90d28596417e81422460a16925cd2bd6143b34da9146c12f` |
| upstream commit | `82b74a8b78ea51b1793e89fbac2604608224e52d` |
| native Windows | 지원 대상으로 두지 않음 |

`lightfm-next 1.19.0`은 CPython 3.12 Linux wheel, PyPI Trusted Publishing provenance와 해당 source commit을
제공한다. 내려받은 wheel의 hash도 PyPI 공개 hash와 일치했다.
[lightfm-next PyPI 파일·provenance](https://pypi.org/project/lightfm-next/)

## 실제 Linux smoke 결과

고정 이미지에서 hash lock만 사용해 12개 패키지를 설치한 뒤 다음을 실제 실행했다.

- `lightfm-next==1.19.0` import
- 3명×5편의 관측 LIKE/DISLIKE `logistic` 학습
- 영화 identity+메타데이터 feature 결합
- 모든 예측값 finite 확인
- item 표현 checksum을 fold-in 전후 비교해 불변 확인
- 관측하지 않은 영화를 negative로 생성하지 않았음을 확인
- BPR/WARP가 실행되지 않았음을 확인

9개 검사가 모두 PASS였고 실제 Validation·Locked Test는 열지 않았다.

## 현재 판정

| 질문 | 판정 |
| --- | --- |
| runner helper와 안전장치를 실제 데이터 runner에 사용할 수 있는가? | GO |
| LightFM 패키지를 고정 Linux 환경에서 사용할 수 있는가? | GO |
| B8에서 BPR/WARP를 사용할 수 있는가? | NO — UNKNOWN negative 계약과 충돌 |
| 실제 Validation을 지금 실행해도 되는가? | 아직 NO — 실제 runner 구현·검토 필요 |
| Locked Test를 열 수 있는가? | NO |
| 제품 champion을 바꿀 수 있는가? | NO |

## 재현

합성 검사:

```powershell
npm run recommendation:019c:synthetic:run
npm run recommendation:019c:synthetic:check
```

Linux 의존성 smoke 결과 검증:

```powershell
npm run recommendation:019c:dependency:run
npm run recommendation:019c:dependency:check
```

추적 근거:

- `docs/recommendation/evidence/results/rec-ev-019c-synthetic-preflight.json`
- `docs/recommendation/evidence/manifests/rec-ev-019c-synthetic-preflight.json`
- `docs/recommendation/evidence/results/rec-ev-019c-lightfm-linux-smoke.json`
- `docs/recommendation/evidence/manifests/rec-ev-019c-lightfm-linux-smoke.json`
- `requirements-rec-ev-019c.lock`
