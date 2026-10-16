/**
 * Product Browsing Tests
 */
import { test } from '@playwright/test';
import LoginPage from '../../fixtures/page-objects/LoginPage';
import ProductPage from '../../fixtures/page-objects/ProductPage';
import { TestUsers } from '../../utils/test-data';

test.describe('Product Browsing', () => {
  test.beforeEach(async ({ page }) => {
    const loginPage = new LoginPage(page);
    await loginPage.navigate();
    await loginPage.login(TestUsers.customer1.email, TestUsers.customer1.password);
  });

  test('should display product list', async ({ page }) => {
    const productPage = new ProductPage(page);
    await productPage.navigate();

    const count = await productPage.getProductCount();
    console.log(`Found ${count} products`);
  });

  test('should get product price', async ({ page }) => {
    const productPage = new ProductPage(page);
    await productPage.navigate();

    const price = await productPage.getProductPrice(0);
    console.log(`First product price: ${price}`);
  });
});
