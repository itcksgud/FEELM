# REC-EV-008 — React UI 비교

> 상태: `COMPLETED_UI_COMPARISON_EVIDENCE`  
> 제품 UI 승인: `NO`  
> 공개 navigation/API: `NO`  
> 실제 사용자 연구: `NOT_RUN`

## 1. 결론

개발 전용 React evidence lab에서 예상 별점, 온보딩 입력 수, 파티 집계, 추천 이유 개수의 네 비교를
동일한 1440×1200 viewport로 만들었다. 이것은 정보 밀도·최소 조작 수·근거 한계를 함께 검토하기
위한 정적 자료다. 클릭 선호, 이해도, 완료시간, 이탈률, 추천 만족도는 측정하지 않았으므로 어떤 안도
제품 기본값으로 승인하지 않는다.

- 예상 별점: K10은 예상 별점 MAE의 첫 3% data Gate지만 C1 정수 척도 paired calibration 전에는
  `NOT_COMPUTED`가 안전한 기본 상태다. 추천 순위 개선 근거는 아니다.
- 온보딩: K5 6회, K10 11회, skip 1회는 화면에서 세는 최소 조작 수다. 실제 입력 부담·이탈률이 아니다.
- 파티: Balanced의 Average 대비 세 paired-bootstrap CI가 모두 0을 포함해 개선 미입증이다.
- 이유: REC-EV-006이 완료되어 실제 typed coverage를 사용했다. 문구·개수와 한 추천에서 최대 3개가
  동시에 나오는 비율은 승인·관측되지 않았다.

## 2. 공개 제품과의 격리

- React route `/__evidence/rec-ev-008`은 `import.meta.env.DEV`일 때만 등록된다.
- 검색·상세·Film 등 공개 navigation에는 링크가 없다.
- API 호출, main OpenAPI operation, backend state, 사용자 event를 추가하지 않았다.
- production build에서는 이 route가 등록되지 않으며 fallback은 기존 `/search`다.
- 화면 상단과 각 한계 블록에 `제품 승격 금지`와 미관측 범위를 표시한다.

## 3. 비교표

| 질문 | A | B/대안 | 사용 근거 | 잠재 제품 영향 | 되돌림 비용 | 현재 판정 |
| --- | --- | --- | --- | --- | --- | --- |
| 예상 별점 표시 | `★ 예상 4.2/5`, 외부 평점과 구분 | 숨김 + `NOT_COMPUTED` 이유 | REC-EV-003B K10, REC-EV-003C fail-closed | 숫자는 빠르지만 calibration보다 확신을 크게 보일 수 있음 | 조건부 rendering은 낮음; 이미 노출한 숫자 해석·event 변경은 중간 | `WAITING_FOR_PRODUCT_APPROVAL_AND_C1_SCALE_CALIBRATION` |
| 온보딩 입력 | K5: 판단 5 + 완료 1 | K10: 10+1, skip: 0+1 | REC-EV-003B K curve | K10은 별점 data Gate를 통과하지만 최소 조작이 K5보다 5회 많음 | prototype은 낮음; 저장 vector/version을 제품화한 뒤에는 중간 | `WAITING_FOR_INPUT_BURDEN_USER_EVIDENCE` |
| 파티 집계 | Average 기준선 | validation-selected Balanced 후보 | REC-EV-005 Test | Balanced가 평균·최저·격차를 개선했다고 말할 수 없음 | 계산 정책·설명을 배포하면 중간~높음 | `DO_NOT_ADOPT_PRODUCT_POLICY` |
| 추천 이유 | reason 1개 | 최대 3개 | REC-EV-006 typed coverage·ablation | 한 개는 밀도가 낮고 세 개는 설명량이 늘지만 동시 coverage 미측정 | display limit config는 낮음; 문구·이벤트 계약은 중간 | `WAITING_FOR_DISPLAY_COUNT_AND_COPY_APPROVAL` |

## 4. 비교별 관측

### 4.1 예상 별점 표시 vs 숨김

숫자안은 `실험 예상 별점`, `/5`, 외부 평점·FEELM 평균과 다르다는 label을 한 묶음으로 표현했다.
숨김안은 단순 공백이 아니라 `NOT_COMPUTED`와 C1 scale calibration 부재를 보여준다. REC-EV-003B의
K10 relative MAE 개선은 3.9516%이고 순위 alpha는 모든 K에서 0이었다. 따라서 별점 후보를 추천 순위
개선이나 서비스 사용자의 정수 평가척도 calibration으로 확장하지 않는다.

### 4.2 K5 vs K10 + skip

LIKE/DISLIKE 한 번을 영화 판단 한 번, 완료/skip을 한 번으로 세었다. 뒤로가기, 수정, 읽기, 포스터
탐색, network 대기는 포함하지 않았다. MovieLens에는 가입 이탈·완료시간이 없어 `K10이 부담스럽다`나
`skip이 전환율을 높인다`고 말하지 않는다. C4A의 K/min/max/rerun 결정도 계속 미승인이다.

