import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { RecommendationDelivery, RecommendationItem } from "../api/c2b";
import { renderCatalog } from "./renderCatalog";
import { server } from "./server";

const deliveryId = "3fe5121c-59ae-4d0e-a49b-4593e1297918";
const cursor = "signed-cursor-for-next-recommendation-page-0001";

function item(index: number, title: string): RecommendationItem {
  return {
    deliveryItemId: `10000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
    position: index,
    sourceRank: index,
    recommendationType: "POPULARITY_BASELINE",
    movie: {
      movieId: `20000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
      title,
      posterUrl: null,
      releaseYear: 2000 + index,
      genres: ["드라마", "미스터리"],
    },
  };
}

function delivery(items: RecommendationItem[], hasMore = false, revision = 1): RecommendationDelivery {
  return {
    deliveryId,
    deliveryRevision: revision,
    label: "POPULARITY_BASELINE",
    composition: "BASELINE_THREE",
    items,
    pageInfo: {
      activeItemCount: items.length,
      hasMore,
      nextCursor: hasMore ? cursor : null,
      cursorExpiresAt: hasMore ? "2026-08-30T12:00:00Z" : null,
    },
  };
}

const errorBody = (status: string) => ({
  code: status,
  message: "recommendation failure",
  traceId: "trace-c2b-test",
  fieldErrors: [],
});

