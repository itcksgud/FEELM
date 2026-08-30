import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { RecommendationInterpretationExperiment } from "../api/c6";
import { renderCatalog } from "./renderCatalog";
import { server } from "./server";

const endpoint = "http://localhost/api/v1/me/recommendation-interpretation-experiment";

function experiment(overrides: Partial<RecommendationInterpretationExperiment> = {}): RecommendationInterpretationExperiment {
  return {
    experimentVersion: "c6-recommendation-interpretation-v2",
    inputVersion: "rating-snapshot-12",
    modelContext: {
      artifactSetVersion: "movielens-local-v3",
      policyVersion: "relative-utility-v1",
      kSelectionPolicyVersion: "C6_MOST_RECENT_VALIDATED_K_FLOOR_V1",
      utilityPolicyVersion: "C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2",
      availableRatingCount: 12,
      usedRatingCount: 10,
    },
    ratingProfile: { activeRatingCount: 12, mean: 3.6, median: 4, confidence: "MEDIUM" },
    predictions: [{
      movie: { movieId: "018f6826-4da1-7c38-a846-8f794cd8b0c1", title: "실험 영화", posterUrl: null, releaseYear: 2024, genres: ["드라마", "미스터리"] },
      predictedRating: 4.25,
      expectedRelativeUtility: 0.82,
      directFoldIn: true,
      confidence: "MEDIUM",
      displayEligible: false,
    }],
    tasteEvidence: [{
      dimensionType: "GENRE",
      dimensionKey: "film-noir",
      displayName: "느와르",
      ratingCount: 6,
      averageRating: 4.25,
      liftFromUserMean: 0.65,
      confidence: "MEDIUM",
    }],
    limitations: ["LOCAL_EXPERIMENT_ONLY", "NOT_SELF_REPORTED_SATISFACTION", "NOT_PRODUCT_DISPLAY_APPROVED", "K_BUCKETED_MOST_RECENT"],
    ...overrides,
  };
}

describe("C6 recommendation interpretation local experiment", () => {
  it("예상 별점·개인 기준 기대 효용과 관측 근거를 경계 문구와 함께 표시한다", async () => {
    server.use(http.get(endpoint, ({ request }) => {
      expect(request.headers.get("Authorization")).toBe("Bearer test-c1-owner-token");
      expect(request.cache).toBe("no-store");
      return HttpResponse.json(experiment());
    }));

    renderCatalog(<App />, ["/__experiments/recommendation-interpretation"]);

    expect(await screen.findByRole("heading", { name: "예상 별점 (실험)" })).toBeInTheDocument();
    expect(screen.getAllByText("4.25 / 5")).toHaveLength(2);
    expect(screen.getByText("82%")).toBeInTheDocument();
    expect(screen.getByText("개인 기준 기대 효용")).toBeInTheDocument();
    expect(screen.getByText("느와르")).toBeInTheDocument();
    expect(screen.getByText("6개")).toBeInTheDocument();
    expect(screen.getByText("+0.65")).toBeInTheDocument();
    expect(screen.getByText(/직접 측정한 만족도가 아니에요/)).toBeInTheDocument();
    expect(screen.getByText(/displayEligible=false/)).toBeInTheDocument();
    expect(screen.queryByText(/측정된 만족도/)).not.toBeInTheDocument();
  });

  it("null과 평가 부족 상태에서 수치를 꾸미지 않고 자료 부족으로 표시한다", async () => {
    server.use(http.get(endpoint, () => HttpResponse.json(experiment({
      modelContext: { artifactSetVersion: "none", policyVersion: "v1", kSelectionPolicyVersion: "C6_MOST_RECENT_VALIDATED_K_FLOOR_V1", utilityPolicyVersion: "C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2", availableRatingCount: 0, usedRatingCount: 0 },
      ratingProfile: { activeRatingCount: 0, mean: null, median: null, confidence: "INSUFFICIENT_DATA" },
      predictions: [{
        movie: { movieId: "018f6826-4da1-7c38-a846-8f794cd8b0c2", title: "자료 부족 영화", posterUrl: null, releaseYear: null, genres: [] },
        predictedRating: 3.1,
        expectedRelativeUtility: null,
        directFoldIn: false,
        confidence: "INSUFFICIENT_DATA",
        displayEligible: false,
      }],
      tasteEvidence: [],
    }))));

    renderCatalog(<App />, ["/__experiments/recommendation-interpretation"]);

    expect(await screen.findByText(/평가 기록이 없어 개인 기준을 계산할 수 없습니다/)).toBeInTheDocument();
    expect(screen.getAllByText("계산 전")).toHaveLength(2);
    expect(screen.getAllByText("자료 부족").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("표시할 관측 근거가 없어요")).toBeInTheDocument();
  });

  it.each([
    [401, "실험 인증이 필요해요", "로컬 실험 인증이 만료됐어요"],
    [503, "모델을 준비하지 못했어요", "추천 실험 모델이 아직 준비되지 않았어요"],
  ])("HTTP %s를 구분하고 재시도를 제공한다", async (status, title, message) => {
    let calls = 0;
    server.use(http.get(endpoint, () => {
      calls += 1;
      return calls === 1 ? HttpResponse.json({ message: "raw server detail" }, { status }) : HttpResponse.json(experiment());
    }));
    const user = userEvent.setup();
    renderCatalog(<App />, ["/__experiments/recommendation-interpretation"]);

    expect(await screen.findByRole("heading", { name: title })).toBeInTheDocument();
    expect(screen.getByText(new RegExp(message))).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "다시 불러오기" }));
    expect(await screen.findByRole("heading", { name: "예상 별점 (실험)" })).toBeInTheDocument();
  });

  it("local feature가 꺼지면 실험 route를 등록하지 않는다", async () => {
    renderCatalog(<App enableLocalFeatures={false} />, ["/__experiments/recommendation-interpretation"]);
    expect(await screen.findByRole("heading", { name: /오늘은 어떤 영화를/ })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "추천 해석 실험" })).not.toBeInTheDocument();
  });
});