### 4.3 Average vs Balanced 후보

REC-EV-005의 실제 순위 반전 사례를 표시하되 전체 결과와 분리했다. Test macro에서 Balanced − Average는
평균 효용 -0.0013 `[-0.0037,+0.0007]`, 최저 효용 +0.0005 `[-0.0035,+0.0045]`, 격차 -0.0042
`[-0.0116,+0.0024]`이며 모두 0을 포함한다. 4인 공통평가 coverage도 0.69%~1.02%라 observation
bias가 심하다. 실제 파티 만족도나 `PARTY_BALANCED_V1` 승인을 주장하지 않는다.

### 4.4 이유 1개 vs 최대 3개

REC-EV-006 `REC_REASON_FAITHFULNESS_V1`이 완료되었으므로 `WAITING_FOR_EVIDENCE` 대신 실제
`EMITTABLE_CANDIDATE` coverage를 연결했다.

| Reason type | Emittable coverage | UI lab 처리 |
| --- | ---: | --- |
| `POPULARITY_BASELINE` | 99.98% | 1개안과 최대 3개안의 첫 후보 |
| `LIST_DIVERSITY` | 59.98% | 최대 3개안의 조건부 후보 |
| `LESS_POPULAR_DISCOVERY` | 24.31% | 최대 3개안의 조건부 후보 |
| `GENRE_AFFINITY` | 0.00% | active policy가 아니므로 표시하지 않음 |

이 비율은 40,000개 sampled recommendation position의 reason별 coverage다. 세 이유의 row-level
동시 발생률이 아니며 한국어 문구도 비교용 미승인 copy다. 실제 출력은 active policy, positive
contribution, rank effect, provenance, non-sensitive Gate를 모두 통과해야 한다.

## 5. Screenshot artifact

스크린샷은 clean checkout의 결정 패킷에서도 열 수 있도록 비밀·raw ID가 없는 canonical PNG를
`docs/recommendation/evidence/assets/rec-ev-008/`에 추적한다. SHA-256과 byte 수는
`manifests/rec-ev-008.json`에서 고정한다.

| 비교 | artifact |
| --- | --- |
| 예상 별점 | [stars-1440x1200.png](./assets/rec-ev-008/stars-1440x1200.png) |
| 온보딩 | [onboarding-1440x1200.png](./assets/rec-ev-008/onboarding-1440x1200.png) |
| 파티 | [party-1440x1200.png](./assets/rec-ev-008/party-1440x1200.png) |
| 이유 | [reasons-1440x1200.png](./assets/rec-ev-008/reasons-1440x1200.png) |

## 6. 접근성·반응형

- `main`, 비교 `nav`, label이 있는 `section`, 순차 heading을 사용했다.
- 선택 tab은 native button과 `aria-current=page`를 사용하고 keyboard Enter를 React test로 검증했다.
- 예상 별점에는 시각적 별과 별개로 `/5` accessible label이 있다.
- 한계는 색만으로 구분하지 않고 `해석 한계` 텍스트와 함께 표시한다.
- 820px 이하에서는 비교 열과 CI가 단일 열로 바뀐다.

자동 접근성 audit나 실제 screen reader 사용성 연구를 수행했다는 뜻은 아니다.

## 7. 검증 결과

| Gate | 결과 |
| --- | --- |
| React 비교·keyboard semantics | `4/4 PASS` |
| frontend 전체 회귀 | `26/26 PASS` |
| 동일 viewport Playwright | `1/1 PASS`, canonical PNG 4개 |
| production build | `PASS` |
| production bundle route/string scan | `PASS`, evidence route 미등록·미번들 |
| manifest source/hash/dimension 검증 | `PASS` |

## 8. 재현

```powershell
npm test --prefix frontend -- --run src/test/RecEv008Lab.test.tsx
npm run test:evidence:rec-ev-008 --prefix e2e
npm test --prefix frontend
npm run build --prefix frontend
node scripts/verify-recommendation-ui-comparison.mjs
```

## 9. 남은 Evidence gap

- 예상 별점: C1 product integer-scale paired calibration, confidence/coverage, 숫자 이해도
- 온보딩: 실제 완료시간·수정·skip·이탈과 K5/K10 무작위 비교
- 파티: 실제 party choice/watch/satisfaction, 4인 full-catalog coverage
- 이유: row-level 동시 reason coverage, copy 이해도, 펼치기/밀도 선호
- 공통: mobile screenshot은 responsive code/test 대상이나 판단 screenshot packet에는 아직 없음

이 gap을 제품 기능으로 메우지 않는다. 다음 단계는 `TASK-REC-EV-009` 결정 패킷 조립이며 제품
소유자가 선택하기 전 main OpenAPI, public navigation, 추천 champion, 정책 default는 바꾸지 않는다.
