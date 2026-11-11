import { readFileSync } from "node:fs";
import { expect, failedAtCollecting, fixture, midInvestigation, pickDemo, rewriteStatus, startDemoRun, STATUS_URL, test } from "./helpers";

test.describe("landing page", () => {
  test("landing page leads into the sample analysis", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Understand what changed.");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("See why it matters.");

    // The preview is the real interface showing the bundled sample, and says so.
    const preview = page.locator("#preview");
    await expect(preview).toContainText("DEMO ANALYTICS CORP");
    await expect(preview).toContainText(/synthetic sample/i);
    await expect(preview.getByRole("article", { name: "Revenue, FY2026" })).toContainText("$1,239");

    await page.getByRole("link", { name: "Explore sample report" }).first().click();
    await expect(page).toHaveURL(/\/app\/sample\/demo$/);
    await expect(page.getByRole("heading", { level: 1, name: "DEMO ANALYTICS CORP" })).toBeVisible();
    await expect(page.getByText("Sample report on synthetic data")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Biggest changes" })).toBeVisible();
  });

  test("landing navigation reaches every section and the workspace", async ({ page }) => {
    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "Main" });
    await nav.getByRole("link", { name: "How it works" }).click();
    await expect(page).toHaveURL(/#how$/);
    await expect(page.getByRole("heading", { name: /From a company name to the evidence/ })).toBeInViewport();
    await nav.getByRole("link", { name: "Report preview" }).click();
    await expect(page.getByRole("heading", { name: "Every finding keeps three things apart" })).toBeInViewport();
    await nav.getByRole("link", { name: "What it covers" }).click();
    await expect(page.locator("#scope")).toContainText("Banks, insurers, broker-dealers, REITs and foreign filers are not supported.");
    // The pipeline detail is there on demand, not in the way.
    const behind = page.locator("details").filter({ hasText: "What happens behind the scenes" });
    await expect(behind.getByText(/eight stages/)).toBeHidden();
    await behind.locator("summary").click();
    await expect(behind.getByText(/eight stages/)).toBeVisible();
    await expect(nav.getByRole("link", { name: /GitHub/ })).toHaveAttribute("href", /github\.com\/Anubhavlfc\/FinSight/);
    await page.getByRole("banner").getByRole("link", { name: "Analyze a company" }).click();
    await expect(page).toHaveURL(/\/app$/);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Analyze a company");
  });
});

