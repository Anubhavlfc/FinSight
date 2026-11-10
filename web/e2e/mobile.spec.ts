import { expect, noHorizontalOverflow, pickDemo, test } from "./helpers";

test.describe("phone", () => {
  test("company selection opens as a drawer and starts a run", async ({ page }) => {
    await page.goto("/app/reports");
    await expect(page.getByRole("complementary", { name: "Company selection" })).toHaveCount(0);
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
    await drawer.getByRole("button", { name: "Run analysis" }).click();
    await page.waitForURL(/\/app\/runs\/run-/);
    await expect(drawer).toBeHidden();
    await expect(page.getByRole("heading", { name: "Company overview" })).toBeVisible();
    await noHorizontalOverflow(page);
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
