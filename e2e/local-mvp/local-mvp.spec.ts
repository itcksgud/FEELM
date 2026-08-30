import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

test.describe.configure({ mode: "serial" });

const mailpitBaseUrl = process.env.E2E_MAILPIT_URL ?? "http://127.0.0.1:58025";

function allStrings(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(allStrings);
  if (value && typeof value === "object") return Object.values(value).flatMap(allStrings);
  return [];
}

async function latestVerificationHref(request: APIRequestContext): Promise<string> {
  return expect.poll(async () => {
    const listResponse = await request.get(`${mailpitBaseUrl}/api/v1/messages`);
    if (!listResponse.ok()) return "";
    const list = await listResponse.json() as { messages?: Array<Record<string, unknown>>; items?: Array<Record<string, unknown>> } | Array<Record<string, unknown>>;
    const messages = Array.isArray(list) ? list : list.messages ?? list.items ?? [];
    const latest = messages[0];
    const id = latest && String(latest.ID ?? latest.Id ?? latest.id ?? "");
    if (!id) return "";
    const messageResponse = await request.get(`${mailpitBaseUrl}/api/v1/message/${encodeURIComponent(id)}`);
    if (!messageResponse.ok()) return "";
    const body = await messageResponse.json() as unknown;
    const source = allStrings(body).join("\n").replaceAll("&amp;", "&");
    return source.match(/https?:\/\/[^\s"'<>]+\/verify-email[^\s"'<>]+/i)?.[0] ?? "";
  }, { timeout: 30_000, intervals: [250, 500, 1000] }).not.toBe("").then(async () => {
    const list = await (await request.get(`${mailpitBaseUrl}/api/v1/messages`)).json() as { messages?: Array<Record<string, unknown>>; items?: Array<Record<string, unknown>> } | Array<Record<string, unknown>>;
    const messages = Array.isArray(list) ? list : list.messages ?? list.items ?? [];
    const latest = messages[0];
    const id = String(latest.ID ?? latest.Id ?? latest.id);
    const body = await (await request.get(`${mailpitBaseUrl}/api/v1/message/${encodeURIComponent(id)}`)).json() as unknown;
    const source = allStrings(body).join("\n").replaceAll("&amp;", "&");
    const href = source.match(/https?:\/\/[^\s"'<>]+\/verify-email[^\s"'<>]+/i)?.[0];
    if (!href) throw new Error("Mailpit message did not contain a verification link");
    return href;
  });
}

async function loginFromMailpit(page: Page, request: APIRequestContext) {
  const email = "local-mvp-e2e@example.test";
  const password = "local-mvp-password-2026";
  await page.goto("/sign-up");
  await page.getByLabel("이메일").fill(email);
  await page.getByLabel("닉네임").fill("local_mvp_user");
  await page.getByLabel("비밀번호").fill(password);
  await page.getByRole("button", { name: "인증 메일 받기" }).click();
  await expect(page.getByRole("heading", { name: "이메일 인증" })).toBeVisible();

  const verificationHref = await latestVerificationHref(request);
  const verifyPage = await page.context().newPage();
  await verifyPage.goto(verificationHref);
  await expect.poll(() => verifyPage.evaluate(() => window.location.hash)).toBe("");
  await verifyPage.getByRole("button", { name: "인증 완료" }).click();
  await expect(verifyPage.getByRole("heading", { name: "로그인" })).toBeVisible();
  await verifyPage.getByLabel("이메일").fill(email);
  await verifyPage.getByLabel("비밀번호").fill(password);
  await verifyPage.getByRole("button", { name: "로그인" }).click();
  await expect(verifyPage.getByRole("heading", { name: "내 멤버십" })).toBeVisible();
  return verifyPage;
}

test("fresh Compose local MVP: C3, C4, C5 and C6 browser verticals", async ({ page, request, context }) => {
  await test.step("C6 local expected-star, personal utility and taste evidence boundary", async () => {
    await page.goto("/__experiments/recommendation-interpretation");
    await expect(page.getByRole("heading", { name: "추천 해석 실험" })).toBeVisible();
    await expect(page.getByText("직접 측정한 만족도가 아니에요", { exact: false })).toBeVisible();
    await expect(page.getByText("예상 별점 (실험)", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("개인 기준 기대 효용", { exact: true }).first()).toBeVisible();
    await expect.poll(() => page.getByRole("article", { name: /예상 별점 실험 결과/ }).count()).toBeGreaterThan(0);
    await expect(page.getByText("실험 전용 · displayEligible=false").first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "취향 관측 근거" })).toBeVisible();
  });

  await test.step("C3 actual OTT pagination and Party baseline", async () => {
    await page.goto("/me/ott-comparisons/new");
    await expect(page.getByRole("heading", { name: "OTT 영화 목록 비교" })).toBeVisible();
    const providers = page.getByRole("checkbox");
    await expect.poll(() => providers.count()).toBeGreaterThanOrEqual(2);
    await providers.nth(0).check();
    await providers.nth(1).check();
    await page.getByRole("button", { name: "비교하기" }).click();
    await expect(page.getByRole("heading", { name: "OTT 비교 결과" })).toBeVisible();
    await page.getByRole("link", { name: /전체 영화 보기/ }).first().click();
    await expect(page.getByRole("heading", { name: "OTT 전체 영화" })).toBeVisible();
    const initialMovies = await page.getByRole("article").count();
    const more = page.getByRole("button", { name: "더 보기" });
    if (await more.isVisible().catch(() => false)) {
      await more.click();
      await expect.poll(() => page.getByRole("article").count()).toBeGreaterThan(initialMovies);
    }

    await page.goto("/me/parties/new");
    await page.getByLabel("Party 이름").fill("local MVP Party");
    const partyProviders = page.getByRole("checkbox");
    await partyProviders.nth(0).check();
    await partyProviders.nth(1).check();
    await page.getByRole("button", { name: "만들기" }).click();
    await expect(page.getByText("구성원 1/4")).toBeVisible();
    await page.getByRole("link", { name: "인기·OTT 기준 영화 보기" }).click();
    await expect(page.getByRole("heading", { name: /Party 영화/ })).toBeVisible();
  });

  const actorPage = await test.step("C4 Mailpit signup, verification, login, onboarding and profile", async () => {
    const authenticated = await loginFromMailpit(page, request);
    await authenticated.getByRole("link", { name: "취향 설정" }).click();
    await expect(authenticated.getByRole("heading", { name: "첫 취향 남기기" })).toBeVisible();
    await authenticated.getByRole("button", { name: "건너뛰기" }).click();
    await expect(authenticated.getByRole("heading", { name: "이용 중인 OTT" })).toBeVisible();
    const ott = authenticated.getByRole("checkbox");
    if (await ott.count()) await ott.first().check();
    await authenticated.getByRole("button", { name: "구독 목록 저장" }).click();
    await expect(authenticated.getByRole("heading", { name: "준비가 끝났어요" })).toBeVisible();
    await authenticated.getByRole("link", { name: "내 멤버십" }).click();
    await expect(authenticated.getByLabel("닉네임")).toHaveValue("local_mvp_user");
    return authenticated;
  });

  await test.step("C5 empty H1 report, PDF, privacy, share revoke and providerless notification", async () => {
    await actorPage.goto("/me/reports");
    await actorPage.getByLabel("반기 시작일").fill("2026-01-01");
    await actorPage.getByRole("button", { name: "새 revision 만들기" }).click();
    await expect(actorPage.getByRole("heading", { name: "리포트 상세" })).toBeVisible();
    await expect(actorPage.getByText("이 기간의 활동이 없어요.")).toBeVisible();

    await actorPage.getByRole("link", { name: "PDF 내보내기" }).click();
    await actorPage.getByRole("button", { name: "PDF 작업 만들기" }).click();
    const refresh = actorPage.getByRole("button", { name: "상태 새로고침" });
    if (await refresh.isVisible().catch(() => false)) await refresh.click();
    const downloadPromise = actorPage.waitForEvent("download");
    await actorPage.getByRole("button", { name: "다운로드" }).click();
    const download = await downloadPromise;
    expect((await download.createReadStream()) !== null).toBeTruthy();

    await actorPage.goto("/me/privacy");
    const profileSetting = actorPage.getByText("PROFILE", { exact: true }).locator("xpath=ancestor::label").getByRole("combobox");
    await profileSetting.selectOption("PUBLIC");
    await actorPage.getByRole("button", { name: "전체 설정 저장" }).click();
    await actorPage.getByRole("link", { name: "공개 프로필 확인" }).click();
    await expect(actorPage.getByRole("heading", { name: "local_mvp_user" })).toBeVisible();

    await actorPage.goBack();
    await actorPage.goto("/me/reports");
    await actorPage.getByRole("link", { name: "전체 보기" }).first().click();
    await actorPage.getByRole("link", { name: "공유" }).click();
    await actorPage.getByRole("button", { name: "공유 링크 한 번 생성" }).click();
    await actorPage.getByRole("button", { name: "링크 복사" }).click();
    const shareHref = await actorPage.evaluate(() => navigator.clipboard.readText());
    let viewerSession = "";
    const sharedPage = await context.newPage();
    sharedPage.on("request", async (outgoing) => {
      if (outgoing.url().includes("/api/v1/public/shared-report")) viewerSession = (await outgoing.allHeaders())["x-report-viewer-session"] ?? "";
    });
    await sharedPage.goto(shareHref);
    await expect.poll(() => sharedPage.evaluate(() => window.location.hash)).toBe("");
    await expect(sharedPage.getByRole("heading", { name: /반기 리포트/ })).toBeVisible();
    await expect.poll(() => viewerSession.length).toBeGreaterThan(0);
    await actorPage.getByRole("button", { name: "즉시 취소" }).click();
    await expect(actorPage.getByRole("status")).toHaveText("공유와 viewer session을 취소했습니다.");
    const revoked = await request.get("http://127.0.0.1:58080/api/v1/public/shared-report?limit=20", { headers: { "X-Report-Viewer-Session": viewerSession } });
    expect(revoked.status()).toBe(404);

    await actorPage.goto("/me/notifications");
    const notificationToggle = actorPage.getByLabel("감상 확인 알림 받기");
    await notificationToggle.click();
    await expect(notificationToggle).toBeChecked();
    await expect(actorPage.getByText("새 알림이 없어요.")).toBeVisible();
    await actorPage.goto("/me/profile");
    await actorPage.getByRole("button", { name: "로그아웃" }).click();
    await expect(actorPage.getByRole("heading", { name: "로그인" })).toBeVisible();
  });
});
