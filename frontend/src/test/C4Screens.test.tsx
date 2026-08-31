import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { App } from "../App";
import { renderCatalog } from "./renderCatalog";
import { server } from "./server";
import { providers } from "./fixtures";
import { classifyPreferenceDistance } from "../pages/C4Pages";

const signupId = "018f6826-4da1-7c38-a846-8f794cd8b0cf";
const movieId = "018f6826-4da1-7c38-a846-8f794cd8b0d0";
const membership = {
  membershipStatus: "ACTIVE" as const,
  emailMasked: "te***@example.com",
  nickname: "film_user",
  profileRevision: 1,
  onboarding: { status: "NOT_STARTED" as const, preferenceCount: 0, revision: 2 },
};
const authentication = { tokenType: "Bearer" as const, accessToken: "memory-only-access", expiresInSeconds: 600, membership };

describe("C4 onboarding distance rule", () => {
  it("원의 경계까지 LIKE이고 경계를 넘으면 DISLIKE다", () => {
    expect(classifyPreferenceDistance(0, 146)).toBe("LIKE");
    expect(classifyPreferenceDistance(146, 146)).toBe("LIKE");
    expect(classifyPreferenceDistance(146.01, 146)).toBe("DISLIKE");
  });
});

afterEach(() => {
  window.history.replaceState({}, "", "/");
  document.cookie = "feelm_local_csrf=; Max-Age=0; Path=/";
});

