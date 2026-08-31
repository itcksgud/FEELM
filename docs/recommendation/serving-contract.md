# FEELM 추천 Serving 계약

> 문서 상태: `APPROVED` — 현재 경로 보호와 vNext 승격 조건의 기준이며 vNext public API 승인은 아니다.
> 개정일: 2026-08-30
> 현재 운영 기준: `APPROVED_C2A_INTERNAL_POPULARITY_ONLY`
> 주의: 이 문서는 vNext 승격 조건과 경계를 정의하지만 OpenAPI·DB·현재 구현을 변경하지 않는다.

## 1. 현재와 vNext의 경계

| 항목 | 현재 승인 경로 | vNext 후보 |
| --- | --- | --- |
| 개인 ranking | Bayesian popularity | binary/rating별 검증된 모델 또는 RRF |
| 온보딩 LIKE/DISLIKE | C2 개인화 입력에서 제외 | binary head 입력 |
| 활성 Rating | 별도 데이터이나 현재 ranking에 미반영 | rating head/ALS fold-in 입력 |
| 예상 별점 | `null` | 별도 gate를 통과한 `K_r` 구간만 |
| 이유 | popularity 근거 또는 비노출 | 실제 score component allowlist |
| fallback | popularity | 항상 popularity/catalog fallback 유지 |

REC-EV-019~026과 C2 vNext 계약이 승인되기 전에는 왼쪽 열만 구현 기준이다.

## 2. 내부 요청 후보

아래 구조는 향후 내부 추천 API의 의미 계약이며 현재 OpenAPI operation을 추가하지 않는다.

```json
{
  "userId": 123,
  "head": "PERSONAL",
  "limit": 20,
  "signals": {
    "onboardingBinary": [
      {"movieId": 10, "action": "LIKE"},
      {"movieId": 20, "action": "DISLIKE"}
    ],
    "activeRatings": [
      {"movieId": 30, "rating": 4}
    ]
  },
  "partyMemberIds": [],
  "seedMovieId": null,
  "providerFilter": null,
  "catalogVersion": "required"
}
```

### 2.1 불변식

- `head`는 `PERSONAL`, `SIMILAR`, `DISCOVERY`, `PARTY` 중 하나다.
- `SIMILAR`는 `seedMovieId`가 필수이고 사용자 신호 없이도 동작한다.
- `PARTY`는 구성원 2~4명과 고정 aggregation policy가 필요하다.
- binary와 rating은 서로 다른 배열로 전달한다.
- provider filter가 `null`이면 OTT 때문에 후보를 제거하거나 선호 순위를 바꾸지 않는다.
- 요청이 DB의 source of truth를 우회해 임의 신호를 주입하는 public API가 되어서는 안 된다.

## 3. 내부 응답 후보

```json
{
  "head": "PERSONAL",
  "policyVersion": "personal-vnext-candidate",
  "catalogVersion": "required",
  "fallback": false,
  "items": [
    {
      "movieId": 100,
      "rank": 1,
      "preferencePercentile": 0.93,
      "predictedRating": null,
      "confidence": "LOW",
      "reasons": [
        {
          "code": "SELECTED_GENRE_MATCH",
          "source": "TMDB_GENRE",
          "modelVersion": "required"
        }
      ],
      "availability": {
        "status": "KNOWN",
        "region": "KR",
        "monetizationType": "FLATRATE",
        "snapshotVersion": "required",
        "providers": []
      }
    }
  ]
}
```

`preferencePercentile`은 ranking 설명용 정규화 값이지 만족 확률이나 별점이 아니다.

## 4. Head별 계약

| Head | 후보·점수 | 필수 입력 | 금지 사항 |
| --- | --- | --- | --- |
| `PERSONAL` | 검증된 개인 preference ranking | `K_b` 또는 `K_r`; 둘 다 0이면 fallback | expected rating을 ranking score로 복제 |
| `SIMILAR` | TMDB structured/text similarity | seed 영화 | seed와 같은 feature를 정답으로 삼은 자체 검증만으로 승인 |
| `DISCOVERY` | PERSONAL 결과 위 constrained rerank | 검증된 PERSONAL score | relevance floor 없이 novelty만 증가 |
| `PARTY` | 구성원별 percentile preference의 고정 집계 | 2~4명, policy | OTT coverage를 구성원 취향으로 간주 |

## 5. fallback 결정 트리

```text
요청·권한·catalogVersion 유효?
  아니오 → 계약 오류
  예
  ├─ head별 필수 입력 없음 → head별 명시 오류 또는 비개인화 fallback
  ├─ 개인 모델 artifact/version 불일치 → popularity fallback
  ├─ 사용자 입력이 모델 최소 K 미만 → 검증된 낮은 K 모델 또는 fallback
  ├─ candidate coverage 부족 → content fallback 후 popularity 보충
  └─ OTT adapter 실패 → 순위 유지 + availability UNKNOWN
```

