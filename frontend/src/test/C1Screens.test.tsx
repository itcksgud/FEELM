import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { OttAvailabilityContent } from "../components/OttSection";
import {
  availability,
  filmPage,
  ids,
  pendingConfirmations,
  popcornBucket,
  ratingPage,
  ratingMutationResult,
  tasteProfile,
  unratedViewingRecords,
  watchIntentClickResult,
} from "./fixtures";
import { renderCatalog } from "./renderCatalog";
import { server } from "./server";

function apiError(code: string, message: string) {
  return { code, message, traceId: "trace-c1-test", fieldErrors: [] };
}

describe("C1 Rating·Film frontend", () => {
  it("pending 확인 목록에서 소유 영화와 OTT·클릭 시각을 표시하고 확인 화면으로 이동한다 (AC-C1-007, AC-C1-042)", async () => {
    const user = userEvent.setup();
    renderCatalog(<App />, ["/me/watch-confirmations"]);

    expect(await screen.findByText("나우 유 씨 미")).toBeInTheDocument();
    expect(screen.getByText(/Netflix/)).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "답하기" }));
    expect(await screen.findByRole("heading", { name: "영화, 잘 보셨나요?" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "봤어요" })).toBeEnabled();
  });

  it("watched=true는 revision·멱등 key와 함께 저장하고 Rating을 강제 생성하지 않은 편집기로 이동한다 (AC-C1-011, AC-C1-013, AC-C1-051)", async () => {
    let capturedKey = "";
    let capturedAuthorization = "";
    let capturedBody: unknown;
    server.use(http.post("http://localhost/api/v1/watch-intents/:watchIntentId/confirmation", async ({ request }) => {
      capturedKey = request.headers.get("Idempotency-Key") ?? "";
      capturedAuthorization = request.headers.get("Authorization") ?? "";
      capturedBody = await request.json();
      return HttpResponse.json({
        watchIntentId: ids.watchIntent,
        status: "CONFIRMED_WATCHED",
        respondedAt: "2026-08-29T12:00:00Z",
        revision: 2,
        viewingRecord: {
          viewingRecordId: ids.viewingRecord,
          movieId: ids.movieOne,
          status: "WATCHED_CONFIRMED",
          watchedConfirmedAt: "2026-08-29T12:00:00Z",
          provider: { providerId: ids.provider, name: "Netflix" },
          revision: 1,
        },
      });
    }));
    const user = userEvent.setup();
    renderCatalog(<App />, [`/me/watch-confirmations/${ids.watchIntent}`]);

    await user.click(await screen.findByRole("button", { name: "봤어요" }));
    expect(await screen.findByRole("heading", { name: "이 영화는 어땠나요?" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "나중에 평가하기" })).toBeInTheDocument();
    expect(capturedAuthorization).toBe("Bearer test-c1-owner-token");
    expect(capturedKey).toMatch(/^[!-~]{8,128}$/);
    expect(capturedBody).toEqual({ watched: true, expectedRevision: 1 });
  });

  it("감상 확인 deep link는 cursor를 끝까지 순회해 두 번째 page의 owner item을 찾는다", async () => {
    const cursors: Array<string | null> = [];
    server.use(http.get("http://localhost/api/v1/me/watch-intents/pending-confirmation", ({ request }) => {
      const cursor = new URL(request.url).searchParams.get("cursor");
      cursors.push(cursor);
      if (cursor === "pending-page-2") return HttpResponse.json(pendingConfirmations);
      return HttpResponse.json({
        totalCount: 21,
        hasNext: true,
        nextCursor: "pending-page-2",
        items: [],
      });
    }));

    renderCatalog(<App />, [`/me/watch-confirmations/${ids.watchIntent}`]);

    expect(await screen.findByRole("heading", { name: "영화, 잘 보셨나요?" })).toBeInTheDocument();
    expect(screen.getByText("나우 유 씨 미")).toBeInTheDocument();
    expect(cursors).toEqual([null, "pending-page-2"]);
  });

  it("평가 수정 deep link는 ratings cursor를 순회하고 첫 page 누락을 404로 오판하지 않는다", async () => {
    const ratingCursors: Array<string | null> = [];
    let unratedCalls = 0;
    server.use(
      http.get("http://localhost/api/v1/me/ratings", ({ request }) => {
        const cursor = new URL(request.url).searchParams.get("cursor");
        ratingCursors.push(cursor);
        if (cursor === "ratings-page-2") return HttpResponse.json(ratingPage);
        return HttpResponse.json({ totalCount: 21, hasNext: true, nextCursor: "ratings-page-2", items: [] });
      }),
      http.get("http://localhost/api/v1/me/viewing-records/unrated", () => {
        unratedCalls += 1;
        return HttpResponse.json(unratedViewingRecords);
      }),
    );

    renderCatalog(<App />, [`/me/movies/${ids.movieOne}/rating`]);

    expect(await screen.findByRole("heading", { name: "평가 수정" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "4점" })).toHaveAttribute("aria-checked", "true");
    expect(ratingCursors).toEqual([null, "ratings-page-2"]);
    expect(unratedCalls).toBe(0);
  });

  it("미평가 deep link는 ratings 전체 확인 뒤 unrated cursor의 owner item까지 순회한다", async () => {
    const unratedCursors: Array<string | null> = [];
    server.use(
      http.get("http://localhost/api/v1/me/ratings", () => HttpResponse.json({
        totalCount: 0,
        hasNext: false,
        nextCursor: null,
        items: [],
      })),
      http.get("http://localhost/api/v1/me/viewing-records/unrated", ({ request }) => {
        const cursor = new URL(request.url).searchParams.get("cursor");
        unratedCursors.push(cursor);
        if (cursor === "unrated-page-2") return HttpResponse.json(unratedViewingRecords);
        return HttpResponse.json({ totalCount: 21, hasNext: true, nextCursor: "unrated-page-2", items: [] });
      }),
    );

    renderCatalog(<App />, [`/me/movies/${ids.movieOne}/rating`]);

    expect(await screen.findByRole("heading", { name: "이 영화는 어땠나요?" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "나중에 평가하기" })).toBeInTheDocument();
    expect(unratedCursors).toEqual([null, "unrated-page-2"]);
  });

  it("정수 1~5 picker는 키보드로 조작되고 503 retry에 같은 key를 쓰며 선택값을 유지한다 (AC-C1-020, AC-C1-022, AC-C1-052, AC-C1-053)", async () => {
    const keys: string[] = [];
    let calls = 0;
    server.use(http.put("http://localhost/api/v1/me/ratings/:movieId", async ({ request }) => {
      keys.push(request.headers.get("Idempotency-Key") ?? "");
      calls += 1;
      if (calls === 1) return HttpResponse.json(apiError("RATING_SERVICE_UNAVAILABLE", "temporary"), { status: 503 });
      const body = await request.json() as { value: number };
      return HttpResponse.json({ ...ratingMutationResult, rating: { ...ratingMutationResult.rating, value: body.value } });
    }));
    const user = userEvent.setup();
    renderCatalog(<App />, ["/me/ratings?tab=unrated"]);
    await user.click(await screen.findByRole("link", { name: "평가하기" }));

    const first = await screen.findByRole("radio", { name: "1점" });
    first.focus();
    await user.keyboard("{ArrowRight}{ArrowRight}{ArrowRight}{ArrowRight}");
    expect(screen.getByRole("radio", { name: "5점" })).toHaveAttribute("aria-checked", "true");
    await user.click(screen.getByRole("button", { name: "저장" }));
    expect(await screen.findByText(/평가 서비스를 잠시 이용할 수 없어요/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "5점" })).toHaveAttribute("aria-checked", "true");
    await user.click(screen.getByRole("button", { name: "저장" }));

    expect(await screen.findByRole("heading", { name: "필름에 추가됐어요" })).toBeInTheDocument();
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
    expect(keys[0]).toMatch(/^[!-~]{8,128}$/);
  });

  it("400 field error는 선택값을 보존하고 성공 화면을 표시하지 않는다 (AC-C1-020, AC-C1-053)", async () => {
    server.use(http.put("http://localhost/api/v1/me/ratings/:movieId", () => HttpResponse.json({
      ...apiError("VALIDATION_ERROR", "invalid"),
      fieldErrors: [{ field: "value", reason: "1~5 정수만 입력해 주세요." }],
    }, { status: 400 })));
    const user = userEvent.setup();
    renderCatalog(<App />, ["/me/ratings?tab=unrated"]);
    await user.click(await screen.findByRole("link", { name: "평가하기" }));
    await user.click(screen.getByRole("radio", { name: "4점" }));
    await user.click(screen.getByRole("button", { name: "저장" }));
    expect(await screen.findByText("1~5 정수만 입력해 주세요.")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "4점" })).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByRole("heading", { name: "필름에 추가됐어요" })).not.toBeInTheDocument();
  });

  it("update revision 충돌은 stale 값으로 덮지 않고 최신 값 reload 행동을 제공한다 (AC-C1-024, AC-C1-054)", async () => {
    let headerRevision = "";
    server.use(http.put("http://localhost/api/v1/me/ratings/:movieId", ({ request }) => {
      headerRevision = request.headers.get("X-Expected-Revision") ?? "";
      return HttpResponse.json(apiError("REVISION_CONFLICT", "conflict"), { status: 409 });
    }));
    const user = userEvent.setup();
    renderCatalog(<App />, ["/me/ratings"]);
    await user.click(await screen.findByRole("link", { name: "수정" }));
    await user.click(screen.getByRole("radio", { name: "5점" }));
    await user.click(screen.getByRole("button", { name: "저장" }));
    expect(await screen.findByRole("button", { name: "최신 값 다시 불러오기" })).toBeInTheDocument();
    expect(screen.getByText(/다른 곳에서 내용이 변경됐어요/)).toBeInTheDocument();
    expect(headerRevision).toBe("");
  });

  it("Rating 삭제는 expected revision을 보내고 감상 기록 유지·projection 제거 의미를 확인한다 (AC-C1-025, AC-C1-026)", async () => {
    let revision = "";
    let key = "";
    server.use(http.delete("http://localhost/api/v1/me/ratings/:movieId", ({ request }) => {
      revision = request.headers.get("X-Expected-Revision") ?? "";
      key = request.headers.get("Idempotency-Key") ?? "";
      return HttpResponse.json({
        movieId: ids.movieOne,
        ratingRemoved: true,
        viewingStatus: "WATCHED_CONFIRMED",
        frameActive: false,
        popcornActive: false,
        filmTotalCount: 0,
        aggregateRevision: 4,
        recommendationRefresh: "QUEUED",
      });
    }));
    const user = userEvent.setup();
    renderCatalog(<App />, ["/me/ratings"]);
    await user.click(await screen.findByRole("link", { name: "수정" }));
    await user.click(screen.getByRole("button", { name: "평가 삭제" }));
    const dialog = screen.getByRole("dialog", { name: "평가를 삭제할까요?" });
    expect(within(dialog).getByText(/감상 기록은.*유지/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "삭제" }));
    expect(await screen.findByText("평가를 삭제했어요. 감상 기록은 유지됩니다.")).toBeInTheDocument();
    expect(revision).toBe("2");
    expect(key).toMatch(/^[!-~]{8,128}$/);
  });

  it("Film 전체 count와 Frame 상세의 내 평가·감상 사실만 표시한다 (AC-C1-032, AC-C1-034)", async () => {
    const user = userEvent.setup();
    renderCatalog(<App />, ["/me/film"]);
    expect(await screen.findByText("평가를 완료한 영화 1편을 모았어요.")).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: /나우 유 씨 미/ }));
    expect(await screen.findByRole("heading", { name: "나우 유 씨 미" })).toBeInTheDocument();
    expect(screen.getByText("4/5")).toBeInTheDocument();
    expect(screen.getByText("Netflix")).toBeInTheDocument();
    expect(screen.queryByText(/한 줄/)).not.toBeInTheDocument();
  });

  it("Popcorn 8 flavors의 count·평균과 raw TasteProfile을 분리해 표시한다 (AC-C1-037, AC-C1-038, AC-C1-039, AC-C1-055)", async () => {
    renderCatalog(<App />, ["/me/popcorn-bucket"]);
    expect(await screen.findByText("긴장")).toBeInTheDocument();
    for (const flavor of popcornBucket.flavors) expect(screen.getByRole("heading", { name: flavor.displayName })).toBeInTheDocument();
    expect(screen.getByText("내 평균 4.0/5")).toBeInTheDocument();
    expect(screen.getAllByText("내 평균 평가 없음")).toHaveLength(7);
    expect(await screen.findByText("취향 원천 집계")).toBeInTheDocument();
    for (const item of tasteProfile.items) expect(screen.getByText(item.displayName)).toBeInTheDocument();
    expect(screen.queryByText(/취향 점수|키워드/)).not.toBeInTheDocument();
  });

  it("OTT 클릭은 createWatchIntent 성공 전 이동하지 않고 server destination만 사용한다 (AC-C1-001, AC-C1-002)", async () => {
    let key = "";
    let body: unknown;
    server.use(http.post("http://localhost/api/v1/watch-intents", async ({ request }) => {
      key = request.headers.get("Idempotency-Key") ?? "";
      body = await request.json();
      await delay(80);
      return HttpResponse.json({
        ...watchIntentClickResult,
        destination: { ...watchIntentClickResult.destination, url: "https://destination.example/watch" },
      }, { status: 201 });
    }));
    const navigateExternal = vi.fn();
    const user = userEvent.setup();
    renderCatalog(<OttAvailabilityContent availability={availability()} navigateExternal={navigateExternal} />);
    await user.click(screen.getByRole("button", { name: "시청 옵션 확인, 외부 페이지로 이동" }));
    expect(navigateExternal).not.toHaveBeenCalled();
    await waitFor(() => expect(navigateExternal).toHaveBeenCalledWith("https://destination.example/watch"));
    expect(key).toMatch(/^[!-~]{8,128}$/);
    expect(body).toEqual({ movieId: ids.movieOne, offerId: ids.offer });
  });

  it("OTT 기록 실패는 이동하지 않고 같은 offer retry에 같은 Idempotency-Key를 쓴다 (AC-C1-003, AC-C1-004)", async () => {
    const keys: string[] = [];
    server.use(http.post("http://localhost/api/v1/watch-intents", ({ request }) => {
      keys.push(request.headers.get("Idempotency-Key") ?? "");
      return HttpResponse.json(apiError("RATING_SERVICE_UNAVAILABLE", "down"), { status: 503 });
    }));
    const navigateExternal = vi.fn();
    const user = userEvent.setup();
    renderCatalog(<OttAvailabilityContent availability={availability()} navigateExternal={navigateExternal} />);
    const button = screen.getByRole("button", { name: "시청 옵션 확인, 외부 페이지로 이동" });
    await user.click(button);
    expect(await screen.findByText(/외부 페이지로 이동하지 않았어요/)).toBeInTheDocument();
    await user.click(button);
    await waitFor(() => expect(keys).toHaveLength(2));
    expect(keys[0]).toBe(keys[1]);
    expect(navigateExternal).not.toHaveBeenCalled();
  });

  it("loading과 empty 상태를 구분해 표시한다 (AC-C1-036)", async () => {
    server.use(http.get("http://localhost/api/v1/me/film", async () => {
      await delay(80);
      return HttpResponse.json({ ...filmPage, totalCount: 0, items: [] });
    }));
    renderCatalog(<App />, ["/me/film"]);
    expect(screen.getByRole("status", { name: /필름 불러오는 중/ })).toBeInTheDocument();
    expect(await screen.findByText("아직 필름에 추가된 영화가 없어요")).toBeInTheDocument();
  });

  it("401·404·503를 로그인, 숨김 not-found, retry 가능한 장애 상태로 구분한다 (AC-C1-042, AC-C1-045)", async () => {
    server.use(http.get("http://localhost/api/v1/me/watch-intents/pending-confirmation", () => HttpResponse.json(apiError("UNAUTHORIZED", "unauthorized"), { status: 401 })));
    const first = renderCatalog(<App />, ["/me/watch-confirmations"]);
    expect(await screen.findByRole("heading", { name: "로그인이 필요해요" })).toBeInTheDocument();
    first.unmount();

    server.use(http.get("http://localhost/api/v1/me/film/frames/:frameId", () => HttpResponse.json(apiError("RESOURCE_NOT_FOUND", "not found"), { status: 404 })));
    const second = renderCatalog(<App />, [`/me/film/frames/${ids.frame}`]);
    expect(await screen.findByRole("heading", { name: "항목을 찾을 수 없어요" })).toBeInTheDocument();
    second.unmount();

    server.use(http.get("http://localhost/api/v1/me/popcorn-bucket", () => HttpResponse.json(apiError("RATING_SERVICE_UNAVAILABLE", "down"), { status: 503 })));
    renderCatalog(<App />, ["/me/popcorn-bucket"]);
    expect(await screen.findByRole("button", { name: "다시 시도" }, { timeout: 3_000 })).toBeInTheDocument();
  });
});
