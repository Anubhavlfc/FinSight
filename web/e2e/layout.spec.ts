import { expect, noHorizontalOverflow, startDemoRun, test } from "./helpers";

// Runs at desktop, tablet and phone sizes (see playwright.config.ts).
test.describe("layout at this screen size", () => {
  for (const [name, path, landmark] of [
    ["landing page", "/", "Understand what changed."],
    ["workspace home", "/app", "Analyze a company"],
    ["sample report", "/app/sample/demo", "Financial summary"],
    ["cached real-company sample", "/app/sample/nvda", "Financial summary"],
    ["report archive", "/app/reports", "Reports"],
  ] as const) {
    test(`${name} fits the screen`, async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole("heading", { name: landmark }).first()).toBeVisible();
      await noHorizontalOverflow(page);
      // Nothing important is clipped: every heading sits inside the viewport's width.
      const width = page.viewportSize()!.width;
      const clipped = await page.evaluate((w) =>
        [...document.querySelectorAll("h1, h2, h3")]
          .filter((el) => (el as HTMLElement).offsetParent !== null && !el.closest(".table-scroll, .section-nav, .tabs"))
          .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && (r.left < -1 || r.right > w + 1); })
          .map((el) => el.textContent), width);
      expect(clipped).toEqual([]);
    });
  }

  test("the homepage puts both actions before the product preview", async ({ page }) => {
    await page.goto("/");
    const hero = page.locator(".hero");
    const primary = hero.getByRole("link", { name: "Analyze a company" });
    const secondary = hero.getByRole("link", { name: "Explore sample report" });
    await expect(primary).toBeInViewport();
    await expect(secondary).toBeInViewport();
    await expect(page.locator("#preview")).toContainText(/synthetic sample/i);
    const order = await page.evaluate(() => {
      const action = document.querySelector(".hero-actions")!.getBoundingClientRect();
      const preview = document.querySelector("#preview")!.getBoundingClientRect();
      return { stacked: preview.top >= action.bottom, beside: preview.left >= action.right };
    });
    expect(order.stacked || order.beside).toBe(true);
    if (page.viewportSize()!.width < 1000) expect(order.stacked).toBe(true);
  });

  test("reading a report leaves the full width to the report", async ({ page }) => {
    await page.goto("/app/sample/demo");
    await expect(page.getByRole("heading", { name: "Financial summary" })).toBeVisible();
    // No company-selection panel sits beside the report; starting again is one compact action.
    await expect(page.getByRole("combobox")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "New analysis" })).toBeVisible();
    // The takeaways come before the company and filing details.
    const tops = await page.evaluate(() => ["summary", "metrics", "about"].map((id) => document.getElementById(id)!.getBoundingClientRect().top));
    expect(tops[0]!).toBeLessThan(tops[1]!);
    expect(tops[1]!).toBeLessThan(tops[2]!);
    // One notes block carries the synthetic label instead of a stack of banners.
    await expect(page.locator(".report-notes")).toHaveCount(1);
    await expect(page.locator(".report-notes")).toContainText("Sample report on synthetic data");
  });

  test("controls are large enough to tap", async ({ page }, info) => {
    test.skip(info.project.name === "desktop", "touch sizes apply to touch screens");
    await page.goto("/app/sample/demo");
    await expect(page.getByRole("heading", { name: "Financial summary" })).toBeVisible();
    const small = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>("button, .btn, .section-nav a, summary")]
        .filter((el) => el.offsetParent !== null)
        .map((el) => ({ text: (el.textContent ?? "").trim().slice(0, 40), h: Math.round(el.getBoundingClientRect().height) }))
        .filter((x) => x.h < 44));
    expect(small).toEqual([]);
  });

  test("a live report and its sections fit the screen", async ({ page }) => {
    await startDemoRun(page);
    await expect(page.getByRole("heading", { name: "Financial summary" })).toBeVisible();
    await noHorizontalOverflow(page);
    for (const section of ["Key metrics", "Three-year charts", "Biggest changes", "Detailed financials", "Evidence and sources", "Review and report details", "Downloads"]) {
      const heading = page.getByRole("heading", { name: section, exact: true });
      await heading.scrollIntoViewIfNeeded();
      await expect(heading).toBeInViewport();
    }
    await noHorizontalOverflow(page);
    await page.goto("/app/reports");
    await expect(page.getByRole("link", { name: "Open report" }).first()).toBeVisible();
    await noHorizontalOverflow(page);
  });
});
