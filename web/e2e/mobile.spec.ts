import { expect, noHorizontalOverflow, pickDemo, test } from "./helpers";

test.describe("phone", () => {
  test("company selection opens as a drawer and starts a run", async ({ page }) => {
    await page.goto("/app/reports");
    await expect(page.getByRole("combobox")).toHaveCount(0);
    const open = page.getByRole("button", { name: "New analysis" });
    await open.click();
    const drawer = page.getByRole("dialog", { name: "New analysis" });
    await expect(drawer).toBeVisible();
    await noHorizontalOverflow(page);

    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(open).toBeFocused();

    await open.click();
    await pickDemo(page);
    await drawer.getByRole("button", { name: "Analyze company" }).click();
    await page.waitForURL(/\/app\/runs\/run-/);
    await expect(drawer).toBeHidden();
    await expect(page.getByRole("heading", { name: "Financial summary" })).toBeVisible();
    await noHorizontalOverflow(page);
  });

  test("the menu button opens and closes the navigation", async ({ page }) => {
    for (const [path, label, link, url] of [
      ["/", "Main", "How it works", /#how$/],
      ["/app/sample/demo", "Workspace", "Reports", /\/app\/reports$/],
    ] as const) {
      await page.goto(path);
      const nav = page.getByRole("navigation", { name: label });
      const menu = page.getByRole("button", { name: "Menu", exact: true });
      await expect(nav.getByRole("link", { name: link })).toBeHidden();
      await expect(menu).toHaveAttribute("aria-expanded", "false");
      await menu.click();
      await expect(nav.getByRole("link", { name: link })).toBeVisible();
      await expect(page.getByRole("button", { name: "Close menu" })).toHaveAttribute("aria-expanded", "true");
      await page.keyboard.press("Escape");
      await expect(nav.getByRole("link", { name: link })).toBeHidden();
      await menu.click();
      await nav.getByRole("link", { name: link }).click();
      await expect(page).toHaveURL(url);
      await expect(nav.getByRole("link", { name: link })).toBeHidden();
      await noHorizontalOverflow(page);
    }
  });

  test("the report sections can be reached and charts switch to tables", async ({ page }) => {
    await page.goto("/app/sample/demo");
    await page.getByRole("navigation", { name: "Report sections" }).getByRole("link", { name: "Charts" }).click();
    await expect(page.getByRole("heading", { name: "Three-year charts" })).toBeInViewport();
    const chart = page.locator("figure, section").filter({ has: page.getByRole("heading", { name: "Gross, operating and net margin" }) }).last();
    await chart.getByRole("button", { name: "Table" }).click();
    await expect(chart.getByRole("row", { name: /Operating margin/ })).toContainText("16.5");
    await noHorizontalOverflow(page);
  });
});