test.describe("running an analysis", () => {
  test("search, select and submit produce a report from the real backend", async ({ page }) => {
    await page.goto("/app");
    await pickDemo(page);

    // The period defaults to the latest completed fiscal years and names the filing behind it.
    const selection = page.getByRole("region", { name: "Selected company" });
    await expect(selection).toContainText("FY2024 to FY2026");
    await selection.getByText("Analysis options").click();
    const years = page.getByLabel("Fiscal years");
    await expect(years.locator("option:checked")).toHaveText("FY2024 to FY2026 (latest)");
    await expect(page.getByText(/FY2026 ended Jun 30, 2026; its 10-K was filed Jul 29, 2026/)).toBeVisible();

    await page.getByLabel(/Question to keep in mind/).fill("Why did free cash flow fall?");
    await page.getByRole("button", { name: "Analyze company" }).click();
    await expect(page).toHaveURL(/\/app\/runs\/run-\d{8}-\d{6}-[0-9a-f]{6}$/);

    await expect(page.getByRole("heading", { name: "Financial summary" })).toBeVisible();
    await expect(page.getByText("Awaiting analyst review").first()).toBeVisible();
    await expect(page.getByText("Synthetic sample data")).toBeVisible();
    await expect(page.locator("#summary")).toContainText("Analysis question: Why did free cash flow fall?");

    // Figures, periods, units and comparisons as the engine calculated them.
    const revenue = page.getByRole("article", { name: "Revenue, FY2026" });
    await expect(revenue).toContainText("$1,239");
    await expect(revenue).toContainText("million");
    await expect(revenue).toContainText("+5.0% vs FY2025");
    await expect(revenue).toContainText("reported");
    const fcf = page.getByRole("article", { name: "Free cash flow, FY2026" });
    await expect(fcf).toContainText("-$60");
    await expect(fcf).toContainText("calculated");

    // Each chart has an exact-value table behind it.
    const chart = page.locator("figure, section").filter({ has: page.getByRole("heading", { name: "Revenue, operating income and net income" }) }).last();
    await expect(chart).toContainText("USD millions");
    await chart.getByRole("button", { name: "Table" }).click();
    const row = chart.getByRole("row", { name: /Revenue/ });
    await expect(row).toContainText("1,000");
    await expect(row).toContainText("1,239");

    // The three kinds of statement are labelled separately on a finding.
    const first = page.locator("#changes li").first();
    await expect(first).toContainText("Net income moved from loss to profit");
    await expect(first).toContainText("Supported by filing quotes");
    await expect(first.getByRole("heading", { name: "What the numbers show" })).toBeVisible();
    await expect(first.getByRole("heading", { name: "What management says" })).toBeVisible();
    await expect(first.getByRole("heading", { name: "Does the filing evidence support it?" })).toBeVisible();
    await expect(first).toContainText("$135 million charge for the settlement of patent litigation");
    await expect(first.getByText("Quote matches the filing word for word")).toBeVisible();
  });

  test("progress shows the backend's stages and reconnects after a refresh", async ({ page }) => {
    // The offline run finishes in well under a second, so the status response is held at the
    // investigation stage to look at the progress screen. Everything else is the real server.
    await rewriteStatus(page, midInvestigation);
    const runId = await startDemoRun(page);

    await expect(page.getByText("Running", { exact: true })).toBeVisible();
    const now = page.getByRole("region", { name: "Current status" });
    await expect(now).toContainText("Searching the 10-K text");
    await expect(now).toContainText("Elapsed");
    await expect(now).toContainText("3 of 8");
    await expect(now).not.toContainText("%"); // no invented percentage

    // The eight stages are detail on demand while the run is live.
    const stages = page.getByRole("list", { name: "Analysis stages" }).getByRole("listitem");
    await expect(stages.first()).toBeHidden();
    await page.getByText("Processing steps").click();
    await expect(stages).toHaveCount(8);
    await expect(stages.nth(0)).toContainText("Planning");
    await expect(stages.nth(0)).toContainText("Done");
    await expect(stages.nth(4)).toContainText("Investigating");
    await expect(stages.nth(4)).toContainText("In progress");
    await expect(stages.nth(5)).toContainText("Waiting");
    await expect(page.getByRole("list", { name: /Activity/ }).getByRole("listitem").first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "Financial summary" })).toHaveCount(0);

    // A refresh comes back to the same run, still in progress, without starting another one.
    await page.reload();
    await expect(page).toHaveURL(new RegExp(`${runId}$`));
    await page.getByText("Processing steps").click();
    await expect(stages.nth(4)).toContainText("In progress");

    // Once the server reports the end state, the report replaces the progress view by itself.
    await page.unroute(STATUS_URL);
    await expect(page.getByRole("heading", { name: "Financial summary" })).toBeVisible();
    await page.goto("/app/reports");
    await expect(page.getByRole("link", { name: "Open report" })).not.toHaveCount(0);
  });

  test("submitting twice does not start two analyses", async ({ page, request }) => {
    const before = (await (await request.get("/api/runs")).json()).runs.length;
    await page.goto("/app");
    await pickDemo(page);
    const button = page.getByRole("button", { name: "Analyze company" });
    await button.dblclick();
    await page.waitForURL(/\/app\/runs\/run-/);
    await expect(page.getByRole("heading", { name: "Financial summary" })).toBeVisible();
    const after = (await (await request.get("/api/runs")).json()).runs.length;
    expect(after - before).toBe(1);
  });

  test("a failed run explains itself and offers a retry from the failed stage", async ({ page }) => {
    await rewriteStatus(page, failedAtCollecting);
    await startDemoRun(page);
    const alert = page.getByRole("alert").filter({ hasText: "Stopped at collecting" });
    await expect(alert).toContainText("SEC EDGAR could not be reached after 3 attempts.");
    await expect(alert).toContainText("Finished stages are kept.");
    const stages = page.getByRole("list", { name: "Analysis stages" }).getByRole("listitem");
    await expect(stages.nth(1)).toContainText("Stopped here");

    // Retrying asks the server to resume; here the server then reports the finished run.
    await page.route(/\/api\/runs\/run-[\w-]+\/resume$/, async (route) => {
      await page.unroute(STATUS_URL);
      await route.fulfill({ status: 202, json: { run_id: page.url().split("/").pop(), reused: true } });
    });
    await alert.getByRole("button", { name: "Retry from this stage" }).click();
    await expect(page.getByRole("heading", { name: "Financial summary" })).toBeVisible();
  });
});

