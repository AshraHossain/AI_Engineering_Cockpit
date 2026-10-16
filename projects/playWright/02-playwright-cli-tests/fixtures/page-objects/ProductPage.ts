/**
 * Product Page Object
 */
import { Page } from '@playwright/test';
import BasePage from './BasePage';
import Selectors from '../../utils/selectors';

export class ProductPage extends BasePage {
  constructor(page: Page) {
    super(page);
  }

  async navigate() {
    await this.goto('/products');
    await this.waitForElement(Selectors.products.productCard);
  }

  async filterByCategory(category: string) {
    await this.page.selectOption(Selectors.products.filterByCategory, category);
    await this.page.waitForLoadState('networkidle');
  }

  async getProductCount(): Promise<number> {
    return await this.page.locator(Selectors.products.productCard).count();
  }

  async addProductToCart(index: number = 0) {
    const buttons = await this.page.locator(Selectors.products.addToCartButton).all();
    if (index < buttons.length) {
      await buttons[index].click();
    }
  }

  async clickProduct(index: number = 0) {
    const products = await this.page.locator(Selectors.products.productLink).all();
    if (index < products.length) {
      await products[index].click();
    }
  }

  async getProductPrice(index: number = 0): Promise<string | null> {
    const prices = await this.page.locator(Selectors.products.productPrice).all();
    if (index < prices.length) {
      return await prices[index].textContent();
    }
    return null;
  }
}

export default ProductPage;
