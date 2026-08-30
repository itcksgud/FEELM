# ADR-0005 — 안정적인 영화 identity와 versioned Catalog projection을 분리한다

> 상태: `ACCEPTED`  
> 결정일: 2026-08-29  
> 승인 근거: 사용자가 공개 movieId 안정성과 Catalog rollback을 함께 승인함

## Context

공개 영화 URL의 `movieId`는 수집 갱신과 무관하게 안정적이어야 한다. 동시에 새 Catalog를 staging한
뒤 active version을 원자적으로 교체하고 실패 시 이전 version으로 되돌릴 수 있어야 한다. 영화 행이
한 개 `catalog_version_id`에 종속되면 같은 UUID를 두 version에 동시에 저장할 수 없고, 행을
덮어쓰면 rollback snapshot을 잃는다.

## Decision

- `MOVIE_IDENTITY`는 공개 UUID와 생성 시각만 소유하는 비버전 identity다.
- 외부 ID mapping은 identity에 연결하며 공개 API ID로 사용하지 않는다.
- metadata, localization, 관계, 검색 문서, 유사도와 availability는
  `catalog_version_id + movie_id`를 경계로 versioning한다.
- API 요청은 시작 시 active Catalog version을 한 번 resolve하고 모든 조회에 같은 version을 사용한다.
- source removal은 identity 삭제가 아니라 version projection 상태로 표현한다.

## Consequences

- Catalog 갱신과 rollback 후에도 영화 URL과 사용자 참조가 유지된다.
- staging version과 active version이 같은 영화 identity를 안전하게 공유한다.
- version별 projection 저장 공간과 복합 FK가 추가된다.
- import·query가 항상 catalog version 조건을 포함해야 하며 이를 통합 테스트로 검증해야 한다.

## Rejected alternatives

- version마다 새 movie UUID 생성: 공개 URL과 이후 사용자 평가 참조가 깨진다.
- 한 movie 행을 update in place: 원자 publish와 rollback을 보장하지 못한다.
- identity까지 version 복제: 동일 영화의 서비스 내부 정체성이 여러 개가 된다.

