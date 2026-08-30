import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const comparisons = ["stars", "onboarding", "party", "reasons"] as const;
const outputDirectory = path.resolve(
  import.meta.dirname,
  "../../docs/recommendation/evidence/assets/rec-ev-008",
);

test("동일 viewport에서 네 UI 비교 evidence를 캡처한다", async ({ page }) => {
  await mkdir(outputDirectory, { recursive: true });

  for (const comparison of comparisons) {
    await page.goto(`/__evidence/rec-ev-008?comparison=${comparison}`);
    await page.evaluate(() => document.fonts.ready);
    await expect(page.getByRole("heading", { level: 1, name: "REC-EV-008 UI 비교" })).toBeVisible();
    await expect(page.getByRole("status")).toContainText("제품 승격 금지");
    await expect(page.getByRole("button", { name: new RegExp(comparisonLabel(comparison)) })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await page.screenshot({
      path: path.join(outputDirectory, `${comparison}-1440x1200.png`),
      fullPage: false,
      animations: "disabled",
    });
  }
});

function comparisonLabel(comparison: (typeof comparisons)[number]) {
  return {
    stars: "예상 별점",
    onboarding: "온보딩 부담",
    party: "파티 정책",
    reasons: "추천 이유",
  }[comparison];
}
