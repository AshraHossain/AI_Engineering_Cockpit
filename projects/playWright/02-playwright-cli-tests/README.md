# Playwright CLI Tests

Automated tests for https://practicesoftwaretesting.com using Playwright Test (CLI).

## Setup

```bash
# Install dependencies
npm install

# Install browsers
npx playwright install

# Run tests
npm test
```

## Project Structure

```
tests/
  ├── auth/               # Login/authentication tests
  ├── products/           # Product browsing tests
  ├── cart/               # Shopping cart tests
  └── checkout/           # Checkout flow tests

fixtures/page-objects/    # Page Object Model classes
  ├── BasePage.ts         # Base class with common methods
  ├── LoginPage.ts
  ├── ProductPage.ts
  ├── CartPage.ts
  └── CheckoutPage.ts

utils/
  ├── selectors.ts        # CSS selectors (single source of truth)
  └── test-data.ts        # Test users and data

playwright.config.ts      # Playwright test configuration
```

## Running Tests

### Run all tests
```bash
npm test
```

### Run tests in headed mode (see browser)
```bash
npm run test:headed
```

### Run specific test file
```bash
npx playwright test tests/auth/login.spec.ts
```

### Run tests in debug mode
```bash
npm run test:debug
```

### Run tests with UI
```bash
npm run test:ui
```

### View test report
```bash
npm run test:report
```

### Run tests by category
```bash
npm run test:auth       # Login tests
npm run test:products   # Product tests
npm run test:checkout   # Checkout tests
```

## Page Object Model

All tests use the Page Object Model pattern:

```typescript
// Tests are readable
await loginPage.login(user.email, user.password);
await productPage.addProductToCart(0);
await cartPage.proceedToCheckout();

// No selectors in tests
// Page objects handle all element interactions
```

## Test Data

Test users and products defined in `utils/test-data.ts`:

```typescript
const user = TestUsers.customer1;  // email, password, role
const checkout = CheckoutData.validCheckout();  // address, city, zip, etc.
```

## Selectors

All CSS selectors centralized in `utils/selectors.ts`:

```typescript
Selectors.login.emailInput         // "input#email"
Selectors.products.addToCartButton // ".btn-add-to-cart"
Selectors.cart.checkoutButton      // ".btn-checkout"
```

### Why centralized?
- Easy to update when UI changes
- Single source of truth
- Reusable across tests
- Supports AI-driven selector repair

## Best Practices

1. **Use Page Objects** - Never reference selectors directly in tests
2. **Keep Tests Independent** - No ordering or shared state
3. **Use Meaningful Names** - Test names describe what the user does
4. **Parametrize Data** - Use test-data.ts for variations
5. **Fail Fast** - Assertions should be explicit and clear

## CI/CD Integration

Tests run in CI with:
- Headless mode
- Automatic retries on failure
- Screenshots on failure
- Video recordings on failure
- HTML reports
- JUnit XML reports

## Adding New Tests

1. Create page object in `fixtures/page-objects/`:
```typescript
export class MyPage extends BasePage {
  async myAction() { /* ... */ }
}
```

2. Create test file in `tests/`:
```typescript
test('should do something', async ({ page }) => {
  const myPage = new MyPage(page);
  await myPage.myAction();
});
```

3. Run test:
```bash
npm test
```

## Troubleshooting

### Test fails with "Timeout waiting for selector"
- Check selectors in `utils/selectors.ts`
- Verify site structure matches
- Try `npm run test:headed` to see what's happening

### Install errors
```bash
npm ci                    # Clean install
npm install
npx playwright install    # Reinstall browsers
```

### Clear reports
```bash
rm -rf playwright-report test-results
```

## Files Created

| File | Purpose |
|------|---------|
| `package.json` | Node project and dependencies |
| `playwright.config.ts` | Test runner configuration |
| `tsconfig.json` | TypeScript configuration |
| `utils/selectors.ts` | CSS selectors (single source of truth) |
| `utils/test-data.ts` | Test fixtures (users, products, checkout data) |
| `fixtures/page-objects/BasePage.ts` | Base class with common methods |
| `fixtures/page-objects/*Page.ts` | Page objects for each page |
| `tests/auth/login.spec.ts` | Login test cases |
| `tests/products/browse.spec.ts` | Product browsing tests |
| `tests/cart/add-to-cart.spec.ts` | Cart functionality tests |
| `tests/checkout/checkout-flow.spec.ts` | End-to-end checkout test |

## Architecture

```
Test Suite (Jest-like runner)
  ↓
Page Objects (semantic methods)
  ↓
Selectors (CSS, stable)
  ↓
Test Data (fixtures, parametrized)
  ↓
Playwright (browser automation)
  ↓
Target Website
```

## Next Steps

1. Run tests locally: `npm test`
2. View report: `npm run test:report`
3. Inspect failures: `npm run test:debug`
4. Add new tests following the pattern
5. Integrate with CI/CD (GitHub Actions, GitLab CI, etc.)

## Key Concepts for AI Integration

### Test Parameterization
Easy to generate test variations by iterating TestUsers, TestProducts

### Selector Centralization
All selectors in one file enables AI-driven repair when selectors break

### Page Object Reusability
Agents can compose test scenarios from page object methods

### Structured Test Data
Fixtures enable AI to generate synthetic test cases

---

**Architecture:** Playwright Test + TypeScript + Page Object Model  
**Goal:** Demonstrate test automation best practices  
**AI Forward:** Foundation for test generation and repair  
