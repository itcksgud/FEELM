import { expect, test } from "@playwright/test";

const MOV_KO_FULL = "6b226903-0ca4-4f5a-9bf0-50d6cedd224c";

test.describe("C0 Catalog", () => {
  test("검색 홈에서 상세의 OTT를 확인하고 유사 영화로 이동한다", async ({ page }) => {
    await page.goto("/search");

    await expect(page.getByRole("heading", { name: /오늘은 어떤 영화를/ })).toBeVisible();
    const search = page.getByRole("textbox", { name: "영화 검색" });
    await search.fill("나우 유 씨 미");

    await expect(page).toHaveURL(/\/search\/results\?q=/);
    await expect(page.getByRole("heading", { name: "‘나우 유 씨 미’ 검색 결과" })).toBeVisible();
    await page.getByRole("link", { name: "나우 유 씨 미 상세 보기" }).click();

    await expect(page).toHaveURL(`/movies/${MOV_KO_FULL}`);
    await expect(page.getByRole("heading", { level: 1, name: "나우 유 씨 미" })).toBeVisible();
    await expect(page.getByText("TMDB 7.3/10", { exact: false })).toBeVisible();

    await expect(page.getByRole("heading", { name: "시청 가능한 OTT" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "구독으로 보기" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Netflix" })).toBeVisible();
    const aggregator = page.getByRole("button", { name: /시청 옵션 확인, 외부 페이지로 이동/ }).first();
    await expect(aggregator).toBeEnabled();

    await expect(page.getByRole("heading", { name: "비슷한 영화" })).toBeVisible();
    await expect(page.getByText("같은 범죄 장르", { exact: false }).first()).toBeVisible();
    await page.getByRole("link", { name: "인사이드 맨 상세 보기" }).click();

    await expect(page).toHaveURL(/\/movies\/e67778c9-7b2e-42d4-9d3e-a3026b2efea3$/);
    await expect(page.getByRole("heading", { level: 1, name: "인사이드 맨" })).toBeVisible();
  });

  test("결과가 없는 검색은 정상 빈 상태를 표시한다", async ({ page }) => {
    await page.goto("/search/results?q=%EC%A1%B4%EC%9E%AC%ED%95%98%EC%A7%80%EC%95%8A%EB%8A%94%EA%B2%80%EC%83%89%EC%96%B4");

    await expect(page.getByRole("heading", { name: "‘존재하지않는검색어’ 검색 결과" })).toBeVisible();
    const emptyState = page.getByRole("status");
    await expect(emptyState.getByRole("heading", { name: "검색 결과가 없어요" })).toBeVisible();
    await expect(emptyState.getByRole("button", { name: "필터 초기화" })).toBeVisible();
  });

  test("API validation 오류는 필터 복구 행동과 함께 표시한다", async ({ page }) => {
    await page.goto("/search/results?q=%EB%82%98%EC%9A%B0&yearFrom=2025&yearTo=2000");

    const alert = page.getByRole("alert");
    await expect(alert.getByRole("heading", { name: "필터 값을 확인해 주세요" })).toBeVisible();
    await expect(alert.getByText("잘못된 필터를 초기화하거나 수정한 뒤 다시 시도해 주세요.")).toBeVisible();
    await alert.getByRole("button", { name: "필터 초기화" }).click();
    await expect(page).not.toHaveURL(/yearFrom|yearTo/);
    await expect(page.getByRole("link", { name: "나우 유 씨 미 상세 보기" })).toBeVisible();
  });

  test("복구 가능한 Catalog 장애는 검색 조건을 유지하고 재시도를 제공한다", async ({ page }) => {
    let allowSearch = false;
    await page.route("**/api/v1/movies?**", async (route) => {
      if (allowSearch) {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          code: "CATALOG_UNAVAILABLE",
          message: "영화 정보를 불러올 수 없어요. 잠시 후 다시 시도해 주세요.",
          traceId: "e2e-catalog-unavailable",
          fieldErrors: [],
        }),
      });
    });

    await page.goto("/search/results?q=%EB%82%98%EC%9A%B0");
    const alert = page.getByRole("alert");
    await expect(alert.getByRole("heading", { name: "검색 결과를 불러오지 못했어요" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "영화 검색" })).toHaveValue("나우");
    allowSearch = true;
    await alert.getByRole("button", { name: "다시 시도" }).click();
    await expect(page.getByRole("link", { name: "나우 유 씨 미 상세 보기" })).toBeVisible();
  });

  test("영화 상세 SPA 주소로 직접 접근하고 새로고침할 수 있다", async ({ page }) => {
    await page.goto(`/movies/${MOV_KO_FULL}`);
    await expect(page.getByRole("heading", { level: 1, name: "나우 유 씨 미" })).toBeVisible();

    await page.reload();
    await expect(page).toHaveURL(`/movies/${MOV_KO_FULL}`);
    await expect(page.getByRole("heading", { level: 1, name: "나우 유 씨 미" })).toBeVisible();
    await expect(page.getByRole("button", { name: "이전 화면으로 돌아가기" })).toBeVisible();
  });
});
