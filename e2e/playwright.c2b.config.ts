import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./c2b",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report-c2b" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:55173",
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  expect: {
    timeout: 10_000,
  },
  timeout: 60_000,
});
