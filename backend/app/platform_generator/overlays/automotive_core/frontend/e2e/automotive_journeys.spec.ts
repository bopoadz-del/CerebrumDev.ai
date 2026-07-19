import { test, expect } from "@playwright/test";

/**
 * Critical automotive pilot journeys (PR3).
 * These tests target a running generated platform instance.
 */

test.describe("Automotive Safety Intelligence journeys", () => {
  test("login and open foundation workspace", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText(/Automotive Safety Intelligence|Vehicle Safety Workspace/i)).toBeVisible({
      timeout: 15000,
    });
  });

  test("ask recall question and inspect citations", async ({ page }) => {
    await page.goto("/");
    const input = page.getByRole("textbox").first();
    await input.fill("What is recall campaign 15V176000?");
    await input.press("Enter");
    await expect(page.getByText(/Automotive Core|15V176000|citation/i)).toBeVisible({
      timeout: 30000,
    });
  });

  test("create private project and upload document", async ({ page }) => {
    await page.goto("/projects");
    await expect(page.getByText(/My Projects|Projects/i)).toBeVisible({ timeout: 15000 });
  });

  test("admin pack status page loads", async ({ page }) => {
    await page.goto("/admin/automotive-core");
    await expect(page.getByText(/Automotive Core Knowledge/i)).toBeVisible({
      timeout: 15000,
    });
  });
});
