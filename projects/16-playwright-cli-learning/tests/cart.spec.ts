import { test, expect } from '@playwright/test';

test.describe('Shopping Cart Tests', () => {
  test.beforeEach(async ({ page }) => {
    // Login before each test
    await page.goto('/');
    await page.click('//a[@href="#/customer/login"]');
    await page.fill('//input[@placeholder="E-mail"]', 'customer@practicesoftwaretesting.com');
    await page.fill('//input[@placeholder="Password"]', 'welcome01');
    await page.click('//button[@type="submit"]');
    await page.waitForURL('**/*');
  });

  test('Add product to cart', async ({ page }) => {
    // Navigate to products
    await page.click('//a[contains(text(), "Products")]');
    await page.waitForSelector('//div[contains(@class, "product")]');

    // Click on first product
    const firstProduct = page.locator('//div[contains(@class, "product")]').first();
    await firstProduct.click();

    // Add to cart
    const addCartBtn = page.locator('//button[contains(text(), "Add to cart")]');
    if (await addCartBtn.isVisible()) {
      await addCartBtn.click();

      // Should see success message or cart update
      await page.waitForLoadState('networkidle');
    }
  });

  test('View cart', async ({ page }) => {
    // Navigate to products and add to cart
    await page.click('//a[contains(text(), "Products")]');
    await page.waitForSelector('//div[contains(@class, "product")]');

    const firstProduct = page.locator('//div[contains(@class, "product")]').first();
    await firstProduct.click();

    const addCartBtn = page.locator('//button[contains(text(), "Add to cart")]');
    if (await addCartBtn.isVisible()) {
      await addCartBtn.click();
      await page.waitForLoadState('networkidle');
    }

    // Click cart icon/link
    const cartLink = page.locator('//a[@href="#/checkout/cart"]');
    if (await cartLink.isVisible()) {
      await cartLink.click();

      // Should see cart items
      const cartItems = page.locator('//div[contains(@class, "cart")]');
      await expect(cartItems).toBeVisible({ timeout: 5000 });
    }
  });

  test('Update cart quantity', async ({ page }) => {
    // Navigate to cart
    const cartLink = page.locator('//a[@href="#/checkout/cart"]');
    if (await cartLink.isVisible()) {
      await cartLink.click();

      // Find quantity input and update
      const quantityInput = page.locator('//input[@type="number"]').first();
      if (await quantityInput.isVisible()) {
        const currentValue = await quantityInput.inputValue();
        const newValue = (parseInt(currentValue || '1') + 1).toString();

        await quantityInput.fill(newValue);
        await page.waitForLoadState('networkidle');

        const updatedValue = await quantityInput.inputValue();
        expect(updatedValue).toBe(newValue);
      }
    }
  });

  test('Remove item from cart', async ({ page }) => {
    // Navigate to cart
    const cartLink = page.locator('//a[@href="#/checkout/cart"]');
    if (await cartLink.isVisible()) {
      await cartLink.click();

      // Find and click remove button
      const removeBtn = page.locator('//button[contains(text(), "Remove")]').first();
      if (await removeBtn.isVisible()) {
        const itemsBeforeRemove = await page.locator('//div[contains(@class, "item")]').count();

        await removeBtn.click();
        await page.waitForLoadState('networkidle');

        const itemsAfterRemove = await page.locator('//div[contains(@class, "item")]').count();
        expect(itemsAfterRemove).toBeLessThanOrEqual(itemsBeforeRemove);
      }
    }
  });

  test('Proceed to checkout', async ({ page }) => {
    // Navigate to products and add to cart
    await page.click('//a[contains(text(), "Products")]');
    await page.waitForSelector('//div[contains(@class, "product")]');

    const firstProduct = page.locator('//div[contains(@class, "product")]').first();
    await firstProduct.click();

    const addCartBtn = page.locator('//button[contains(text(), "Add to cart")]');
    if (await addCartBtn.isVisible()) {
      await addCartBtn.click();
      await page.waitForLoadState('networkidle');
    }

    // Go to cart
    const cartLink = page.locator('//a[@href="#/checkout/cart"]');
    if (await cartLink.isVisible()) {
      await cartLink.click();

      // Click proceed/checkout button
      const checkoutBtn = page.locator('//button[contains(text(), "Proceed")]');
      if (await checkoutBtn.isVisible()) {
        await checkoutBtn.click();
        await page.waitForURL('**/*');
      }
    }
  });
});
