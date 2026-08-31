# FEELM 공용 시각·반응형 계약

> 상태: `APPROVED`
> 승인 근거: 2026-08-30 사용자 지시 — 요구사항 명세 우선, 최종 목업의 화면 구조와 색상 체계를 전체 React 화면에 적용
> 시각 원본: `C:\Users\kingc\Downloads\FEELM UI Mockups Final FOR REAL.html`
> 원본 SHA-256: `c438d2da2b53c45c1bbc577799c40e416249c753cf6eaf1c1b281be90622afbf`

## 1. 권위 순서

1. `docs/spec/approved-slices.json`에 등록된 제품 의미와 각 수직 기능의 승인된 local 계약
2. OpenAPI·DB schema·업무 규칙·상태와 접근성 acceptance criterion
3. 본 문서의 공용 시각·반응형 규칙
4. 39개 화면을 담은 412×892 최종 목업의 화면 구성

목업의 문구·점수·기능이 상위 계약과 다르면 상위 계약을 따른다. 목업에 기능이 없어도 승인된 loading,
empty, error, retry, pagination과 privacy 상태를 삭제하지 않는다.

## 2. 브랜드 팔레트

| 역할 | Token | 값 | 사용 |
| --- | --- | --- | --- |
| 기본 배경 | `--canvas` | `#FAF7F2` | 앱 전체의 따뜻한 종이 배경 |
| 표면 | `--surface` | `#FFFFFF` | card, sheet, form |
| 본문 | `--ink-strong` | `#2E2C29` | 제목과 핵심 정보 |
| 보조 본문 | `--ink-muted` | `#706D67` | metadata와 도움말 |
| 브랜드 | `--accent` | `#741F32` | 주요 CTA, active navigation |
| 브랜드 진한색 | `--accent-strong` | `#4A0D0D` | hero gradient와 완료 장면 |
| 필름 | `--surface-warm` | `#F6EFE3` | Party, Film, poster fallback |
| 감상 포인트 | `--gold` | `#D2A34D` | 별점과 강조 수치 |
| 긍정 선택 | `--sage` | `#65806B` | 좋아요와 성공 상태 |
| 리포트 | `--violet` | `#77618F` | factual report 영역 보조색 |

색은 기능 의미를 대체하지 않는다. 선택·오류·성공은 text, icon, `aria-*` 상태를 함께 제공한다.

## 3. 공용 화면 구조

- `feelm.` wordmark와 본문은 목업처럼 serif display + `IBM Plex Sans KR` 본문 조합을 사용한다.
- 700px 미만에서는 desktop pill menu를 렌더링하지 않고 하단 app navigation을 사용한다.
- 하단 navigation 때문에 본문이 가려지지 않도록 최소 112px의 끝 padding을 둔다.
- 검색 홈은 412px에서 제목·검색창·최근 검색어·3열 인기 영화 순서를 보존한다.
- 검색 결과는 412px에서 1열 poster row, 넓은 화면에서 grid로 전환한다.
- 추천은 412px에서 실제 card를 세로 film strip 안에 누적하고, `추가 추천` 성공 전 기존 card를 유지한다.
- 취향 초기 설정은 목업의 중앙 사용자·원형 공간을 사용한다. 포스터를 중앙에 가까운 원 안에 두면
  `LIKE`, 반지름보다 먼 원 밖에 두면 `DISLIKE`, 공간에서 빼면 `미선택`이며 좌표 자체는 저장하지 않는다.
- 회원·온보딩, 평가·Film, Party·OTT 비교, 리포트·설정은 같은 palette와 radius를 공유하되
  제품 영역별 warm/sage/violet 보조색만 사용한다.

## 4. 포스터·외부 이미지

- 실제 `posterPath`가 있으면 `https://image.tmdb.org`의 snapshot URL을 표시한다.
- `posterPath=null` 또는 image load 실패면 repository의 `/poster-placeholder.svg`를 표시한다.
- 테스트를 위해 존재하지 않는 원격 경로를 만들지 않는다.
- TMDB catalog import와 attribution 경계는 데이터 계약을 따르며, 화면 요청 중 TMDB를 호출하지 않는다.

## 5. 시각 완료 Gate

- 320, 390, 412, 768, 1280px에서 document horizontal overflow가 없어야 한다.
- 390·412px에서 모든 주요 행동의 touch target은 최소 44px이다.
- route별 normal/loading/empty/error 상태가 같은 layout 폭을 유지한다.
- 실제 로컬 API를 사용한 검색·추천·평가·Party·회원·리포트 대표 route screenshot을 확인한다.
- frontend test와 production build가 통과하고, 목업과 다른 부분은 상위 요구사항 ID로 설명할 수 있어야 한다.