test.describe("reports", () => {
  test("archive opens a finished report without rerunning it, and downloads are real files", async ({ page, request }) => {
    const runId = await startDemoRun(page);
    await expect(page.getByRole("heading", { name: "Financial summary" })).toBeVisible();
    const count = (await (await request.get("/api/runs")).json()).runs.length;

    await page.goto("/app/reports");
    const row = page.getByRole("row").filter({ has: page.locator(`a[href$="${runId}"]`) });
    await expect(row).toContainText("DEMO ANALYTICS CORP");
    await expect(row).toContainText("FY2024 to FY2026");
    await expect(row).toContainText("Complete");
    await expect(row).toContainText("Awaiting analyst review");
    await expect(row).toContainText("HTML · Markdown · CSV · JSON · PNG");
    await expect(row).not.toContainText("PDF");
    await row.getByRole("link", { name: "Open report" }).click();
    await expect(page).toHaveURL(new RegExp(`${runId}$`));
    await expect(page.getByRole("heading", { name: "Financial summary" })).toBeVisible();
    expect((await (await request.get("/api/runs")).json()).runs.length).toBe(count);

    const downloads = page.locator("#downloads");
    const [md] = await Promise.all([page.waitForEvent("download"), downloads.getByRole("link", { name: /Report \(Markdown\)/ }).click()]);
    expect(md.suggestedFilename()).toMatch(/report\.md$/);
    const text = readFileSync((await md.path())!, "utf8");
    expect(text).toContain("DEMO ANALYTICS CORP");
    expect(text).toContain("FY2026");

    const [csv] = await Promise.all([page.waitForEvent("download"), downloads.getByRole("link", { name: /^Metrics/ }).click()]);
    expect(csv.suggestedFilename()).toMatch(/metrics\.csv$/);
    expect(readFileSync((await csv.path())!, "utf8")).toContain("metric:revenue:FY2026");

    // Every listed download exists on the server.
    const result = await (await request.get(`/api/runs/${runId}/result`)).json();
    for (const d of result.downloads) {
      const res = await request.get(`/api/runs/${runId}/files/${d.name}`);
      expect(res.status(), d.name).toBe(200);
    }
  });

  test("approval is an explicit step and the archive tells the two states apart", async ({ page }) => {
    const runId = await startDemoRun(page);
    const review = page.locator("section").filter({ has: page.getByRole("heading", { name: "Report review" }) }).first();
    await expect(review).toContainText("Awaiting analyst review");
    await review.getByLabel(/Review note/).fill("Checked revenue and cash flow against the 10-K.");
    await review.getByRole("button", { name: "Mark as approved" }).click();
    await expect(review).toContainText("Approved");
    await expect(review).toContainText("Checked revenue and cash flow against the 10-K.");

    await page.goto("/app/reports");
    const row = page.getByRole("row").filter({ has: page.locator(`a[href$="${runId}"]`) });
    await expect(row).toContainText("Approved");
    await page.getByRole("group", { name: "Filter reports" }).getByRole("button", { name: "Approved" }).click();
    await expect(row).toBeVisible();
    await page.getByRole("group", { name: "Filter reports" }).getByRole("button", { name: "In progress" }).click();
    await expect(row).toHaveCount(0);
    await expect(page.getByText("No reports match this filter.")).toBeVisible();
  });
});

