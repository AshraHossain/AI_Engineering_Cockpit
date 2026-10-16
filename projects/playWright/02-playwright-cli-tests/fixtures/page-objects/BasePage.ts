/**
 * Base page class with common methods
 */
import { Page, expect } from '@playwright/test';

export class BasePage {
  protected page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  async goto(path: string = '') {
    await this.page.goto(path);
  }

  async fillField(selector: string, text: string) {
    await this.page.fill(selector, text);
    await expect(this.page.locator(selector)).toHaveValue(text);
  }

  async clickButton(selector: string) {
    await this.page.click(selector);
  }

  async waitForElement(selector: string, timeout: number = 5000) {
    await this.page.waitForSelector(selector, { state: 'visible', timeout });
  }

  async getText(selector: string): Promise<string | null> {
    return await this.page.textContent(selector);
  }

  async isVisible(selector: string): Promise<boolean> {
    try {
      await this.waitForElement(selector, 1000);
      return true;
    } catch {
      return false;
    }
  }

  async screenshot(name: string) {
    await this.page.screenshot({ path: `screenshots/${name}.png` });
  }

  async getCurrentURL(): Promise<string> {
    return this.page.url();
  }
}

export default BasePage;
