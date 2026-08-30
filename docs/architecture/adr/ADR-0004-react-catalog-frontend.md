# ADR-0004 — Catalog frontend는 React·TypeScript·Vite를 사용한다

> 상태: `ACCEPTED`  
> 결정일: 2026-08-29  
> 변경 근거: 프로젝트 소유자가 React 사용을 명시함

## Context

최종 목업은 HTML 시각 참고이며 제품 frontend baseline은 React다. 화면마다 다른 data access,
cache, URL filter 처리를 발명하지 않도록 C0 구현 기준을 고정한다.

## Decision

- React + TypeScript + Vite
- React Router
- 서버 상태는 TanStack Query, 검색·filter 상태는 URL query parameter를 기준으로 한다.
- OpenAPI-generated TypeScript type + `openapi-fetch`
- Vitest + React Testing Library + MSW
- C0에서는 Redux·Zustand 같은 전역 client store를 필수로 도입하지 않는다.
- 화면 style은 CSS Modules와 공통 design token file을 사용한다.

## Consequences

- OpenAPI 변경을 type generation과 build에서 감지할 수 있다.
- mock과 실제 Spring API를 같은 client로 교체할 수 있다.
- 검색 URL을 새로고침·공유해도 query와 filter를 복원할 수 있다.
- 20MB 목업을 runtime dependency로 사용하지 않는다.
- 인증·파티처럼 화면 간 client state가 실제로 늘어나면 store 도입을 별도 ADR로 판단한다.
