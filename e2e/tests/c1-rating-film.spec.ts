import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const OWNER_AUTHORIZATION = "Bearer test-c1-owner-token";
const WI_PENDING = "2dfa8b82-9f40-452d-a63f-18347483f7b7";
const WI_PENDING_E2E = "8b7f4a21-4bc4-4c5e-93cb-4e348abcae02";
const MOV_KO_FULL = "6b226903-0ca4-4f5a-9bf0-50d6cedd224c";
const MOV_NONE_LISTED = "e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490";
const MOV_OTT_UNKNOWN = "1958ba3a-3d8c-4a4f-8845-124c0b12373e";
const PROVIDER_NETFLIX = "d392a4d5-0428-4e06-aa41-aef899c06842";

type FlavorCode = "ADRENALINE" | "WONDER" | "JOY" | "HEART" | "SHADOW" | "REAL" | "LEGACY" | "RHYTHM";

type PopcornBucket = {
  totalCount: number;
  flavors: Array<{ code: FlavorCode; displayName: string; count: number; averageRating: number | null }>;
};

type FilmPage = {
  totalCount: number;
  items: Array<{ frameId: string; movie: { movieId: string; displayTitle: string } }>;
};

type RatingPage = {
  items: Array<{ rating: { movieId: string; value: number; revision: number }; movie: { displayTitle: string } }>;
};

type UnratedPage = {
  items: Array<{ movie: { movieId: string; displayTitle: string } }>;
};

async function getC1<T>(request: APIRequestContext, path: string): Promise<T> {
  const response = await request.get(path, { headers: { Authorization: OWNER_AUTHORIZATION } });
  expect(response.status(), `${path} requires the C1 endpoints and the approved local fixture`).toBe(200);
  return response.json() as Promise<T>;
}

function recordCard(page: Page, title: string) {
  return page.locator("article").filter({ has: page.getByRole("heading", { level: 2, name: title }) });
}

function frameCard(page: Page, title: string) {
  return page.getByRole("link").filter({ has: page.getByRole("heading", { level: 2, name: title }) });
}

function flavorCard(page: Page, displayName: string) {
  return page.locator("article").filter({ has: page.getByRole("heading", { level: 2, name: displayName }) });
}

async function expectBucketTotal(page: Page, expected: number) {
  const suffix = page.getByText(/알의 취향 · mapping/);
  await expect(suffix).toBeVisible();
  await expect(suffix.locator("xpath=preceding-sibling::strong")).toHaveText(String(expected));
}

const apiError = (code: string, message: string) => ({
  code,
  message,
  traceId: `e2e-${code.toLowerCase()}`,
  fieldErrors: [],
});

/**
 * These mutations share USER-C1-OWNER aggregate rows, so this describe is deliberately serial.
 * Each journey owns a different approved fixture resource:
 *   1. WI-PENDING / MOV-NONE-LISTED
 *   2. WI-PENDING-E2E / MOV-OTT-UNKNOWN
 *   3. RATING-ONE / MOV-KO-FULL
 * No test resets or deletes the database. Run the mutation group once against a freshly migrated
 * local fixture; a failed precondition is reported instead of silently rebuilding state.
 */
