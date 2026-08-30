import { expect, test, type Locator, type Page } from "@playwright/test";

const TARGETS = new Map([
  ["e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490", "2dfa8b82-9f40-452d-a63f-18347483f7b7"],
  ["1958ba3a-3d8c-4a4f-8845-124c0b12373e", "8b7f4a21-4bc4-4c5e-93cb-4e348abcae02"],
]);

function recommendationCards(page: Page): Locator {
  return page.locator('section[aria-labelledby="recommendation-list-title"] article');
}

async function movieIds(cards: Locator): Promise<string[]> {
  return cards.locator('a[href^="/movies/"]').evaluateAll((links) => links.map((link) => {
    const href = link.getAttribute("href") ?? "";
    return href.replace("/movies/", "");
  }));
}

test("real Compose C2B collection: initial 3 → append → dismiss → viewing-only 유지 → Rating 이탈", async ({ context, page }) => {
  await page.goto("/me/recommendations");
  await expect(page.getByRole("heading", { level: 1, name: "오늘의 영화 추천" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "인기 기준 추천" })).toBeVisible();

  const cards = recommendationCards(page);
  await expect(cards).toHaveCount(3);
  const initialMovieIds = await movieIds(cards);
  expect(new Set(initialMovieIds).size).toBe(3);

  await page.getByRole("button", { name: "추천 더 보기" }).click();
  await expect(cards).toHaveCount(5);
  const appendedMovieIds = await movieIds(cards);
  expect(appendedMovieIds.slice(0, 3)).toEqual(initialMovieIds);
  expect(new Set(appendedMovieIds).size).toBe(5);
  await expect(page.getByRole("status")).toContainText("준비된 추천을 모두 확인했어요");

  const targetMovieId = appendedMovieIds.find((movieId) => TARGETS.has(movieId));
  expect(targetMovieId, "fresh V100/V101 fixtures must expose one confirmable recommendation").toBeTruthy();
  const watchIntentId = TARGETS.get(targetMovieId!);
  expect(watchIntentId).toBeTruthy();

  const dismissMovieId = appendedMovieIds.find((movieId) => !TARGETS.has(movieId));
  expect(dismissMovieId, "fixture must include an independent dismissal candidate").toBeTruthy();
  const dismissedCard = cards.filter({ has: page.locator(`a[href="/movies/${dismissMovieId}"]`) });
  await dismissedCard.getByRole("button", { name: "관심 없음" }).click();
  await expect(cards).toHaveCount(4);
  await expect(page.locator(`a[href="/movies/${dismissMovieId}"]`)).toHaveCount(0);

  await page.goto(`/me/watch-confirmations/${watchIntentId}`);
  await page.getByRole("button", { name: "봤어요", exact: true }).click();
  await expect(page).toHaveURL(`/me/movies/${targetMovieId}/rating`);

  const viewingOnlyPage = await context.newPage();
  await viewingOnlyPage.goto("/me/recommendations");
  await expect(viewingOnlyPage.locator(`a[href="/movies/${targetMovieId}"]`)).toBeVisible();
  await expect(recommendationCards(viewingOnlyPage)).toHaveCount(4);
  await viewingOnlyPage.close();

  await page.getByRole("radio", { name: "4점" }).click();
  await page.getByRole("button", { name: "저장" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "필름에 추가됐어요" })).toBeVisible();

  await page.goto("/me/recommendations");
  await expect(page.locator(`a[href="/movies/${targetMovieId}"]`)).toHaveCount(0);
  await expect(page.locator(`a[href="/movies/${dismissMovieId}"]`)).toHaveCount(0);
  await expect(recommendationCards(page)).toHaveCount(3);
});