describe("C4 local membership vertical", () => {
  it("protected direct route는 local cookie 경계의 refresh로 메모리 세션을 복구한다", async () => {
    server.use(
      http.post("http://localhost/api/v1/auth/refresh", ({ request }) => {
        expect(request.credentials).toBe("include");
        expect(request.headers.get("X-CSRF-Token")).toBe("csrf-local");
        return HttpResponse.json(authentication);
      }),
      http.get("http://localhost/api/v1/me", () => HttpResponse.json(membership)),
    );
    document.cookie = "feelm_local_csrf=csrf-local; Path=/";
    renderCatalog(<App />, ["/me/profile"]);
    expect(await screen.findByRole("heading", { name: "내 멤버십" })).toBeInTheDocument();
  });

  it("회원가입 후 raw secret 없는 Mailpit 안내로 이동한다", async () => {
    const requestBody = vi.fn();
    server.use(http.post("http://localhost/api/v1/auth/sign-up", async ({ request }) => {
      requestBody(await request.json());
      expect(request.headers.get("Idempotency-Key")).toBeTruthy();
      return HttpResponse.json({ signupId, membershipStatus: "PENDING_EMAIL_VERIFICATION", emailMasked: "te***@example.com", deliveryStatus: "QUEUED", verificationExpiresAt: "2026-08-30T01:10:00Z", resendAvailableAt: "2026-08-30T01:01:00Z", revision: 1 }, { status: 202 });
    }));
    const user = userEvent.setup(); renderCatalog(<App />, ["/sign-up"]);
    await user.type(screen.getByLabelText("이메일"), "test@example.com");
    await user.type(screen.getByLabelText("닉네임"), "film_user");
    await user.type(screen.getByLabelText("비밀번호"), "long-enough-password");
    await user.click(screen.getByRole("button", { name: "인증 메일 받기" }));
    expect(await screen.findByRole("heading", { name: "이메일 인증" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Mailpit 받은편지함 열기" })).toHaveAttribute("href", "http://localhost:8025");
    expect(requestBody).toHaveBeenCalledWith({ email: "test@example.com", nickname: "film_user", password: "long-enough-password" });
    expect(document.body.textContent).not.toContain("verificationSecret");
  });

  it("fragment secret을 즉시 지운 뒤 verification POST body로만 전송한다", async () => {
    let posted: unknown;
    server.use(http.post("http://localhost/api/v1/auth/email-verifications", async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json({ membershipStatus: "ACTIVE", emailMasked: "te***@example.com", nextAction: "LOGIN", revision: 2 });
    }));
    window.history.pushState({}, "", `/verify-email?signupId=${signupId}#verificationSecret=raw-secret-value`);
    const user = userEvent.setup(); renderCatalog(<App />, [`/verify-email?signupId=${signupId}#verificationSecret=raw-secret-value`]);
    expect(window.location.hash).toBe("");
    expect(document.body.textContent).not.toContain("raw-secret-value");
    await user.click(screen.getByRole("button", { name: "인증 완료" }));
    expect(await screen.findByRole("heading", { name: "로그인" })).toBeInTheDocument();
    expect(posted).toEqual({ signupId, verificationSecret: "raw-secret-value" });
  });

  it("access token을 브라우저 저장소에 쓰지 않고 membership과 logout을 처리한다", async () => {
    const localStorageSpy = vi.spyOn(Storage.prototype, "setItem");
    server.use(
      http.post("http://localhost/api/v1/auth/login", () => HttpResponse.json(authentication)),
      http.get("http://localhost/api/v1/me", () => HttpResponse.json(membership)),
      http.post("http://localhost/api/v1/auth/logout", () => new HttpResponse(null, { status: 204 })),
    );
    document.cookie = "feelm_local_csrf=csrf-local; Path=/";
    const user = userEvent.setup(); renderCatalog(<App />, ["/login"]);
    await user.type(screen.getByLabelText("이메일"), "test@example.com"); await user.type(screen.getByLabelText("비밀번호"), "long-enough-password");
    await user.click(screen.getByRole("button", { name: "로그인" }));
    expect(await screen.findByRole("heading", { name: "내 멤버십" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("film_user")).toBeInTheDocument();
    expect(localStorageSpy).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "로그아웃" }));
    expect(await screen.findByRole("heading", { name: "로그인" })).toBeInTheDocument();
    localStorageSpy.mockRestore();
  });

  it("LIKE 1개와 KR OTT 전체 교체 후 onboarding을 완료한다", async () => {
    let preferencesBody: unknown; let ottBody: unknown; let completionBody: unknown;
    server.use(
      http.post("http://localhost/api/v1/auth/login", () => HttpResponse.json(authentication)),
      http.get("http://localhost/api/v1/me", () => HttpResponse.json(membership)),
      http.get("http://localhost/api/v1/onboarding/movies", () => HttpResponse.json({ catalogVersion: "catalog-v1", selectionPolicyVersion: "local-baseline-v1", targetCount: 1, items: [{ movieId, title: "실제 영화", posterUrl: null }] })),
      http.put("http://localhost/api/v1/onboarding/preferences", async ({ request }) => { preferencesBody = await request.json(); expect(request.headers.get("X-Expected-Revision")).toBe("2"); return HttpResponse.json({ status: "IN_PROGRESS", preferenceCount: 1, likeCount: 1, dislikeCount: 0, requiredPreferenceCount: 1, maximumPreferenceCount: 10, revision: 3, recommendationProjection: "NOT_REQUESTED" }); }),
      http.get("http://localhost/api/v1/me/ott-subscriptions", () => HttpResponse.json({ region: "KR", selectionStatus: "NOT_CONFIGURED", providerIds: [], revision: 1 })),
      http.put("http://localhost/api/v1/me/ott-subscriptions", async ({ request }) => { ottBody = await request.json(); return HttpResponse.json({ region: "KR", selectionStatus: "CONFIGURED", providerIds: ["018f6826-4da1-7c38-a846-8f794cd8b0cf"], revision: 2 }); }),
      http.post("http://localhost/api/v1/onboarding/complete", async ({ request }) => { completionBody = await request.json(); return HttpResponse.json({ status: "COMPLETED", preferenceCount: 1, likeCount: 1, dislikeCount: 0, requiredPreferenceCount: 1, maximumPreferenceCount: 10, revision: 4, recommendationProjection: "PENDING" }); }),
    );
    const user = userEvent.setup(); renderCatalog(<App />, ["/login"]);
    await user.type(screen.getByLabelText("이메일"), "test@example.com"); await user.type(screen.getByLabelText("비밀번호"), "long-enough-password"); await user.click(screen.getByRole("button", { name: "로그인" }));
    await user.click(await screen.findByRole("link", { name: "취향 설정" }));
    await user.click(await screen.findByRole("button", { name: "좋아요" })); await user.click(screen.getByRole("button", { name: "선택 저장 (1)" }));
    await screen.findByRole("heading", { name: "이용 중인 OTT" });
    await user.click(screen.getByLabelText("Netflix")); await user.click(screen.getByRole("button", { name: "구독 목록 저장" }));
    expect(await screen.findByRole("heading", { name: "준비가 끝났어요" })).toBeInTheDocument();
    expect(preferencesBody).toEqual({ catalogVersion: "catalog-v1", selectionPolicyVersion: "local-baseline-v1", preferences: [{ movieId, preference: "LIKE" }] });
    expect(ottBody).toEqual({ selectionMode: "CONFIGURED", providerIds: [providers.items[0].providerId] });
    expect(completionBody).toEqual({ completionMode: "SUBMITTED", expectedPreferenceCount: 1 });
  });

  it("0개 건너뛰기는 preference PUT 없이 SKIPPED completion으로 끝낸다", async () => {
    let preferenceCalls = 0; let completionBody: unknown;
    server.use(
      http.post("http://localhost/api/v1/auth/login", () => HttpResponse.json(authentication)),
      http.get("http://localhost/api/v1/me", () => HttpResponse.json(membership)),
      http.get("http://localhost/api/v1/onboarding/movies", () => HttpResponse.json({ catalogVersion: "catalog-v1", selectionPolicyVersion: "local-baseline-v1", targetCount: 0, items: [] })),
      http.put("http://localhost/api/v1/onboarding/preferences", () => { preferenceCalls += 1; return HttpResponse.json({}); }),
      http.get("http://localhost/api/v1/me/ott-subscriptions", () => HttpResponse.json({ region: "KR", selectionStatus: "NOT_CONFIGURED", providerIds: [], revision: 1 })),
      http.put("http://localhost/api/v1/me/ott-subscriptions", () => HttpResponse.json({ region: "KR", selectionStatus: "SKIPPED", providerIds: [], revision: 2 })),
      http.post("http://localhost/api/v1/onboarding/complete", async ({ request }) => { completionBody = await request.json(); return HttpResponse.json({ status: "SKIPPED", preferenceCount: 0, likeCount: 0, dislikeCount: 0, requiredPreferenceCount: null, maximumPreferenceCount: 10, revision: 3, recommendationProjection: "NOT_REQUESTED" }); }),
    );
    const user = userEvent.setup(); renderCatalog(<App />, ["/login"]);
    await user.type(screen.getByLabelText("이메일"), "test@example.com"); await user.type(screen.getByLabelText("비밀번호"), "long-enough-password"); await user.click(screen.getByRole("button", { name: "로그인" }));
    await user.click(await screen.findByRole("link", { name: "취향 설정" })); await user.click(await screen.findByRole("button", { name: "건너뛰기" }));
    await user.click(await screen.findByRole("button", { name: "OTT 선택 건너뛰기" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "준비가 끝났어요" })).toBeInTheDocument());
    expect(preferenceCalls).toBe(0); expect(completionBody).toEqual({ completionMode: "SKIPPED", expectedPreferenceCount: 0 });
  });
});
