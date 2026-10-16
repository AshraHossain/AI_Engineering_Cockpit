import { test, expect } from '@playwright/test';

const testAccounts = [
  { email: 'admin@practicesoftwaretesting.com', password: 'welcome01', role: 'admin' },
  { email: 'customer@practicesoftwaretesting.com', password: 'welcome01', role: 'user' },
  { email: 'customer2@practicesoftwaretesting.com', password: 'welcome01', role: 'user' },
  { email: 'customer3@practicesoftwaretesting.com', password: 'pass123', role: 'user' },
];

test.describe('Authentication Tests', () => {
  testAccounts.forEach((account) => {
    test(`Login as ${account.role}: ${account.email}`, async ({ page }) => {
      // Navigate to login page
      await page.goto('/');
      await page.click('//a[@href="#/customer/login"]');

      // Enter credentials
      await page.fill('//input[@placeholder="E-mail"]', account.email);
      await page.fill('//input[@placeholder="Password"]', account.password);

      // Click login
      await page.click('//button[@type="submit"]');

      // Wait for redirect and verify login
      await page.waitForURL('**/*');

      // Check if user is logged in (look for user menu or logout button)
      const logoutButton = page.locator('//a[contains(text(), "Logout")]');
      await expect(logoutButton).toBeVisible({ timeout: 5000 });
    });
  });

  test('Login with invalid credentials', async ({ page }) => {
    await page.goto('/');
    await page.click('//a[@href="#/customer/login"]');

    await page.fill('//input[@placeholder="E-mail"]', 'invalid@test.com');
    await page.fill('//input[@placeholder="Password"]', 'wrongpassword');
    await page.click('//button[@type="submit"]');

    // Should see error message
    const errorMsg = page.locator('//div[contains(@class, "alert")]');
    await expect(errorMsg).toContainText('Invalid', { timeout: 5000 });
  });

  test('Logout successfully', async ({ page }) => {
    // Login first
    await page.goto('/');
    await page.click('//a[@href="#/customer/login"]');
    await page.fill('//input[@placeholder="E-mail"]', 'customer@practicesoftwaretesting.com');
    await page.fill('//input[@placeholder="Password"]', 'welcome01');
    await page.click('//button[@type="submit"]');

    // Wait for login to complete
    await page.waitForURL('**/*');

    // Logout
    await page.click('//a[contains(text(), "Logout")]');

    // Should redirect to home
    await page.waitForURL('**/*');
    const loginLink = page.locator('//a[@href="#/customer/login"]');
    await expect(loginLink).toBeVisible();
  });
});
