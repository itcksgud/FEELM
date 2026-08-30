import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./evidence",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "line",
  outputDir: "../outputs/recommendation-evidence/rec-ev-008/playwright",
  use: {
    baseURL: "http://127.0.0.1:5174",
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
    viewport: { width: 1440, height: 1200 },
    colorScheme: "light",
    reducedMotion: "reduce",
  },
  webServer: {
    command: "npm run dev --prefix ../frontend -- --host 127.0.0.1 --port 5174 --strictPort",
    url: "http://127.0.0.1:5174/__evidence/rec-ev-008",
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1200 } },
    },
  ],
});
