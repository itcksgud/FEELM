# FEELM Catalog data pipeline

MovieLens `movies.csv`/`links.csv`와 TMDB API를 결합해 Spring importer가 읽는
ADR-0006 JSONL schema v1 artifact를 생성한다. Python job은 운영 DB에 직접 쓰지 않는다.

```powershell
cd C:\higher\projects\FEELM-standalone\data-pipeline
py -3.12 -m unittest discover -s tests -v

py -3.12 -m feelm_catalog_pipeline build `
  --archive C:\higher\projects\MM\data\raw\ml-32m.zip `
  --catalog-version catalog-20260829-01 `
  --output ..\outputs\catalog\catalog-20260829-01.jsonl `
  --quality-report ..\outputs\catalog\catalog-20260829-01-quality.json `
  --identity-map ..\outputs\catalog\identity-map.json `
  --identity-map-output ..\outputs\catalog\identity-map.json `
  --cache-dir ..\outputs\catalog\tmdb-cache
```

실제 `TMDB_READ_ACCESS_TOKEN`은 저장소 루트의 `.env.local` 또는 프로세스 환경에서만 읽는다.
TMDB v4 Read Access Token을 우선 사용하며, 로컬 데이터 작업 호환을 위해 v3 API key도 같은 변수로
주입할 수 있다. 어느 형식도 artifact·cache·로그에 기록하지 않는다.
토큰, Authorization header, 원본 HTTP header는 artifact와 로그에 기록하지 않는다.

`identity-map.json`은 MovieLens/TMDB/IMDb 외부 ID를 공개 `movieId` UUID에 연결한다. 기존 map을
입력하면 UUID를 재사용하고, 신규 영화는 UUID v4를 한 번 생성해 output map에 보존한다. 외부 ID를
해시하거나 UUID namespace에 넣어 공개 ID를 결정적으로 만들지 않는다.

JSONL 첫 행은 `artifactHeader`이며 이후 허용 record type은 다음과 같다.
각 행의 기계 판독 계약은 `schema/catalog-artifact-v1.schema.json`에 고정되어 있다.

- `movieIdentity`
- `movieProjection`
- `localization`
- `genre`
- `country`
- `credit`
- `provider`
- `availabilitySnapshot`
- `ottOffer`

네트워크 응답 cache는 중단된 실행을 재개하기 위한 로컬 산출물이다. cache에는 요청 header나 token을
넣지 않으며 Git에 추가하지 않는다.