fallback 결과는 `fallback=true`, `fallbackReason`, 실제 사용한 `policyVersion`을 남긴다. 개인화가
실행되지 않았는데 “당신을 위한 추천”이나 개인 예상 별점을 표시하지 않는다.

## 6. 예상 별점 계약

- `predictedRating`은 `K_r`와 별점 모델 version이 모두 있을 때만 계산한다.
- binary-only, K0, model coverage 부족, calibration gate 실패에서는 `null`이다.
- ranking model의 cosine/BPR/ALS raw score를 1~5 범위로 단순 clipping하지 않는다.
- 화면에 노출할 때는 model version, 평가 구간, MAE/calibration evidence를 model registry에서 추적한다.

## 7. XAI allowlist

| reason code | 필요한 실제 근거 | 허용 Head |
| --- | --- | --- |
| `SELECTED_GENRE_MATCH` | LIKE 또는 높은 Rating 영화와 TMDB genre 기여 | PERSONAL, DISCOVERY, PARTY |
| `SELECTED_CREATOR_MATCH` | 감독·배우 feature의 non-zero 기여 | PERSONAL, SIMILAR |
| `SIMILAR_TO_SELECTED_MOVIE` | 명시된 seed와 similarity component | PERSONAL, SIMILAR |
| `GROUP_COMMON_GROUND` | 모든 구성원 score가 고정 하한 이상 | PARTY |
| `NEW_BUT_CONNECTED` | relevance floor 통과 + 미경험 feature | DISCOVERY |
| `POPULARITY_FALLBACK` | 개인화 실패 후 Bayesian/catalog prior 사용 | PERSONAL, PARTY |

- 모델이 실제로 사용하지 않은 feature를 이유로 생성하지 않는다.
- LLM 자유 생성 문구는 허용하지 않는다. code를 고정 문장으로 렌더링한다.
- reason에는 `source`, `modelVersion`, 가능하면 contribution 또는 rank-change provenance를 저장한다.

## 8. OTT availability 계약

- 기본 mode는 추천 이후 `KR/FLATRATE` availability join이다.
- provider를 선택하지 않은 요청은 join 전후 movie ID와 rank가 동일해야 한다.
- 사용자가 명시적으로 provider-only filter를 켠 경우에만 eligibility를 제한한다.
- provider 응답 누락은 `UNKNOWN`이며 dislike나 낮은 preference가 아니다.
- TMDB 응답의 watch URL과 JustWatch attribution 조건을 보존한다.
- TMDB 응답만으로 provider별 직접 deep link가 있다고 표현하지 않는다.

## 9. version과 관측 필드

모든 응답 또는 내부 trace는 다음을 기록한다.

- `requestId`, `head`, `policyVersion`, `modelVersion`
- `featureVersion`, `catalogVersion`, `availabilitySnapshotVersion`
- `K_b`, `K_r`, 사용한 head 목록
- candidate 수, fallback 여부·이유
- total/candidate/rerank/availability latency
- 설명 reason code와 provenance

raw MovieLens user ID, TMDB token, 사용자 비밀정보는 trace에 넣지 않는다.

## 10. vNext 승격 조건

다음을 모두 충족해야 현재 C2 계약 변경 작업을 시작할 수 있다.

1. 해당 Head의 REC-EV가 [평가 프로토콜](./01-offline-evaluation-protocol-vnext.md)의 gate를 통과한다.
2. model registry에 champion, 적용 `K_b/K_r`, fallback, artifact checksum이 등록된다.
3. C2 vNext business rule과 sequence/data contract가 `APPROVED`가 된다.
4. OpenAPI, DTO, DB/migration, frontend consumer 영향이 한 변경 단위로 정리된다.
5. 정상·빈 결과·권한 오류·artifact 불일치·외부 OTT 장애 acceptance test가 있다.
6. 현재 popularity-only 경로로 즉시 되돌릴 수 있다.

## 11. 필요한 acceptance test

- binary-only 요청에서 `predictedRating == null`
- K0 fallback에서 개인화 reason 미노출
- model artifact version 불일치 시 fail-closed fallback
- provider filter가 없을 때 OTT join 전후 rank 불변
- availability adapter 장애에서 결과 영화·rank 유지
- user가 rating 완료하거나 관심 없음 처리한 영화는 개인 추천에서 제외
- 기존 추천은 평가/관심 없음 전까지 추가 추천 후에도 유지
- 같은 영화의 onboarding binary와 활성 Rating 중복 가산 방지
- XAI reason code가 실제 component allowlist와 일치

## 12. 관련 문서

- [추천 입력 신호 계약 vNext](./00-input-signal-contract-vnext.md)
- [오프라인 평가 프로토콜 vNext](./01-offline-evaluation-protocol-vnext.md)
- [현재 C2 업무 규칙](../c2-recommendation/01-business-rules.md)
- [현재 C2 sequence/data 계약](../c2-recommendation/02-sequence-and-data-contract.md)
- [현재 C2 batch candidate 계약](../c2-recommendation/03-batch-candidate-contract.md)
