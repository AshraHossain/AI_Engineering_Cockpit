/**
 * End-to-End Checkout Flow Test
 */
import { test } from '@playwright/test';
import LoginPage from '../../fixtures/page-objects/LoginPage';
import ProductPage from '../../fixtures/page-objects/ProductPage';
import CartPage from '../../fixtures/page-objects/CartPage';
import CheckoutPage from '../../fixtures/page-objects/CheckoutPage';
import { TestUsers, CheckoutData } from '../../utils/test-data';

test('should complete end-to-end checkout', async ({ page }) => {
  const loginPage = new LoginPage(page);
  const productPage = new ProductPage(page);
  const cartPage = new CartPage(page);
  const checkoutPage = new CheckoutPage(page);

  // Step 1: Login
  console.log('Step 1: Logging in...');
  await loginPage.navigate();
  await loginPage.login(TestUsers.customer1.email, TestUsers.customer1.password);

  // Step 2: Browse and add product
  console.log('Step 2: Browsing products...');
  await productPage.navigate();
  await productPage.addProductToCart(0);

  // Step 3: Go to cart
  console.log('Step 3: Opening cart...');
  await cartPage.openCart();
  const itemCount = await cartPage.getCartItemCount();
  console.log(`Cart has ${itemCount} items`);

  // Step 4: Checkout
  console.log('Step 4: Proceeding to checkout...');
  await cartPage.proceedToCheckout();

  const checkoutData = CheckoutData.validCheckout();
  await checkoutPage.fillShippingAddress(checkoutData);
  await checkoutPage.selectPaymentMethod('credit_card');
  await checkoutPage.placeOrder();

  // Step 5: Verify success
  console.log('Step 5: Verifying order...');
  await checkoutPage.assertOrderSuccess();
  console.log('✓ Checkout completed successfully!');
});
