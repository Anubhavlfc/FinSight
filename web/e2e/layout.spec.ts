import { expect, noHorizontalOverflow, startDemoRun, test } from "./helpers";

// Runs at desktop, tablet and phone sizes (see playwright.config.ts).
test.describe("layout at this screen size", () => {
  for (const [name, path, landmark] of [
    ["landing page", "/", "Three years of financial performance."],
    ["workspace home", "/app", "Analyze a company"],
    ["sample report", "/app/sample/demo", "Company overview"],
    ["cached real-company sample", "/app/sample/nvda", "Company overview"],
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

  test("a live report and its sections fit the screen", async ({ page }) => {
    await startDemoRun(page);
    await expect(page.getByRole("heading", { name: "Company overview" })).toBeVisible();
    await noHorizontalOverflow(page);
    for (const section of ["Key metrics", "Three-year charts", "Biggest changes", "Financial interpretation", "Sources and limitations", "Downloads"]) {
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
