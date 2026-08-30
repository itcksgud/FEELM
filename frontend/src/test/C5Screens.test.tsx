import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { App } from "../App";
import { renderCatalog } from "./renderCatalog";
import { server } from "./server";

const reportId = "018f6826-4da1-7c38-a846-8f794cd8b0c1";
const exportId = "018f6826-4da1-7c38-a846-8f794cd8b0c2";
const profileId = "018f6826-4da1-7c38-a846-8f794cd8b0c3";
const shareId = "018f6826-4da1-7c38-a846-8f794cd8b0c4";
const notificationId = "018f6826-4da1-7c38-a846-8f794cd8b0c5";
const movie = (name: string, suffix: string) => ({ movieId: `018f6826-4da1-7c38-a846-8f794cd8b0${suffix}`, displayTitle: name, posterUrl: null, watchedAt: "2026-06-01T00:00:00Z", rating: 4 });
const report = (items = [movie("첫 영화", "d1")], hasNext = false, nextCursor: string | null = null) => ({ reportId, periodStart: "2026-01-01", periodEnd: "2026-06-30", revision: 1, status: "READY", createdAt: "2026-07-04T00:00:00Z", metrics: { viewingCount: 2, ratedCount: 1, averageRating: 4 }, periodItems: { totalCount: 2, hasNext, nextCursor, items } });

afterEach(() => { window.history.replaceState({}, "", "/"); vi.restoreAllMocks(); });

