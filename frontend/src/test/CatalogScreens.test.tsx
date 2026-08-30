import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { MovieCard } from "../components/CatalogUi";
import { FilterSheet } from "../components/FilterSheet";
import { OttAvailabilityContent } from "../components/OttSection";
import { emptyFilters } from "../search/searchState";
import { availability, detail, ids, movieCard, searchPage } from "./fixtures";
import { renderCatalog } from "./renderCatalog";
import { server } from "./server";

describe("C0 Catalog frontend", () => {
  it("느린 이전 검색 응답이 최신 검색 결과를 덮어쓰지 않는다 (AC-CAT-045)", async () => {
    server.use(http.get("http://localhost/api/v1/movies", async ({ request }) => {
      const query = new URL(request.url).searchParams.get("query");
      if (query === "A") {
        await delay(600);
        return HttpResponse.json(searchPage([movieCard({ displayTitle: "느린 A 영화" })]));
      }
      await delay(10);
      return HttpResponse.json(searchPage([movieCard({ movieId: ids.movieTwo, displayTitle: "최신 B 영화" })]));
    }));

    const user = userEvent.setup();
    renderCatalog(<App />, ["/search/results?q=A"]);
    const input = screen.getByRole("textbox", { name: "영화 검색" });
    await user.clear(input);
    await user.type(input, "B");

    expect(await screen.findByText("최신 B 영화", {}, { timeout: 1500 })).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 650));
    expect(screen.queryByText("느린 A 영화")).not.toBeInTheDocument();
  });

  it("상세에서 뒤로 가면 URL 조건과 이미 불러온 페이지를 복원한다 (AC-CAT-046)", async () => {
    let firstPageCalls = 0;
    server.use(
      http.get("http://localhost/api/v1/movies", ({ request }) => {
        const cursor = new URL(request.url).searchParams.get("cursor");
        if (cursor === "page-2") {
          return HttpResponse.json(searchPage([movieCard({ movieId: ids.movieTwo, displayTitle: "두 번째 영화" })]));
        }
        firstPageCalls += 1;
        return HttpResponse.json(searchPage([movieCard({ displayTitle: "첫 번째 영화" })], "page-2"));
      }),
      http.get("http://localhost/api/v1/movies/:movieId", ({ params }) => HttpResponse.json({ ...detail, movieId: String(params.movieId), displayTitle: "두 번째 영화 상세" })),
    );

    const user = userEvent.setup();
    renderCatalog(<App />, [`/search/results?q=마술&genre=${ids.genre}`]);
    expect(await screen.findByText("첫 번째 영화")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "더 보기" }));
    expect(await screen.findByText("두 번째 영화")).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "두 번째 영화 상세 보기" }));
    expect(await screen.findByRole("heading", { name: "두 번째 영화 상세" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "이전 화면으로 돌아가기" }));

    expect(await screen.findByText("두 번째 영화")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "‘마술’ 검색 결과" })).toBeInTheDocument();
    expect(firstPageCalls).toBe(1);
  });

  it("poster null은 로컬 placeholder를 사용하고 레이아웃을 유지한다 (AC-CAT-047)", () => {
    renderCatalog(<MovieCard movie={movieCard({ posterUrl: null, displayTitle: "포스터 없는 영화" })} />);
    expect(screen.getByRole("img", { name: "포스터 없는 영화 포스터 없음" })).toHaveAttribute("src", "/poster-placeholder.svg");
  });

  it("외부 평점은 출처와 척도를 함께 표시하고 예상 별점으로 표현하지 않는다 (AC-CAT-049)", () => {
    renderCatalog(<MovieCard movie={movieCard()} />);
    expect(screen.getByText(/TMDB 7\.3\/10/)).toBeInTheDocument();
    expect(screen.queryByText(/예상 별점/)).not.toBeInTheDocument();
  });

  it("OTT LISTED/FRESH 상태와 기록 후 AGGREGATOR 외부 이동 행동을 구분한다 (AC-CAT-048, AC-CAT-050)", () => {
    renderCatalog(<OttAvailabilityContent availability={availability()} />);
    expect(screen.getByText("구독으로 보기")).toBeInTheDocument();
    expect(screen.getByText(/구독 중/)).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "시청 옵션 확인, 외부 페이지로 이동" });
    expect(button).toHaveTextContent("시청 옵션 확인");
    expect(button).not.toHaveTextContent("Netflix에서 보기");
  });

  it("OTT LISTED/STALE 상태는 스냅샷 기준 시각을 표시한다 (AC-CAT-048)", () => {
    renderCatalog(<OttAvailabilityContent availability={availability({ freshness: "STALE", snapshotAt: "2026-08-26T12:00:00Z" })} />);
    expect(screen.getByText(/정보 기준/)).toBeInTheDocument();
    expect(screen.getByText("Netflix")).toBeInTheDocument();
  });

  it("OTT NONE_LISTED와 UNKNOWN을 서로 다른 문구와 행동으로 표시한다 (AC-CAT-048)", async () => {
    const onRetry = vi.fn();
    const { rerender } = renderCatalog(<OttAvailabilityContent availability={availability({ availabilityStatus: "NONE_LISTED", groups: [] })} />);
    expect(screen.getByText("현재 등록된 한국 시청 옵션이 없어요.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();

    rerender(<OttAvailabilityContent availability={availability({ availabilityStatus: "UNKNOWN", freshness: "UNKNOWN", snapshotAt: null, groups: [] })} onRetry={onRetry} />);
    expect(screen.getByText("OTT 정보를 확인할 수 없어요.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("유효하지 않은 연도 범위는 필터 적용을 막고 field error를 표시한다", async () => {
    const user = userEvent.setup();
    renderCatalog(
      <FilterSheet open value={emptyFilters("")} onClose={vi.fn()} onApply={vi.fn()} />,
    );
    expect(await screen.findByText("범죄")).toBeInTheDocument();
    const from = screen.getByRole("spinbutton", { name: "개봉 연도 시작" });
    const to = screen.getByRole("spinbutton", { name: "개봉 연도 끝" });
    await user.type(from, "2020");
    await user.type(to, "2010");
    expect(screen.getByText("시작 연도는 끝 연도보다 늦을 수 없어요.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "적용하기" })).toBeDisabled();
  });

  it("영문 fallback 줄거리는 한국어 번역이라고 오표기하지 않는다", async () => {
    server.use(http.get("http://localhost/api/v1/movies/:movieId", () => HttpResponse.json({ ...detail, overview: "An English overview.", overviewLocale: "en-US" })));
    renderCatalog(<App />, [`/movies/${ids.movieOne}`]);
    expect(await screen.findByText("An English overview.")).toBeInTheDocument();
    expect(screen.getByText("en-US 원문으로 제공되는 줄거리예요.")).toBeInTheDocument();
    expect(screen.queryByText(/AI 영화 요약/)).not.toBeInTheDocument();
  });

  it("검색 홈에서 빈 인기 결과는 인기 영화 section을 숨긴다", async () => {
    server.use(http.get("http://localhost/api/v1/movies", () => HttpResponse.json(searchPage([]))));
    renderCatalog(<App />);
    await waitFor(() => expect(screen.queryByRole("heading", { name: "인기 영화" })).not.toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: "영화 검색" })).toBeEnabled();
  });
});
