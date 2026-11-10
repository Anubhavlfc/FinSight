import { defineConfig, devices } from "@playwright/test";

// The tests drive the built app as FastAPI serves it, against the real backend running
// offline on the synthetic fixture company: no SEC access and no API keys are needed.
const PORT = Number(process.env.E2E_PORT ?? 8765);
const PYTHON = process.env.FINSIGHT_PYTHON ?? "python";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "off",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } }, testIgnore: /mobile\.spec\.ts/ },
    { name: "tablet", use: { ...devices["Desktop Chrome"], viewport: { width: 820, height: 1180 }, hasTouch: true }, testMatch: /layout\.spec\.ts/ },
    { name: "mobile", use: { ...devices["Pixel 7"] }, testMatch: /(layout|mobile)\.spec\.ts/ },
  ],
  webServer: {
    // A throwaway data folder per test run; an empty SEC user agent keeps the server offline.
    command: `${PYTHON} -m finsight.api`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      PORT: String(PORT),
      HOST: "127.0.0.1",
      FINSIGHT_HOME: process.env.E2E_FINSIGHT_HOME ?? `${process.env.RUNNER_TEMP ?? "/tmp"}/finsight-e2e-${Date.now()}`,
      FINSIGHT_SEC_USER_AGENT: "",
      ANTHROPIC_API_KEY: "",
      FINSIGHT_ANTHROPIC_API_KEY: "",
      FINSIGHT_RUNS_PER_HOUR: "1000",
      FINSIGHT_LOOKUPS_PER_MINUTE: "6000",
    },
  },
});