describe("C5 local report/profile vertical", () => {
  it("report 목록과 상세 actual movie를 cursor 끝까지 누적한다", async () => {
    server.use(
      http.get("http://localhost/api/v1/me/taste-reports", ({ request }) => new URL(request.url).searchParams.get("cursor") ? HttpResponse.json({ totalCount: 2, hasNext: false, nextCursor: null, items: [{ reportId: `${reportId.slice(0, -1)}9`, periodStart: "2025-07-01", periodEnd: "2025-12-31", revision: 1, status: "EMPTY_NO_ACTIVITY", createdAt: "2026-01-04T00:00:00Z" }] }) : HttpResponse.json({ totalCount: 2, hasNext: true, nextCursor: "reports-2", items: [{ reportId, periodStart: "2026-01-01", periodEnd: "2026-06-30", revision: 1, status: "READY", createdAt: "2026-07-04T00:00:00Z" }] })),
      http.get("http://localhost/api/v1/me/taste-reports/:reportId", ({ request }) => new URL(request.url).searchParams.get("cursor") ? HttpResponse.json(report([movie("둘째 영화", "d2")])) : HttpResponse.json(report([movie("첫 영화", "d1")], true, "movies-2"))),
    );
    const user = userEvent.setup(); renderCatalog(<App />, ["/me/reports"], { c4Token: "owner" });
    expect(await screen.findByText("2026-01-01 ~ 2026-06-30")).toBeInTheDocument(); await user.click(screen.getByRole("button", { name: "다음 페이지" })); expect(await screen.findByText("2025-07-01 ~ 2025-12-31")).toBeInTheDocument();
    await user.click(screen.getAllByRole("link", { name: "전체 보기" })[0]); expect(await screen.findByText("첫 영화")).toBeInTheDocument(); await user.click(screen.getByRole("button", { name: "다음 페이지" })); expect(await screen.findByText("둘째 영화")).toBeInTheDocument(); expect(screen.getByText("첫 영화")).toBeInTheDocument();
  });

  it("report revision을 생성하고 local export READY PDF를 다운로드한다", async () => {
    server.use(
      http.post("http://localhost/api/v1/me/taste-reports", () => HttpResponse.json(report(), { status: 201 })),
      http.get("http://localhost/api/v1/me/taste-reports", () => HttpResponse.json({ totalCount: 0, hasNext: false, nextCursor: null, items: [] })),
      http.get("http://localhost/api/v1/me/taste-reports/:reportId", () => HttpResponse.json(report())),
      http.post("http://localhost/api/v1/me/taste-reports/:reportId/exports", () => HttpResponse.json({ exportId, reportId, status: "PENDING", createdAt: "2026-08-30T00:00:00Z", expiresAt: "2026-08-31T00:00:00Z", downloadHref: null }, { status: 202 })),
      http.get("http://localhost/api/v1/me/report-exports/:exportId", () => HttpResponse.json({ exportId, reportId, status: "READY", createdAt: "2026-08-30T00:00:00Z", expiresAt: "2026-08-31T00:00:00Z", downloadHref: `/api/v1/me/report-exports/${exportId}/content` })),
      http.get("http://localhost/api/v1/me/report-exports/:exportId/content", () => new HttpResponse(new Blob(["%PDF-1.7"]), { headers: { "Content-Type": "application/pdf" } })),
    );
    const user = userEvent.setup(); renderCatalog(<App />, ["/me/reports"], { c4Token: "owner" });
    await user.type(await screen.findByLabelText("반기 시작일"), "2026-01-01"); await user.click(screen.getByRole("button", { name: "새 revision 만들기" }));
    await user.click(await screen.findByRole("link", { name: "PDF 내보내기" })); await user.click(screen.getByRole("button", { name: "PDF 작업 만들기" })); expect(await screen.findByText("PENDING")).toBeInTheDocument(); await user.click(screen.getByRole("button", { name: "상태 새로고침" }));
    const createObjectURL = vi.fn().mockReturnValue("blob:pdf"); Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL }); Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() }); const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await user.click(await screen.findByRole("button", { name: "다운로드" })); await waitFor(() => expect(createObjectURL).toHaveBeenCalled()); expect(click).toHaveBeenCalled();
  });

  it("privacy 3 resources를 exact replace하고 public Film/Popcorn 전체 pagination을 분리한다", async () => {
    let privacyBody: unknown;
    server.use(
      http.get("http://localhost/api/v1/me/privacy-settings", () => HttpResponse.json({ publicProfileId: profileId, revision: 1, resources: [{ resource: "PROFILE", visibility: "PRIVATE" }, { resource: "FILM", visibility: "PRIVATE" }, { resource: "POPCORN", visibility: "PRIVATE" }] })),
      http.put("http://localhost/api/v1/me/privacy-settings", async ({ request }) => { privacyBody = await request.json(); return HttpResponse.json({ publicProfileId: profileId, revision: 2, resources: [{ resource: "PROFILE", visibility: "PUBLIC" }, { resource: "FILM", visibility: "PRIVATE" }, { resource: "POPCORN", visibility: "PRIVATE" }] }); }),
      http.get("http://localhost/api/v1/public/profiles/:profileId", () => HttpResponse.json({ publicProfileId: profileId, nickname: "공개닉네임" })),
      http.get("http://localhost/api/v1/public/profiles/:profileId/film", ({ request }) => new URL(request.url).searchParams.get("cursor") ? HttpResponse.json({ totalCount: 2, hasNext: false, nextCursor: null, items: [{ frameId: `${profileId.slice(0, -1)}8`, movieId: movie("B", "d2").movieId, displayTitle: "Film B", watchedAt: "2026-02-01T00:00:00Z" }] }) : HttpResponse.json({ totalCount: 2, hasNext: true, nextCursor: "film-2", items: [{ frameId: `${profileId.slice(0, -1)}7`, movieId: movie("A", "d1").movieId, displayTitle: "Film A", watchedAt: "2026-01-01T00:00:00Z" }] })),
      http.get("http://localhost/api/v1/public/profiles/:profileId/popcorns", () => HttpResponse.json({ totalCount: 1, hasNext: false, nextCursor: null, items: [{ popcornId: shareId, frameId: profileId, movieId: movie("P", "d3").movieId, displayTitle: "Popcorn A" }] })),
    );
    const user = userEvent.setup(); const rendered = renderCatalog(<App />, ["/me/privacy"], { c4Token: "owner" });
    const profileSelect = (await screen.findByText("PROFILE")).closest("label")!.querySelector("select")!; await user.selectOptions(profileSelect, "PUBLIC"); await user.click(screen.getByRole("button", { name: "전체 설정 저장" })); expect(privacyBody).toEqual({ expectedRevision: 1, resources: [{ resource: "PROFILE", visibility: "PUBLIC" }, { resource: "FILM", visibility: "PRIVATE" }, { resource: "POPCORN", visibility: "PRIVATE" }] });
    rendered.unmount(); renderCatalog(<App />, [`/people/${profileId}`]); expect(await screen.findByRole("heading", { name: "공개닉네임" })).toBeInTheDocument(); expect(screen.getByText("Film A")).toBeInTheDocument(); expect(screen.getByText("Popcorn A")).toBeInTheDocument(); await user.click(screen.getByRole("button", { name: "다음 페이지" })); expect(await screen.findByText("Film B")).toBeInTheDocument(); expect(screen.getByText("Film A")).toBeInTheDocument();
  });

  it("raw-once share를 만들고 revoke한다", async () => {
    let revokeCalls = 0;
    server.use(http.post("http://localhost/api/v1/me/taste-reports/:reportId/shares", () => HttpResponse.json({ shareId, reportId, rawToken: "x".repeat(43), shareHref: `/shared-report#token=${"x".repeat(43)}`, expiresAt: "2026-09-30T00:00:00Z" }, { status: 201 })), http.post("http://localhost/api/v1/me/report-shares/:shareId/revoke", () => { revokeCalls += 1; return new HttpResponse(null, { status: 204 }); }));
    const user = userEvent.setup(); renderCatalog(<App />, [`/me/reports/${reportId}/share`], { c4Token: "owner" }); await user.click(screen.getByRole("button", { name: "공유 링크 한 번 생성" })); expect(await screen.findByText(/다시 조회할 수 없습니다/)).toBeInTheDocument(); expect(document.body.textContent).not.toContain("x".repeat(43)); await user.click(screen.getByRole("button", { name: "링크 복사" })); expect(await navigator.clipboard.readText()).toContain("#token="); await user.click(screen.getByRole("button", { name: "즉시 취소" })); expect(revokeCalls).toBe(1); expect(await screen.findByText(/viewer session을 취소/)).toBeInTheDocument();
  });

  it("shared entry는 hash를 선제 제거하고 body exchange 후 15m memory session으로 pagination한다", async () => {
    let exchangeBody: unknown; const rawToken = "s".repeat(43);
    server.use(
      http.post("http://localhost/api/v1/public/report-shares/exchange", async ({ request }) => { exchangeBody = await request.json(); return HttpResponse.json({ viewerSessionToken: "v".repeat(43), expiresAt: "2026-08-30T00:15:00Z" }); }),
      http.get("http://localhost/api/v1/public/shared-report", ({ request }) => { expect(request.headers.get("X-Report-Viewer-Session")).toBe("v".repeat(43)); return new URL(request.url).searchParams.get("cursor") ? HttpResponse.json({ ownerNickname: "공유자", report: report([movie("공유 둘째", "d2")]) }) : HttpResponse.json({ ownerNickname: "공유자", report: report([movie("공유 첫째", "d1")], true, "shared-2") }); }),
    );
    window.history.pushState({}, "", `/shared-report#token=${rawToken}`); const user = userEvent.setup(); renderCatalog(<App />, [`/shared-report#token=${rawToken}`]); expect(window.location.hash).toBe(""); expect(document.body.textContent).not.toContain(rawToken); expect(await screen.findByText("공유 첫째")).toBeInTheDocument(); expect(exchangeBody).toEqual({ rawToken }); await user.click(screen.getByRole("button", { name: "다음 페이지" })); expect(await screen.findByText("공유 둘째")).toBeInTheDocument(); expect(screen.getByText("공유 첫째")).toBeInTheDocument();
  });

  it("providerless notification opt-in과 read/dismiss를 처리한다", async () => {
    let settingBody: unknown;
    server.use(
      http.get("http://localhost/api/v1/me/notification-settings", () => HttpResponse.json({ watchConfirmationDueEnabled: false, revision: 1 })),
      http.put("http://localhost/api/v1/me/notification-settings", async ({ request }) => { settingBody = await request.json(); return HttpResponse.json({ watchConfirmationDueEnabled: true, revision: 2 }); }),
      http.get("http://localhost/api/v1/me/notifications", () => HttpResponse.json({ totalCount: 1, hasNext: false, nextCursor: null, items: [{ notificationId, category: "WATCH_CONFIRMATION_DUE", state: "UNREAD", message: "감상을 확인해 주세요", createdAt: "2026-08-30T00:00:00Z" }] })),
      http.put("http://localhost/api/v1/me/notifications/:notificationId/state", async ({ request }) => { const body = await request.json() as { state: "READ" | "DISMISSED" }; return HttpResponse.json({ notificationId, category: "WATCH_CONFIRMATION_DUE", state: body.state, message: "감상을 확인해 주세요", createdAt: "2026-08-30T00:00:00Z" }); }),
    );
    const user = userEvent.setup(); renderCatalog(<App />, ["/me/notifications"], { c4Token: "owner" }); const toggle = await screen.findByLabelText("감상 확인 알림 받기"); await user.click(toggle); expect(settingBody).toEqual({ watchConfirmationDueEnabled: true, expectedRevision: 1 }); await user.click(screen.getByRole("button", { name: "읽음" })); expect(await screen.findByText("READ")).toBeInTheDocument(); await user.click(screen.getByRole("button", { name: "숨기기" })); expect(await screen.findByText("DISMISSED")).toBeInTheDocument();
  });
});
