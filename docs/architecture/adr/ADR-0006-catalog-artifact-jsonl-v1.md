# ADR-0006 — C0 normalized Catalog artifact는 JSONL schema v1을 사용한다

> 상태: `ACCEPTED`  
> 결정일: 2026-08-29

## Context

Python 수집 job과 Spring import 사이의 첫 artifact 형식이 정해지지 않아 import 구현자가 JSONL과
Parquet 중 하나를 추측해야 했다. C0는 row 단위 streaming import, 사람이 읽을 수 있는 고정 fixture,
명시적 schema validation을 우선한다.

## Decision

- C0 artifact는 UTF-8 JSONL이며 파일 첫 행은 `artifactHeader`다.
- header에는 `schemaVersion: 1`, `catalogVersion`, `generatedAt`, source checksum을 둔다.
- 이후 각 행은 `recordType`과 해당 normalized payload를 가진다.
- importer는 알 수 없는 schemaVersion이나 recordType을 거부하고 active Catalog를 유지한다.
- token, 전체 외부 HTTP header와 원본 응답 본문은 artifact에 넣지 않는다.

## Consequences

- 작은 fixture와 streaming import를 같은 parser로 검증할 수 있다.
- 열 지향 분석 성능과 압축 효율은 Parquet보다 낮다.
- 대규모 import 측정에서 JSON decode/I/O가 병목이면 schema를 유지한 Parquet v2를 별도 ADR로 검토한다.

