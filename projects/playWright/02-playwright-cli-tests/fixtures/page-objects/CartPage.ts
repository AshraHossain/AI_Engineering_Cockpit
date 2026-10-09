/**
 * Cart Page Object
 */
import { Page, expect } from '@playwright/test';
import BasePage from './BasePage';
import Selectors from '../../utils/selectors';

export class CartPage extends BasePage {
  constructor(page: Page) {
    super(page);
  }

  async navigate() {
    await this.goto('/cart');
  }

  async openCart() {
    await this.clickButton(Selectors.nav.cartLink);
    await this.page.waitForLoadState('networkidle');
  }

  async getCartItemCount(): Promise<number> {
    return await this.page.locator(Selectors.cart.cartItems).count();
  }

  async removeItem(index: number = 0) {
    const buttons = await this.page.locator(Selectors.cart.removeButton).all();
    if (index < buttons.length) {
      await buttons[index].click();
    }
  }

  async proceedToCheckout() {
    await this.clickButton(Selectors.cart.checkoutButton);
    await this.page.waitForLoadState('networkidle');
  }

  async assertEmptyCart() {
    await expect(this.page.locator(Selectors.cart.emptyCartMessage)).toBeVisible();
  }

  async getCartTotal(): Promise<string | null> {
    return await this.getText(Selectors.cart.cartTotal);
  }
}

export default CartPage;