describe("C2B personal discovery recommendations", () => {
  it("공통 헤더의 접근 가능한 메뉴 링크로 추천 route에 진입한다", async () => {
    server.use(http.get("http://localhost/api/v1/me/recommendations/personal-discovery", () => HttpResponse.json(
      delivery([item(1, "헤더에서 찾은 추천")]),
    )));
    const user = userEvent.setup();
    renderCatalog(<App />, ["/search"]);

    const navigation = screen.getByRole("navigation", { name: "주요 메뉴" });
    const link = within(navigation).getByRole("link", { name: "영화 추천" });
    expect(link).toHaveAttribute("href", "/me/recommendations");
    await user.click(link);

    expect(await screen.findByRole("heading", { level: 1, name: "오늘의 영화 추천" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "헤더에서 찾은 추천" })).toBeInTheDocument();
  });

  it("최초 또는 기존 active collection의 실제 영화 카드를 같은 형태로 렌더링한다", async () => {
    server.use(http.get("http://localhost/api/v1/me/recommendations/personal-discovery", () => HttpResponse.json(
      delivery([item(1, "첫 번째 추천"), item(2, "기존 추천")]),
    )));

    renderCatalog(<App />, ["/me/recommendations"]);

    expect(await screen.findByRole("heading", { name: "첫 번째 추천" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "기존 추천" })).toBeInTheDocument();
    expect(screen.getAllByText("인기 기준 추천").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText(/예상 별점|NOT_COMPUTED|0점/)).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("준비된 추천을 모두 확인했어요");
  });

  it("추천 더 보기는 기존 카드를 유지하고 최대 3편을 append한 뒤 collection을 refetch한다", async () => {
    const first = item(1, "기존 카드");
    const appended = [item(2, "추가 추천 A"), item(3, "추가 추천 B"), item(4, "추가 추천 C")];
    let getCalls = 0;
    let appendBody: unknown;
    let idempotencyKey = "";
    server.use(
      http.get("http://localhost/api/v1/me/recommendations/personal-discovery", () => {
        getCalls += 1;
        return HttpResponse.json(getCalls === 1 ? delivery([first], true) : delivery([first, ...appended], false, 2));
      }),
      http.post("http://localhost/api/v1/me/recommendation-deliveries/:deliveryId/append", async ({ request }) => {
        appendBody = await request.json();
        idempotencyKey = request.headers.get("Idempotency-Key") ?? "";
        await delay(80);
        return HttpResponse.json({
          appendEventId: "30000000-0000-4000-8000-000000000001",
          deliveryId,
          deliveryRevision: 2,
          outcome: "COMPLETE",
          selectionSummary: { scannedCount: 3, selectedCount: 3, excludedCount: 0 },
          appendedItems: appended,
          issues: [],
          pageInfo: delivery([first, ...appended], false, 2).pageInfo,
          replayed: false,
        }, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderCatalog(<App />, ["/me/recommendations"]);

    expect(await screen.findByRole("heading", { name: "기존 카드" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "추천 더 보기" }));
    expect(screen.getByRole("heading", { name: "기존 카드" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "추천 추가 중" })).toBeDisabled();

    expect(await screen.findByRole("heading", { name: "추가 추천 C" })).toBeInTheDocument();
    expect(screen.getAllByRole("article")).toHaveLength(4);
    await waitFor(() => expect(getCalls).toBe(2));
    expect(screen.queryByRole("button", { name: "추천 더 보기" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("준비된 추천을 모두 확인했어요");
    expect(appendBody).toMatchObject({ expectedRevision: 1, cursor });
    expect(idempotencyKey).toMatch(/^[0-9a-f-]{36}$/i);
  });

  it("관심 없음 성공 시 선택한 카드만 제거하고 서버 collection을 refetch한다", async () => {
    const removed = item(1, "관심 없는 영화");
    const kept = item(2, "남길 영화");
    let getCalls = 0;
    let dismissalBody: unknown;
    server.use(
      http.get("http://localhost/api/v1/me/recommendations/personal-discovery", () => {
        getCalls += 1;
        return HttpResponse.json(getCalls === 1 ? delivery([removed, kept]) : delivery([kept], false, 2));
      }),
      http.post("http://localhost/api/v1/me/recommendation-delivery-items/:deliveryItemId/dismissals", async ({ params, request }) => {
        dismissalBody = await request.json();
        return HttpResponse.json({
          dismissalEventId: "40000000-0000-4000-8000-000000000001",
          deliveryItemId: String(params.deliveryItemId),
          deliveryRevision: 2,
          status: "DISMISSED_NOT_INTERESTED",
          occurredAt: "2026-08-30T03:00:00Z",
          replayed: false,
        }, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderCatalog(<App />, ["/me/recommendations"]);

    const removedCard = await screen.findByRole("heading", { name: "관심 없는 영화" });
    await user.click(within(removedCard.closest("article")!).getByRole("button", { name: "관심 없음" }));

    await waitFor(() => expect(screen.queryByRole("heading", { name: "관심 없는 영화" })).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "남길 영화" })).toBeInTheDocument();
    await waitFor(() => expect(getCalls).toBe(2));
    expect(dismissalBody).toMatchObject({ expectedRevision: 1, reason: "NOT_INTERESTED" });
  });

  it("loading 뒤 empty 상태를 구분하고 검색 경로를 제공한다", async () => {
    server.use(http.get("http://localhost/api/v1/me/recommendations/personal-discovery", async () => {
      await delay(80);
      return HttpResponse.json(delivery([]));
    }));

    renderCatalog(<App />, ["/me/recommendations"]);

    expect(screen.getByRole("status", { name: "추천 영화 불러오는 중" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "지금 보여드릴 추천이 없어요" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "영화 찾아보기" })).toHaveAttribute("href", "/search");
  });

  it("초기 오류에는 retry를 표시한다", async () => {
    server.use(http.get("http://localhost/api/v1/me/recommendations/personal-discovery", () => HttpResponse.json(
      errorBody("UNAUTHORIZED"),
      { status: 401 },
    )));

    renderCatalog(<App />, ["/me/recommendations"]);

    expect(await screen.findByRole("heading", { name: "추천을 불러오지 못했어요" })).toBeInTheDocument();
    expect(screen.getByText("로그인이 만료됐어요. 다시 로그인해 주세요.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
  });

  it("append 실패 시 기존 카드를 제거하지 않는다", async () => {
    const existing = item(1, "보존할 추천");
    server.use(
      http.get("http://localhost/api/v1/me/recommendations/personal-discovery", () => HttpResponse.json(delivery([existing], true))),
      http.post("http://localhost/api/v1/me/recommendation-deliveries/:deliveryId/append", () => HttpResponse.json(
        errorBody("RECOMMENDATION_UNAVAILABLE"),
        { status: 503 },
      )),
    );
    const user = userEvent.setup();
    renderCatalog(<App />, ["/me/recommendations"]);

    expect(await screen.findByRole("heading", { name: "보존할 추천" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "추천 더 보기" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("기존 추천은 그대로 유지했어요");
    expect(screen.getByRole("heading", { name: "보존할 추천" })).toBeInTheDocument();
  });
});
