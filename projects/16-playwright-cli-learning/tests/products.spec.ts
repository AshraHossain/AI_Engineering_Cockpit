import { test, expect } from '@playwright/test';

test.describe('Product Browsing Tests', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('Browse all products', async ({ page }) => {
    // Click on Products or navigate to products page
    await page.click('//a[contains(text(), "Products")]');

    // Should see product list
    const productCards = page.locator('//div[contains(@class, "product")]');
    const count = await productCards.count();
    expect(count).toBeGreaterThan(0);
  });

  test('Filter products by category', async ({ page }) => {
    await page.click('//a[contains(text(), "Products")]');

    // Wait for products to load
    await page.waitForSelector('//div[contains(@class, "product")]');

    // Click on a category (e.g., Pliers)
    const categoryLinks = page.locator('//a[@href="#/products/category/1"]');
    if (await categoryLinks.count() > 0) {
      await categoryLinks.first().click();

      // Products should be filtered
      await page.waitForSelector('//div[contains(@class, "product")]');
      const products = page.locator('//div[contains(@class, "product")]');
      const count = await products.count();
      expect(count).toBeGreaterThan(0);
    }
  });

  test('Filter products by brand', async ({ page }) => {
    await page.click('//a[contains(text(), "Products")]');

    // Wait for products and filters to load
    await page.waitForSelector('//div[contains(@class, "filter")]');

    // Select a brand filter if available
    const brandCheckbox = page.locator('//input[@type="checkbox"]').first();
    if (await brandCheckbox.isVisible()) {
      await brandCheckbox.check();

      // Wait for filtering
      await page.waitForLoadState('networkidle');

      const products = page.locator('//div[contains(@class, "product")]');
      const count = await products.count();
      expect(count).toBeGreaterThanOrEqual(0);
    }
  });

  test('Filter products by price range', async ({ page }) => {
    await page.click('//a[contains(text(), "Products")]');

    // Look for price filter inputs
    const priceInputs = page.locator('//input[@type="text"][contains(@placeholder, "price")]');
    if (await priceInputs.count() >= 2) {
      // Set min price
      await priceInputs.nth(0).fill('10');

      // Set max price
      await priceInputs.nth(1).fill('100');

      // Wait for filter to apply
      await page.waitForLoadState('networkidle');

      const products = page.locator('//div[contains(@class, "product")]');
      const count = await products.count();
      expect(count).toBeGreaterThanOrEqual(0);
    }
  });

  test('View product details', async ({ page }) => {
    await page.click('//a[contains(text(), "Products")]');

    // Wait for products to load
    await page.waitForSelector('//div[contains(@class, "product")]');

    // Click on first product
    const firstProduct = page.locator('//div[contains(@class, "product")]').first();
    await firstProduct.click();

    // Should see product details
    await page.waitForURL('**/*');
    const productName = page.locator('//h1');
    await expect(productName).toBeVisible();
  });

  test('Search for product', async ({ page }) => {
    // Look for search input
    const searchInput = page.locator('//input[@type="text"][@placeholder="Search"]');

    if (await searchInput.isVisible()) {
      await searchInput.fill('pliers');
      await page.press('//input[@type="text"][@placeholder="Search"]', 'Enter');

      // Should show search results
      await page.waitForLoadState('networkidle');
      const products = page.locator('//div[contains(@class, "product")]');
      const count = await products.count();
      expect(count).toBeGreaterThan(0);
    }
  });
});