test.describe.serial("C1 Rating·Film real browser journeys", () => {
  test("pending → 봤어요 → 정수 별점 → 완료 → Film/Frame → Popcorn (AC-C1-007, AC-C1-011, AC-C1-019, AC-C1-032, AC-C1-034, AC-C1-037)", async ({ page, request }) => {
    const beforeBucket = await getC1<PopcornBucket>(request, "/api/v1/me/popcorn-bucket");
    const beforeHeart = beforeBucket.flavors.find((flavor) => flavor.code === "HEART")?.count ?? 0;

    await page.goto("/me/watch-confirmations");
    await expect(page.getByRole("heading", { level: 1, name: "영화, 잘 보셨나요?" })).toBeVisible();

    const answer = page.locator(`a[href="/me/watch-confirmations/${WI_PENDING}"]`);
    await expect(answer, "WI-PENDING must be the one due owner fixture").toBeVisible();
    const title = (await answer.locator("xpath=ancestor::article").getByRole("heading", { level: 2 }).textContent())?.trim();
    expect(title).toBeTruthy();
    await answer.click();

    await expect(page).toHaveURL(`/me/watch-confirmations/${WI_PENDING}`);
    await page.getByRole("button", { name: "봤어요", exact: true }).click();
    await expect(page).toHaveURL(`/me/movies/${MOV_NONE_LISTED}/rating`);
    await expect(page.getByRole("heading", { level: 1, name: "이 영화는 어땠나요?" })).toBeVisible();

    await page.getByRole("radio", { name: "5점" }).click();
    await expect(page.getByText("내 별점 5/5")).toBeVisible();
    await page.getByRole("button", { name: "저장" }).click();

    await expect(page.getByRole("heading", { level: 1, name: "필름에 추가됐어요" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "내 별점 5/5" })).toBeVisible();
    await page.getByRole("link", { name: "필름 보기" }).click();

    const newFrame = frameCard(page, title!);
    await expect(newFrame).toBeVisible();
    await newFrame.click();
    await expect(page).toHaveURL(/\/me\/film\/frames\/[0-9a-f-]+$/);
    await expect(page.getByRole("heading", { level: 1, name: title! })).toBeVisible();
    await expect(page.getByText("5/5", { exact: true })).toBeVisible();

    await page.getByRole("link", { name: "팝콘" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "한눈에 보는 내 취향" })).toBeVisible();
    await expect(page.locator('[aria-label="팝콘 맛별 기록"] article')).toHaveCount(8);
    await expectBucketTotal(page, beforeBucket.totalCount + 1);

    const afterBucket = await getC1<PopcornBucket>(request, "/api/v1/me/popcorn-bucket");
    expect(afterBucket.flavors.find((flavor) => flavor.code === "HEART")?.count).toBe(beforeHeart + 1);
  });

  test("봤어요 → 나중에 평가 → unrated 유지 → 평가 완료 (AC-C1-011, AC-C1-012, AC-C1-019, AC-C1-051)", async ({ page, request }) => {
    const beforeBucket = await getC1<PopcornBucket>(request, "/api/v1/me/popcorn-bucket");
    const beforeShadow = beforeBucket.flavors.find((flavor) => flavor.code === "SHADOW")?.count ?? 0;

    await page.goto("/me/watch-confirmations");
    const answer = page.locator(`a[href="/me/watch-confirmations/${WI_PENDING_E2E}"]`);
    await expect(answer, "WI-PENDING-E2E must be an independent due owner fixture").toBeVisible();
    await answer.click();
    await page.getByRole("button", { name: "봤어요", exact: true }).click();

    await expect(page).toHaveURL(`/me/movies/${MOV_OTT_UNKNOWN}/rating`);
    await page.getByRole("link", { name: "나중에 평가하기" }).click();
    await expect(page).toHaveURL(/\/me\/ratings\?tab=unrated$/);
    await expect(recordCard(page, "시청 옵션 미확인")).toBeVisible();

    await recordCard(page, "시청 옵션 미확인").getByRole("link", { name: "평가하기" }).click();
    await page.getByRole("radio", { name: "3점" }).click();
    await page.getByRole("button", { name: "저장" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "필름에 추가됐어요" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "내 별점 3/5" })).toBeVisible();

    const afterBucket = await getC1<PopcornBucket>(request, "/api/v1/me/popcorn-bucket");
    expect(afterBucket.totalCount).toBe(beforeBucket.totalCount + 1);
    expect(afterBucket.flavors.find((flavor) => flavor.code === "SHADOW")?.count).toBe(beforeShadow + 1);
  });

  test("rating 수정 → 삭제 후 Film/Popcorn 감소와 unrated 감상 유지 (AC-C1-023, AC-C1-025, AC-C1-026)", async ({ page, request }) => {
    const beforeFilm = await getC1<FilmPage>(request, "/api/v1/me/film");
    const beforeBucket = await getC1<PopcornBucket>(request, "/api/v1/me/popcorn-bucket");
    const beforeShadow = beforeBucket.flavors.find((flavor) => flavor.code === "SHADOW")?.count ?? 0;
    expect(beforeShadow, "RATING-ONE must contribute one SHADOW popcorn before deletion").toBeGreaterThan(0);

    await page.goto("/me/ratings?tab=rated");
    await recordCard(page, "나우 유 씨 미").getByRole("link", { name: "수정" }).click();
    await expect(page.getByRole("radio", { name: "4점" })).toHaveAttribute("aria-checked", "true");
    await page.getByRole("radio", { name: "5점" }).click();
    await page.getByRole("button", { name: "저장" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "내 별점 5/5" })).toBeVisible();

    const updated = await getC1<RatingPage>(request, "/api/v1/me/ratings");
    expect(updated.items.find((item) => item.rating.movieId === MOV_KO_FULL)?.rating.value).toBe(5);

    await page.getByRole("link", { name: "평가", exact: true }).click();
    await recordCard(page, "나우 유 씨 미").getByRole("link", { name: "수정" }).click();
    await page.getByRole("button", { name: "평가 삭제" }).click();
    const dialog = page.getByRole("dialog", { name: "평가를 삭제할까요?" });
    await expect(dialog.getByText(/감상 기록은.*유지/)).toBeVisible();
    await dialog.getByRole("button", { name: "삭제" }).click();

    await expect(page).toHaveURL(/\/me\/ratings\?tab=unrated$/);
    await expect(page.getByText("평가를 삭제했어요. 감상 기록은 유지됩니다.")).toBeVisible();
    await expect(recordCard(page, "나우 유 씨 미")).toBeVisible();

    const afterFilm = await getC1<FilmPage>(request, "/api/v1/me/film");
    const afterBucket = await getC1<PopcornBucket>(request, "/api/v1/me/popcorn-bucket");
    const afterUnrated = await getC1<UnratedPage>(request, "/api/v1/me/viewing-records/unrated");
    expect(afterFilm.totalCount).toBe(beforeFilm.totalCount - 1);
    expect(afterBucket.totalCount).toBe(beforeBucket.totalCount - 1);
    expect(afterBucket.flavors.find((flavor) => flavor.code === "SHADOW")?.count).toBe(beforeShadow - 1);
    expect(afterUnrated.items.some((item) => item.movie.movieId === MOV_KO_FULL)).toBe(true);

    await page.goto("/me/film");
    await expect(page.getByText(`평가를 완료한 영화 ${afterFilm.totalCount}편을 모았어요.`)).toBeVisible();
    await page.goto("/me/popcorn-bucket");
    await expectBucketTotal(page, afterBucket.totalCount);
    await expect(flavorCard(page, "긴장").getByText(`${beforeShadow - 1}편`, { exact: true })).toBeVisible();
  });

  test("createWatchIntent 실패는 외부 이동하지 않고 401·409·503 상태를 구분한다 (AC-C1-003, AC-C1-015, AC-C1-042, AC-C1-045)", async ({ page }) => {
    await page.route("**/api/v1/watch-intents", async (route) => {
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify(apiError("RATING_SERVICE_UNAVAILABLE", "down")) });
    });
    await page.goto(`/movies/${MOV_KO_FULL}`);
    const originalUrl = page.url();
    await page.getByRole("button", { name: /시청 옵션 확인, 외부 페이지로 이동/ }).first().click();
    await expect(page.getByText(/외부 페이지로 이동하지 않았어요/)).toBeVisible();
    await expect(page).toHaveURL(originalUrl);
    await page.unroute("**/api/v1/watch-intents");

    await page.route(/\/api\/v1\/me\/film(?:\?.*)?$/, async (route) => {
      await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify(apiError("UNAUTHORIZED", "unauthorized")) });
    });
    await page.goto("/me/film");
    await expect(page.getByRole("heading", { level: 2, name: "로그인이 필요해요" })).toBeVisible();
    await page.unroute(/\/api\/v1\/me\/film(?:\?.*)?$/);

    await page.route(/\/api\/v1\/me\/watch-intents\/pending-confirmation(?:\?.*)?$/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          totalCount: 1,
          hasNext: false,
          nextCursor: null,
          items: [{
            watchIntentId: WI_PENDING,
            movie: { movieId: MOV_NONE_LISTED, displayTitle: "현재 제공처 없음", posterUrl: null, releaseYear: 2020 },
            provider: { providerId: PROVIDER_NETFLIX, name: "Netflix" },
            clickedAt: "2026-08-27T11:00:00Z",
            confirmationDueAt: "2026-08-29T11:00:00Z",
            expiresAt: "2026-09-03T11:00:00Z",
            revision: 1,
          }],
        }),
      });
    });
    await page.route(`**/api/v1/watch-intents/${WI_PENDING}/confirmation`, async (route) => {
      await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify(apiError("WATCH_INTENT_NOT_CONFIRMABLE", "terminal")) });
    });
    await page.goto(`/me/watch-confirmations/${WI_PENDING}`);
    await page.getByRole("button", { name: "봤어요", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("이미 응답했거나 확인 가능한 시간이 지났어요");

    await page.unrouteAll({ behavior: "wait" });
    await page.route(/\/api\/v1\/me\/popcorn-bucket(?:\?.*)?$/, async (route) => {
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify(apiError("RATING_SERVICE_UNAVAILABLE", "down")) });
    });
    await page.goto("/me/popcorn-bucket");
    const alert = page.getByRole("alert").filter({ has: page.getByRole("heading", { name: "불러오지 못했어요" }) });
    await expect(alert.getByRole("button", { name: "다시 시도" })).toBeVisible();
  });

  test("C1 SPA direct route·reload와 완료 route redirect를 유지한다", async ({ page }) => {
    await page.goto("/me/film");
    await expect(page.getByRole("heading", { level: 1, name: "내 취향 필름" })).toBeVisible();
    await page.reload();
    await expect(page).toHaveURL("/me/film");
    await expect(page.getByRole("heading", { level: 1, name: "내 취향 필름" })).toBeVisible();

    await page.goto("/me/ratings?tab=unrated");
    await page.reload();
    await expect(page.getByRole("tab", { name: "평가 안 남긴 영화" })).toHaveAttribute("aria-selected", "true");

    await page.goto("/me/popcorn-bucket");
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "한눈에 보는 내 취향" })).toBeVisible();

    const pendingCursors: Array<string | null> = [];
    await page.route(/\/api\/v1\/me\/watch-intents\/pending-confirmation(?:\?.*)?$/, async (route) => {
      const cursor = new URL(route.request().url()).searchParams.get("cursor");
      pendingCursors.push(cursor);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(cursor === "pending-page-2" ? {
          totalCount: 21,
          hasNext: false,
          nextCursor: null,
          items: [{
            watchIntentId: WI_PENDING,
            movie: { movieId: MOV_NONE_LISTED, displayTitle: "두 번째 페이지 감상 영화", posterUrl: null, releaseYear: 2020 },
            provider: { providerId: PROVIDER_NETFLIX, name: "Netflix" },
            clickedAt: "2026-08-27T11:00:00Z",
            confirmationDueAt: "2026-08-29T11:00:00Z",
            expiresAt: "2026-09-03T11:00:00Z",
            revision: 1,
          }],
        } : {
          totalCount: 21,
          hasNext: true,
          nextCursor: "pending-page-2",
          items: [],
        }),
      });
    });
    await page.goto(`/me/watch-confirmations/${WI_PENDING}`);
    await expect(page.getByRole("heading", { level: 2, name: "두 번째 페이지 감상 영화" })).toBeVisible();
    expect(pendingCursors).toEqual([null, "pending-page-2"]);
    await page.unroute(/\/api\/v1\/me\/watch-intents\/pending-confirmation(?:\?.*)?$/);

    const ratingCursors: Array<string | null> = [];
    await page.route(/\/api\/v1\/me\/ratings(?:\?.*)?$/, async (route) => {
      const cursor = new URL(route.request().url()).searchParams.get("cursor");
      ratingCursors.push(cursor);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(cursor === "ratings-page-2" ? {
          totalCount: 21,
          hasNext: false,
          nextCursor: null,
          items: [{
            rating: {
              ratingId: "3391dfcf-3e7f-426d-a667-08e1627599d8",
              movieId: MOV_KO_FULL,
              value: 4,
              revision: 2,
              createdAt: "2026-08-29T12:00:00Z",
              updatedAt: "2026-08-29T13:00:00Z",
            },
            movie: { movieId: MOV_KO_FULL, displayTitle: "두 번째 페이지 평가 영화", posterUrl: null, releaseYear: 2013 },
            watchedConfirmedAt: "2026-08-29T12:00:00Z",
            frameId: "dbece9df-5955-4072-8e13-b265f0a09a90",
          }],
        } : {
          totalCount: 21,
          hasNext: true,
          nextCursor: "ratings-page-2",
          items: [],
        }),
      });
    });
    await page.goto(`/me/movies/${MOV_KO_FULL}/rating`);
    await expect(page.getByRole("heading", { level: 1, name: "평가 수정" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "두 번째 페이지 평가 영화" })).toBeVisible();
    await expect(page.getByRole("radio", { name: "4점" })).toHaveAttribute("aria-checked", "true");
    expect(ratingCursors).toEqual([null, "ratings-page-2"]);
    await page.unroute(/\/api\/v1\/me\/ratings(?:\?.*)?$/);

    await page.goto(`/me/rating-complete/${MOV_KO_FULL}`);
    await expect(page).toHaveURL("/me/film");
  });
});
