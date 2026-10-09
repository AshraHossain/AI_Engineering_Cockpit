/**
 * Checkout Page Object
 */
import { Page, expect } from '@playwright/test';
import BasePage from './BasePage';
import Selectors from '../../utils/selectors';

export interface CheckoutData {
  address: string;
  city: string;
  zip: string;
  country: string;
  paymentMethod: string;
}

export class CheckoutPage extends BasePage {
  constructor(page: Page) {
    super(page);
  }

  async fillShippingAddress(data: CheckoutData) {
    await this.fillField(Selectors.checkout.addressInput, data.address);
    await this.fillField(Selectors.checkout.cityInput, data.city);
    await this.fillField(Selectors.checkout.zipInput, data.zip);
    await this.page.selectOption(Selectors.checkout.countrySelect, data.country);
  }

  async selectPaymentMethod(method: string) {
    await this.page.selectOption(Selectors.checkout.paymentMethod, method);
  }

  async fillCardNumber(cardNumber: string) {
    await this.fillField(Selectors.checkout.cardNumber, cardNumber);
  }

  async placeOrder() {
    await this.clickButton(Selectors.checkout.placeOrderButton);
  }

  async assertOrderSuccess() {
    await expect(this.page.locator(Selectors.checkout.orderConfirmation)).toBeVisible();
  }

  async getOrderConfirmationText(): Promise<string | null> {
    return await this.getText(Selectors.checkout.orderConfirmation);
  }
}

export default CheckoutPage;
