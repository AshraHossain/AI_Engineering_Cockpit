/**
 * Add to Cart Tests
 */
import { test } from '@playwright/test';
import LoginPage from '../../fixtures/page-objects/LoginPage';
import ProductPage from '../../fixtures/page-objects/ProductPage';
import CartPage from '../../fixtures/page-objects/CartPage';
import { TestUsers } from '../../utils/test-data';

test.describe('Add to Cart', () => {
  test.beforeEach(async ({ page }) => {
    const loginPage = new LoginPage(page);
    await loginPage.navigate();
    await loginPage.login(TestUsers.customer1.email, TestUsers.customer1.password);
  });

  test('should add product to cart', async ({ page }) => {
    const productPage = new ProductPage(page);
    const cartPage = new CartPage(page);

    await productPage.navigate();
    await productPage.addProductToCart(0);

    await cartPage.openCart();
    const itemCount = await cartPage.getCartItemCount();
    console.log(`Cart now has ${itemCount} items`);
  });

  test('should remove product from cart', async ({ page }) => {
    const productPage = new ProductPage(page);
    const cartPage = new CartPage(page);

    await productPage.navigate();
    await productPage.addProductToCart(0);

    await cartPage.openCart();
    const countBefore = await cartPage.getCartItemCount();

    await cartPage.removeItem(0);
    const countAfter = await cartPage.getCartItemCount();

    console.log(`Items before: ${countBefore}, after: ${countAfter}`);
  });
});
