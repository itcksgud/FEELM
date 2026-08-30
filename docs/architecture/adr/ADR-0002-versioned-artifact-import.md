# ADR-0002 — Python 수집과 Spring DB 소유권을 versioned artifact로 분리한다

> 상태: `ACCEPTED`  
> 결정일: 2026-08-29

## Context

MovieLens/TMDB 정규화는 Python 생태계가 편리하지만 서비스 Schema와 transaction은 Spring이
소유한다. Python이 운영 DB를 직접 변경하면 migration·불변식과 publish 원자성이 두 코드베이스에
분산된다.

## Decision

Python은 Schema version과 source provenance가 있는 normalized artifact와 quality report를 만든다.
Spring `CatalogImportService`가 staging import, 제약 검증, active version publish를 수행한다.

## Consequences

- 같은 artifact로 import를 반복해 재현할 수 있다.
- Python과 Spring 사이 artifact Schema가 새로운 계약이 된다.
- 중간 파일 저장·정리와 Schema migration 전략이 필요하다.
- artifact format은 첫 구현에서 JSONL과 Parquet를 비교해 별도 implementation ADR로 고정한다.