test.describe("reading a report", () => {
  test("a finding opens its evidence in context, with the filing details", async ({ page }) => {
    await page.goto("/app/sample/demo#change-2");
    // A direct link opens that finding.
    const second = page.locator("#change-2");
    await expect(second.getByRole("heading", { name: "What the numbers show" })).toBeVisible();

    const first = page.locator("#change-1");
    await expect(first.getByRole("heading", { name: "What management says" })).toBeVisible();
    const open = first.getByRole("button", { name: "View evidence" }).first();
    await open.click();

    const dialog = page.getByRole("dialog", { name: /passage in the filing/i });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("10-K for FY2025");
    await expect(dialog).toContainText("Jul 30, 2025");
    await expect(dialog).toContainText("Item 7. Management's Discussion and Analysis");
    await expect(dialog).toContainText("Synthetic sample text, no public link");
    // A matching quote is not presented as a proven explanation.
    await expect(dialog).toContainText("not that its explanation is correct");
    const mark = dialog.locator("mark");
    await expect(mark).toContainText("$135 million charge for the settlement of patent litigation");
    await expect(mark).toBeInViewport();
    // Identifiers stay out of the way until asked for.
    await expect(dialog.getByText(/Accession/i)).toBeHidden();

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(open).toBeFocused();
  });

  test("the section links follow the reader and jump to each section", async ({ page }) => {
    await page.goto("/app/sample/demo");
    const nav = page.getByRole("navigation", { name: "Report sections" });
    await expect(nav.getByRole("link", { name: "Summary" })).toHaveAttribute("aria-current", "true");
    await nav.getByRole("link", { name: "Biggest changes" }).click();
    await expect(page).toHaveURL(/#changes$/);
    await expect(page.getByRole("heading", { name: "Biggest changes" })).toBeInViewport();
    await expect(nav.getByRole("link", { name: "Biggest changes" })).toHaveAttribute("aria-current", "true");
    await expect(nav.getByRole("link", { name: "Summary" })).not.toHaveAttribute("aria-current", "true");
    await expect(nav).toBeInViewport(); // still in reach after scrolling
    await nav.getByRole("link", { name: "Downloads" }).click();
    await expect(nav.getByRole("link", { name: "Downloads" })).toHaveAttribute("aria-current", "true");

    // The four primary metrics each carry the latest value, its year, the change and the trend.
    for (const name of ["Revenue", "Operating margin", "Net income", "Free cash flow"]) {
      const card = page.getByRole("article", { name: `${name}, FY2026` });
      await expect(card).toContainText("vs FY2025");
      await expect(card).toContainText("FY2024");
    }
    // Changes are not coloured as good or bad by direction alone: the reading is labelled in words.
    await expect(page.locator("#change-1")).toContainText("Investigator's reading of the change:");
  });

  test("the bundled sample works with no backend at all, including its downloads", async ({ page, request, problems }) => {
    await page.route("**/api/**", (route) => route.fulfill({ status: 404, contentType: "text/html", body: "<html>Not found</html>" }));
    await page.goto("/app");
    const notice = page.getByRole("status").filter({ hasText: "Live analysis is unavailable" });
    // The cause is stated when it is known: this site has no analysis service behind it.
    await expect(notice).toContainText("without an analysis service");
    await notice.getByRole("link", { name: "Explore sample report" }).click();
    await expect(page.getByRole("heading", { name: "Financial summary" })).toBeVisible();
    await expect(page.locator(".report-notes")).toContainText("Sample report on synthetic data");
    const links = await page.locator("#downloads a[href]").evaluateAll((as) => as.map((a) => (a as HTMLAnchorElement).href));
    expect(links.length).toBeGreaterThan(5);
    for (const href of links) expect((await request.get(href)).status(), href).toBe(200);
    problems.length = 0; // the refused health checks are logged by the browser
  });
});

test.describe("things that go wrong", () => {
  test("an unsupported company gets a reason and next steps instead of a run", async ({ page, request }) => {
    const before = (await (await request.get("/api/runs")).json()).runs.length;
    await page.goto("/app");
    const box = page.getByRole("combobox", { name: "Search by company name or ticker" });

    await box.fill("zzqqx");
    await expect(page.getByText(/No company matches “zzqqx”/)).toBeVisible();
    await box.press("Enter"); // submitting free text does not guess a company
    await expect(page.getByRole("alert")).toContainText("Pick a company from the suggestions");
    await expect(page).toHaveURL(/\/app$/);

    await box.fill("sbnk");
    await page.getByRole("option", { name: /SBNK/ }).click();
    const card = page.getByRole("region", { name: "Selected company" });
    await expect(card).toContainText("This company is outside the supported scope");
    await expect(card).toContainText("net interest margin");
    await expect(card).toContainText("Choose a US nonfinancial operating company that files Form 10-K.");
    await expect(page.getByRole("button", { name: "Analyze company" })).toBeDisabled();
    expect((await (await request.get("/api/runs")).json()).runs.length).toBe(before);
  });

  test("without live SEC access a real ticker is refused, never swapped for demo figures", async ({ page }) => {
    await page.goto("/app");
    await expect(page.getByText("Sample data only").filter({ visible: true })).toHaveCount(1);
    const box = page.getByRole("combobox", { name: "Search by company name or ticker" });
    await box.fill("MSFT");
    await expect(page.getByText(/No company matches “MSFT”/)).toBeVisible();
    await expect(page.getByText(/Only sample companies are listed/)).toBeVisible();
  });

  test("an unknown run or page says so and points somewhere useful", async ({ page, problems }) => {
    await page.goto("/app/runs/run-20200101-000000-abcdef");
    await expect(page.getByText("This analysis was not found")).toBeVisible();
    await expect(page.getByRole("link", { name: "Open the report archive" })).toBeVisible();
    await page.goto("/app/nothing-here");
    await expect(page.getByText("There is no page at this address")).toBeVisible();
    await page.goto("/nothing-here");
    await expect(page.getByText("There is no page at this address")).toBeVisible();
    await expect(page.getByRole("link", { name: "Open the workspace" })).toBeVisible();
    problems.length = 0; // the 404 for the unknown run is logged by the browser
  });

  test("when the backend is down the app stays usable and recovers on retry", async ({ page, problems }) => {
    await page.route("**/api/**", (route) => route.abort("connectionrefused"));

    // The landing page and the sample do not need the backend.
    await page.goto("/");
    await expect(page.locator("#preview")).toContainText("DEMO ANALYTICS CORP");

    await page.goto("/app");
    const notice = page.getByRole("status").filter({ hasText: "Live analysis is unavailable" });
    await expect(notice).toContainText("The sample report is bundled with this site and works in full.");
    await expect(page.getByRole("banner").getByRole("status")).toHaveText("Service offline");
    await expect(page.getByRole("button", { name: "Service offline. Check again" })).toBeVisible();
    // No dead controls: the search is replaced by the two things that work.
    await expect(page.getByRole("combobox")).toHaveCount(0);
    await expect(page.locator("main button:disabled")).toHaveCount(0);
    await expect(notice.getByRole("link", { name: "Explore sample report" })).toBeVisible();
    await expect(page.getByText("Recent reports could not be loaded")).toBeVisible();
    await expect(page.getByText("No reports yet")).toHaveCount(0);

    await page.goto("/app/reports");
    await expect(page.getByText("The archive could not be loaded")).toBeVisible();
    await expect(page.getByRole("row", { name: /DEMO ANALYTICS CORP/ })).toBeVisible(); // bundled samples still listed

    await page.goto("/app");
    await notice.getByRole("link", { name: "Explore sample report" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "DEMO ANALYTICS CORP" })).toBeVisible();
    await expect(page.getByRole("article", { name: "Revenue, FY2026" })).toContainText("$1,239");

    // The service comes back: retry reconnects without a page reload.
    await page.goto("/app");
    await expect(notice).toBeVisible();
    await page.unroute("**/api/**");
    await notice.getByRole("button", { name: "Retry connection" }).click();
    await expect(notice).toHaveCount(0);
    await expect(page.getByRole("combobox", { name: "Search by company name or ticker" })).toBeEnabled();
    problems.length = 0; // refused connections are logged by the browser
  });

  test("losing the connection during a run keeps the progress view and recovers", async ({ page, problems }) => {
    await rewriteStatus(page, midInvestigation);
    await startDemoRun(page);
    await expect(page.getByText("Running", { exact: true })).toBeVisible();
    await page.unroute(STATUS_URL);
    await page.route(STATUS_URL, (route) => route.abort("connectionrefused"));
    await expect(page.getByText("Connection to the analysis service was lost")).toBeVisible();
    await expect(page.getByRole("region", { name: "Current status" })).toBeVisible();
    await page.unroute(STATUS_URL);
    await expect(page.getByRole("heading", { name: "Financial summary" })).toBeVisible({ timeout: 20_000 });
    problems.length = 0;
  });

  test("missing data produces partial results, not a blank page", async ({ page }) => {
    // Replays a real PARTIAL run from the engine (see scripts/build_e2e_fixtures.py).
    const status = fixture<Record<string, unknown>>("partial-status.json");
    const result = fixture<Record<string, unknown>>("partial-result.json");
    const id = status.id as string;
    await page.route(`**/api/runs/${id}`, (route) => route.fulfill({ json: status }));
    await page.route(`**/api/runs/${id}/result`, (route) => route.fulfill({ json: result }));
    await page.goto(`/app/runs/${id}`);

    await expect(page.getByText("Partial results", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Nothing was estimated.")).toBeVisible();
    const revenue = page.getByRole("article", { name: "Revenue, FY2026" });
    await expect(revenue).toContainText(/not available/i);
    await expect(revenue).toContainText("missing validated input: revenue");
    await expect(revenue).toContainText("$1,180"); // earlier years are still shown
    await expect(revenue).not.toContainText("$0");

    await page.getByRole("tab", { name: /Missing and limitations/ }).click();
    const panel = page.getByRole("tabpanel");
    await expect(panel).toContainText("Revenue");
    await expect(panel).toContainText("FY2026");
    await expect(panel).toContainText("missing validated input");
    await expect(page.locator("#downloads").getByRole("link", { name: /Report \(HTML\)/ })).toBeVisible();
  });
});

test.describe("keyboard and assistive technology", () => {
  test("the workflow can be driven from the keyboard with visible focus", async ({ page }) => {
    await page.goto("/app");
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to the analysis" });
    await expect(skip).toBeFocused();
    await expect(skip).toBeInViewport();

    const box = page.getByRole("combobox", { name: "Search by company name or ticker" });
    await box.focus();
    await page.keyboard.type("demo");
    await expect(box).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("status").filter({ hasText: "1 matching company" })).toHaveCount(1);
    await page.keyboard.press("ArrowDown");
    await expect(box).toHaveAttribute("aria-activedescendant", /opt-0$/);
    await page.keyboard.press("Enter");
    await expect(page.getByRole("region", { name: "Selected company" })).toBeVisible();

    // Focus is visible: the focused control draws an outline.
    const run = page.getByRole("button", { name: "Analyze company" });
    await run.focus();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Tab");
    await expect(run).toBeFocused();
    const outline = await run.evaluate((el) => getComputedStyle(el).outlineStyle);
    expect(outline).not.toBe("none");
    await page.keyboard.press("Enter");
    await page.waitForURL(/\/app\/runs\/run-/);
    await expect(page.getByRole("heading", { name: "Financial summary" })).toBeVisible();

    // Tabs follow the arrow keys.
    const tab = page.getByRole("tab", { name: /Filing passages/ });
    await tab.focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("tab", { name: /Calculations/ })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("tabpanel")).toContainText("metric:");
  });

  test("status changes are announced and every form control has a label", async ({ page }) => {
    await rewriteStatus(page, midInvestigation);
    await startDemoRun(page);
    const live = page.locator("[aria-live='polite'], [role='status']").filter({ hasText: /Investigating|Searching the 10-K/ });
    await expect(live.first()).toBeAttached();
    await page.unroute(STATUS_URL);
    await expect(page.getByRole("heading", { name: "Financial summary" })).toBeVisible();

    const unlabeled = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>("input, select, textarea, button, a[href]")]
        .filter((el) => el.offsetParent !== null)
        .filter((el) => {
          const labelled = el.getAttribute("aria-label") || el.getAttribute("aria-labelledby") || el.getAttribute("title");
          const label = "labels" in el && (el as HTMLInputElement).labels?.length;
          return !labelled && !label && !(el.textContent ?? "").trim();
        })
        .map((el) => el.outerHTML.slice(0, 120)),
    );
    expect(unlabeled).toEqual([]);
  });

  test("reduced motion turns animation off", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/app/sample/demo");
    await expect(page.getByRole("heading", { name: "Financial summary" })).toBeVisible();
    const longest = await page.evaluate(() =>
      Math.max(0, ...[...document.querySelectorAll("*")].map((el) => {
        const s = getComputedStyle(el);
        return Math.max(parseFloat(s.animationDuration) || 0, parseFloat(s.transitionDuration) || 0);
      })),
    );
    expect(longest).toBeLessThanOrEqual(0.01);
  });
});
