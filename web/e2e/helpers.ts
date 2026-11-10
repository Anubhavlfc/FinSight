import { expect, test as base, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

export function fixture<T = unknown>(name: string): T {
  return JSON.parse(readFileSync(join(here, "fixtures", name), "utf8")) as T;
}

type Fixtures = {
  /** Console errors and uncaught exceptions seen on the page. Tests that cut the network on purpose clear it. */
  problems: string[];
};

export const test = base.extend<Fixtures>({
  problems: [
    async ({ page }, use) => {
      const problems: string[] = [];
      page.on("console", (m) => {
        if (m.type() === "error") problems.push(m.text());
      });
      page.on("pageerror", (e) => problems.push(`uncaught: ${e.message}`));
      await use(problems);
      expect(problems, "the page logged no errors").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

export const STATUS_URL = /\/api\/runs\/run-[\w-]+$/;

export async function noHorizontalOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
  expect(scroll, "the page does not scroll sideways").toBeLessThanOrEqual(client);
}

/** Pick the synthetic sample company through the search box, with the keyboard. */
export async function pickDemo(page: Page) {
  const box = page.getByRole("combobox", { name: "Company" });
  await box.fill("demo");
  const option = page.getByRole("option", { name: /DEMO/ });
  await expect(option).toBeVisible();
  await expect(option).toContainText("Synthetic sample");
  await box.press("ArrowDown");
  await box.press("Enter");
  await expect(page.getByRole("region", { name: "Selected company" })).toContainText("DEMO ANALYTICS CORP");
}

/** Start an analysis of the synthetic company and return its run ID once the run page is open. */
export async function startDemoRun(page: Page): Promise<string> {
  await page.goto("/app");
  await pickDemo(page);
  await page.getByRole("button", { name: "Run analysis" }).click();
  await page.waitForURL(/\/app\/runs\/run-/);
  return page.url().split("/").pop()!;
}

type Json = Record<string, any>;

/** Answer run-status requests with the real response, changed by `edit`. Other requests pass through. */
export async function rewriteStatus(page: Page, edit: (body: Json) => Json) {
  await page.route(STATUS_URL, async (route: Route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const response = await route.fetch();
    await route.fulfill({ response, json: edit(await response.json()) });
  });
}

const ORDER = ["planning", "collecting", "validating", "calculating", "investigating", "writing", "reviewing", "ready"];

/** The same run as the server would have reported it part-way through the investigation stage. */
export function midInvestigation(body: Json): Json {
  return {
    ...body,
    state: "INVESTIGATING",
    phase: "running",
    is_active: true,
    finished_at: null,
    has_results: false,
    has_report: false,
    current_activity: "Searching the 10-K text for the company's explanation of each flagged change.",
    investigation: { investigated: 3, flagged: 8, limit: 8 },
    stages: body.stages.map((s: Json) => {
      const i = ORDER.indexOf(s.key);
      if (i < 4) return s;
      return { ...s, state: i === 4 ? "active" : "pending", finished_at: null, started_at: i === 4 ? s.started_at : null, detail: null };
    }),
  };
}

/** The same run as the server reports one whose SEC download failed. */
export function failedAtCollecting(body: Json): Json {
  return {
    ...body,
    state: "FAILED",
    phase: "failed",
    is_active: false,
    has_results: false,
    has_report: false,
    current_activity: "Stopped while collecting.",
    error: {
      message: "SEC EDGAR could not be reached after 3 attempts.",
      stage: "collecting",
      retryable: true,
      next_steps: ["Retry from this stage. Finished stages are kept.", "If it keeps failing, SEC EDGAR may be unavailable; try again later."],
    },
    stages: body.stages.map((s: Json) => {
      const i = ORDER.indexOf(s.key);
      if (i < 1) return s;
      return { ...s, state: i === 1 ? "failed" : "pending", finished_at: null, started_at: i === 1 ? s.started_at : null, detail: null };
    }),
  };
}
