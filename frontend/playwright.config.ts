import { defineConfig } from "@playwright/test";

// E2E против поднятого приложения (docker compose up или `pnpm dev` + backend). Адрес — E2E_BASE_URL;
// без загрузки браузеров можно взять установленный: E2E_CHANNEL=msedge или chrome.
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 20_000 },
  workers: 1,
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["junit", { outputFile: "../reports/playwright-report.xml" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    viewport: { width: 1600, height: 900 },
    channel: process.env.E2E_CHANNEL || undefined,
    trace: "retain-on-failure",
    acceptDownloads: true,
    actionTimeout: 20_000,
    navigationTimeout: 60_000,
  },
});
