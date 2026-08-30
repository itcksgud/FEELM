# FEELM C0 Catalog 고정 Fixture

> 상태: `APPROVED`  
> 승인 확장: `docs/c1-draft/testing/fixtures.md` — C1 Rating·Film  
> Canonical registry: `docs/spec/approved-slices.json`  
> 고정 시계: `2026-08-29T12:00:00Z`  
> 모든 UUID와 응답 의미는 backend·frontend·contract test에서 동일하게 사용한다.

## 1. 사용자·Provider

| Fixture | UUID | 값 |
| --- | --- | --- |
| `USER-SUBSCRIBED` | `018f6826-4da1-7c38-a846-8f794cd8b0cf` | Netflix 구독 사용자 |
| `PROVIDER-NETFLIX` | `d392a4d5-0428-4e06-aa41-aef899c06842` | display priority 10 |
| `PROVIDER-WATCHA` | `4f57022d-6d8e-40b2-b7be-4ac313ef6bd0` | display priority 20 |
| `PROVIDER-WAVVE` | `1f0c5888-f6f4-42a9-b661-a90cff45e303` | display priority 30 |
| `PROVIDER-GOOGLE-PLAY` | `7012659c-f25e-429b-9fda-21528dc6cd1b` | rent/buy provider |

## 2. 영화

| Fixture | movieId | 핵심 상태 |
| --- | --- | --- |
| `MOV-KO-FULL` | `6b226903-0ca4-4f5a-9bf0-50d6cedd224c` | 나우 유 씨 미, KO 전체, poster/runtime/director, fresh Netflix+wavve flatrate |
| `MOV-EN-FALLBACK` | `19406c31-213f-4fe1-93f6-109f8570ec20` | KO translation 없음, EN title·overview fallback, Watcha flatrate |
| `MOV-NO-POSTER` | `97204ea5-e6e5-4417-a13f-bc8197660705` | Catalog visible, poster null, UI-ready 아님 |
| `MOV-NONE-LISTED` | `e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490` | 6시간 전 SUCCESS_EMPTY |
| `MOV-OTT-UNKNOWN` | `1958ba3a-3d8c-4a4f-8845-124c0b12373e` | 성공 snapshot 없음 |
| `MOV-OTT-STALE` | `0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89` | 72시간 전 Netflix flatrate 성공 snapshot |
| `MOV-STALE-ID-RECOVERED` | `c886c3ca-52d6-45c6-bdbc-89fbfce62d3c` | 예전 TMDB ID 404, IMDb로 새 movie ID 복구 |
| `MOV-TV-MISMATCH` | `8524f2c2-aaeb-48ff-a21d-df544df23d46` | `TYPE_MISMATCH_TV`, 공개 금지 |
| `MOV-SIMILAR-1` | `e67778c9-7b2e-42d4-9d3e-a3026b2efea3` | UI-ready, MOV-KO-FULL과 장르·감독 공통 |
| `MOV-SIMILAR-2` | `cc3ddb45-0511-46ea-bf28-95b67c9fd20f` | UI-ready, MOV-KO-FULL과 장르·키워드 공통 |

## 3. Availability 시각

| Movie | fetch status | fetchedAt | 기대 API 상태 |
| --- | --- | --- | --- |
| `MOV-KO-FULL` | SUCCESS_LISTED | `2026-08-29T06:00:00Z` | LISTED/FRESH |
| `MOV-NONE-LISTED` | SUCCESS_EMPTY | `2026-08-29T06:00:00Z` | NONE_LISTED/FRESH |
| `MOV-OTT-UNKNOWN` | FAILED만 존재 | `2026-08-29T10:00:00Z` | UNKNOWN/UNKNOWN |
| `MOV-OTT-STALE` | SUCCESS_LISTED | `2026-08-26T12:00:00Z` | LISTED/STALE |

## 4. 검색 Fixture

- query `나우 유`: `MOV-KO-FULL` 1건 이상
- query `Louis Leterrier`: 감독명으로 `MOV-KO-FULL`
- query `Jesse Eisenberg`: 배우명으로 `MOV-KO-FULL`
- query `존재하지않는검색어`: 0건
- filter `provider=Netflix`, 기본 유형: Netflix FLATRATE 영화만
- filter `provider=Google Play`, type RENT: rent offer 영화만
- TV mismatch는 어떤 검색·filter에도 노출되지 않음

## 5. Similarity Fixture

`MOV-KO-FULL`, similarity version `sim-fixture-v1`:

1. `MOV-SIMILAR-1`: `SHARED_GENRE`, `SHARED_DIRECTOR`
2. `MOV-SIMILAR-2`: `SHARED_GENRE`, `SHARED_KEYWORD`

source 자신, `MOV-NO-POSTER`, `MOV-TV-MISMATCH`는 결과에서 제외한다.

## 6. Token Fixture

- token 없음: 익명, 모든 `isSubscribed=null`
- `test-valid-subscribed-token`: `USER-SUBSCRIBED`, Netflix만 true
- `test-invalid-token`: 401 `INVALID_ACCESS_TOKEN`

실제 JWT secret·운영 token을 fixture에 넣지 않는다. test profile의 fake decoder만 위 symbolic token을
인식한다.
