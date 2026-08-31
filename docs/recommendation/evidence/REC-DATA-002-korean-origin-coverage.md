# TMDB 한국-origin · MovieLens 교차 감사

> 상태: `COMPLETED_PROXY_AUDIT`
> 생성 시각: 2026-08-31T05:31:34.080512+00:00
> 기준: TMDB Discover `with_origin_country=KR`, primary release date 1870-01-01~2023-10-13

## 결론

이 감사는 한국 영화에 대한 MovieLens 표본이 학습·검증에 어느 정도 존재하는지 확인한다.
`with_origin_country=KR`는 TMDB의 검색 필터로 만든 proxy이며 작품의 국적을 법적·문화적으로 확정하지 않는다.
MovieLens에는 사용자 국가와 나이가 없으므로 아래 숫자로 **한국 20대 사용자 성능**을 주장할 수 없다.

- TMDB 한국-origin proxy: 11,680편
- MovieLens와 TMDB ID가 교차되는 영화: 1,056편 (1.21%)
- 해당 영화 Rating: 90,885개 (0.28%)
- 해당 영화를 1편 이상 평가한 사용자: 32,848명 (16.35%)
- 한국-origin Rating 5개 이상 사용자: 4,053명
- 한국-origin Rating 10개 이상 사용자: 1,379명
- 한국-origin Rating 25개 이상 사용자: 260명

## 영화별 상호작용 밀도

| Rating 구간 | 영화 수 | 교차 영화 내 비율 |
| --- | --- | --- |
| zero | 8 | 0.76% |
| one_to_nine | 606 | 57.39% |
| ten_to_ninety_nine | 377 | 35.70% |
| one_hundred_to_999 | 49 | 4.64% |
| one_thousand_to_9999 | 14 | 1.33% |
| ten_thousand_plus | 2 | 0.19% |

## 해석과 사용 제한

1. 전체 모델 학습에는 한국-origin 영화를 포함하되, 작은 집단을 전체 Train/Validation/Test에 중복 사용하지 않는다.
2. 한국 영화 slice는 사용자 기준으로 먼저 분리한 뒤 Train 이력만으로 cohort를 정의한다.
3. 한국-origin Rating 1개만 있는 사용자를 한국 영화 선호 사용자로 부르지 않는다. 5/10/25개 기준을 민감도 분석한다.
4. 한국 20대 검증은 별도의 실제 사용자 평가 또는 서비스 이벤트가 생기기 전까지 `NOT_EVALUATED`다.
5. 이 결과는 2026년 TMDB 상태를 2023-10-13까지의 primary release date로 조회한 회고적 proxy다.
