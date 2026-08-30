# C2A Batch Candidate Artifact 계약

> 상태: `APPROVED_C2A_LOCAL_AND_BATCH_BOUNDARY`  
> 목적: 후보 발견과 온라인 순위화를 분리하고, 같은 후보 집합을 재현 가능하게 전달한다.

## 1. 책임 경계

- producer는 게시된 Catalog와 검증된 MovieLens↔service UUID mapping에서 후보를 만든다.
- artifact와 HTTP에는 FEELM service movie UUID만 기록한다. MovieLens ID는 producer 내부 join 뒤 버린다.
- FastAPI는 전달된 후보만 검증·순위화하며 새 영화를 발견하지 않는다.
- 현재 C2A producer policy는 `GLOBAL_VERIFIED_CATALOG_V1`이다. 개인화·탐험·파티 후보라고 주장하지 않는다.
- smoke Catalog의 2편 결과는 입력 범위 검증일 뿐 production coverage가 아니다.

## 2. Canonical payload v1

```json
{
  "schemaVersion": 1,
  "candidateSetVersion": "sha256:<canonical-payload-sha256>",
  "catalogVersion": "catalog version identifier",
  "mappingPayloadSha256": "64 lowercase hex",
  "compatibilityId": "artifact family identifier",
  "producerPolicy": "GLOBAL_VERIFIED_CATALOG_V1",
  "movieIds": ["service UUID in ascending canonical order"]
}
```

`candidateSetVersion` 계산 때는 해당 필드만 빈 문자열로 둔 canonical JSON의 SHA-256을 사용한다.
키 순서는 사전식, UTF-8, LF, compact JSON이며 생성 시각·host path·token을 payload에 넣지 않는다.
동일 입력과 policy는 byte-identical artifact를 만들어야 한다.

## 3. 입력·품질 Gate

producer는 다음을 모두 통과한 movie만 accepted set에 넣는다.

1. active Catalog projection의 `mediaType=MOVIE`, `identityStatus=IDENTITY_VERIFIED`,
   `visibilityStatus=UI_READY`, `deleted=false`
2. mapping artifact에서 conflict 없이 정확히 하나의 MovieLens ID에 연결된 service UUID
3. active serving artifact가 Bayesian Popularity score를 계산할 수 있는 model item
4. 중복 없는 canonical service UUID

제외 row는 allowlist reason(`NOT_UI_READY`, `NOT_MAPPED`, `MAPPING_CONFLICT`, `MODEL_ITEM_MISSING`,
`DUPLICATE`)과 source count만 sidecar report에 남긴다. external ID·filesystem path는 report에 넣지 않는다.

accepted 0건이면 publish를 실패시키며 이전 candidate set을 유지한다. accepted count와 입력 Catalog 범위
coverage를 기록하되 전체 서비스 coverage로 확대 해석하지 않는다.

## 4. 게시와 retention

- artifact는 immutable이며 content hash version으로 게시한다.
- Spring serving store의 active pointer는 artifact 검증 뒤 원자 교체한다.
- active 1개와 직전 rollback 가능 1개를 최소 보존한다. 실제 기간·Redis TTL은 부하 실측 전 고정하지 않는다.
- 이미 생성된 recommendation exposure가 참조하는 `candidateSetVersion` metadata는 삭제하지 않는다.
- consumer가 candidate set을 못 읽거나 checksum이 다르면 stale set을 성공처럼 쓰지 않고 추천 503을 반환한다.

## 5. Acceptance

| ID | Given / When / Then |
| --- | --- |
| `AC-C2-033` | 같은 Catalog·mapping·model·policy로 두 번 export하면 payload와 version이 byte-identical이다. |
| `AC-C2-034` | unmapped/model-missing 영화가 섞이면 accepted set에서 빠지고 safe quarantine count가 일치한다. |
| `AC-C2-035` | accepted 0건 또는 checksum mismatch이면 active pointer가 바뀌지 않는다. |
| `AC-C2-036` | artifact와 rank HTTP에서 MovieLens ID, user ID, token, host path가 노출되지 않는다. |
| `AC-C2-037` | Spring이 전달한 `candidateSetVersion`과 FastAPI snapshot의 값이 정확히 같다. |

Redis 도입은 이 계약의 필수 의미가 아니다. local file, PostgreSQL, Redis 중 어떤 adapter를 사용해도
canonical artifact·원자 active pointer·rollback·version snapshot 의미를 지켜야 한다.
