import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "local-mvp.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:55173",
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
    permissions: ["clipboard-read", "clipboard-write"],
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  projects: [{ name: "local-mvp-chromium", use: { ...devices["Desktop Chrome"] } }],
  expect: { timeout: 15_000 },
  timeout: 180_000,
});
