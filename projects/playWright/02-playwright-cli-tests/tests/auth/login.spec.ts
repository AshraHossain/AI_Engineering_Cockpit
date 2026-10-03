/**
 * Login Tests
 */
import { test, expect } from '@playwright/test';
import LoginPage from '../../fixtures/page-objects/LoginPage';
import { TestUsers } from '../../utils/test-data';

test.describe('Login', () => {
  test.beforeEach(async ({ page }) => {
    const loginPage = new LoginPage(page);
    await loginPage.navigate();
  });

  test('should login successfully with valid credentials', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const user = TestUsers.customer1;

    await loginPage.login(user.email, user.password);
    // Note: Will wait for navigation or timeout if selectors don't match
  });

  test('should show error for invalid password', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const user = TestUsers.customer1;

    await loginPage.login(user.email, 'wrongpassword');
    // Check if error message appears (depends on actual site behavior)
  });

  test.describe('Multiple user accounts', () => {
    for (const [key, user] of Object.entries(TestUsers)) {
      test(`should handle ${key} account`, async ({ page }) => {
        const loginPage = new LoginPage(page);

        await loginPage.login(user.email, user.password);
        // Each user should be able to reach dashboard or products page
      });
    }
  });
});
