export type EvidenceComparisonId = "stars" | "onboarding" | "party" | "reasons";

export const comparisonOrder: EvidenceComparisonId[] = [
  "stars",
  "onboarding",
  "party",
  "reasons",
];

export const comparisonLabels: Record<EvidenceComparisonId, string> = {
  stars: "예상 별점",
  onboarding: "온보딩 부담",
  party: "파티 정책",
  reasons: "추천 이유",
};

export const starComparison = {
  movieTitle: "UI 비교용 영화",
  computed: {
    status: "COMPUTED_EVIDENCE_ONLY",
    value: 4.2,
    scale: 5,
    evidence: "REC-EV-003B K10_DATA_ONLY",
  },
  hidden: {
    status: "NOT_COMPUTED",
    reason: "C1 정수 별점 척도와의 paired calibration 근거가 아직 없습니다.",
    evidence: "REC-EV-003C fail-closed",
  },
  limitation:
    "K10은 예상 별점 MAE의 3% data Gate를 처음 통과했지만 추천 순위 개선 근거는 아닙니다.",
} as const;

export const onboardingComparison = [
  {
    id: "K5",
    title: "K5 빠른 입력안",
    movieDecisions: 5,
    minimumActions: 6,
    note: "별점 head의 3% 실질 개선 Gate에는 미달했습니다.",
  },
  {
    id: "K10",
    title: "K10 데이터 후보",
    movieDecisions: 10,
    minimumActions: 11,
    note: "별점 head의 첫 실질 개선 지점이며 순위 alpha는 여전히 0입니다.",
  },
  {
    id: "SKIP",
    title: "건너뛰기",
    movieDecisions: 0,
    minimumActions: 1,
    note: "입력 부담은 가장 작지만 실제 이탈률·만족도는 관측하지 않았습니다.",
  },
] as const;

export const partyComparison = {
  average: {
    policy: "AVERAGE",
    selectedTitle: "Thing, The (1982)",
    observedRelativeUtility: 0.41,
    ratings: [1.5, 4.0],
  },
  balanced: {
    policy: "BALANCED 후보",
    selectedTitle: "Sully (2016)",
    observedRelativeUtility: 0.594,
    ratings: [4.0, 3.5],
  },
  pairedBootstrap: [
    { label: "평균 효용 Δ", value: "-0.0013", ci: "[-0.0037, +0.0007]" },
    { label: "최저 효용 Δ", value: "+0.0005", ci: "[-0.0035, +0.0045]" },
    { label: "격차 Δ", value: "-0.0042", ci: "[-0.0116, +0.0024]" },
  ],
  limitation:
    "세 95% CI가 모두 0을 포함하고 4인 Test 공통평가 coverage는 0.69%~1.02%입니다.",
} as const;

export const reasonComparison = {
  evidenceStatus: "COMPLETED_OFFLINE_EVIDENCE",
  contract: "REC_REASON_FAITHFULNESS_V1",
  variants: [
    {
      id: "ONE",
      title: "이유 1개",
      reasons: [
        {
          code: "POPULARITY_BASELINE",
          candidateCopy: "평가가 충분히 쌓인 인기 작품",
          coverage: 0.999825,
        },
      ],
    },
    {
      id: "UP_TO_THREE",
      title: "최대 3개",
      reasons: [
        {
          code: "POPULARITY_BASELINE",
          candidateCopy: "평가가 충분히 쌓인 인기 작품",
          coverage: 0.999825,
        },
        {
          code: "LIST_DIVERSITY",
          candidateCopy: "목록의 장르 구성을 넓힌 작품",
          coverage: 0.599775,
        },
        {
          code: "LESS_POPULAR_DISCOVERY",
          candidateCopy: "덜 알려진 작품을 발견하도록 포함",
          coverage: 0.243125,
        },
      ],
    },
  ],
  limitation:
    "coverage는 40,000개 sampled position의 reason별 비율입니다. 세 이유가 한 추천에서 동시에 발생한다는 뜻이 아니며 문구·개수도 미승인입니다.",
} as const;
