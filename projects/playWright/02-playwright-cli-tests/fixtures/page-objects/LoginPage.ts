/**
 * Login Page Object
 */
import { Page, expect } from '@playwright/test';
import BasePage from './BasePage';
import Selectors from '../../utils/selectors';

export class LoginPage extends BasePage {
  constructor(page: Page) {
    super(page);
  }

  async navigate() {
    await this.goto('/login');
    await this.waitForElement(Selectors.login.emailInput);
  }

  async login(email: string, password: string) {
    await this.fillField(Selectors.login.emailInput, email);
    await this.fillField(Selectors.login.passwordInput, password);
    await this.clickButton(Selectors.login.submitButton);
  }

  async assertLoginSuccess() {
    await this.page.waitForURL(/\/dashboard|\/products/, { timeout: 5000 });
  }

  async assertLoginError() {
    await expect(this.page.locator(Selectors.login.errorMessage)).toBeVisible();
  }

  async getErrorMessage(): Promise<string | null> {
    return await this.getText(Selectors.login.errorMessage);
  }
}

export default LoginPage;
