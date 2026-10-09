# Playwright CLI Learning Project

Traditional Playwright test automation using the CLI approach. This project automates testing for [Practice Software Testing](https://practicesoftwaretesting.com).

## What You'll Learn

- **Locator Strategies**: XPath, CSS selectors, accessibility locators
- **Test Structure**: Test suites, hooks (beforeEach, afterEach), data parameterization
- **Assertions**: Expect API, visibility checks, text matching
- **Navigation**: Page routing, URL waiting, network waits
- **Interaction**: Click, fill, select, drag, upload files
- **Screenshots & Traces**: Debug info collection on failures
- **Multi-browser Testing**: Run same tests across Chrome, Firefox, Safari
- **Test Reports**: HTML reports with traces

## Setup

```bash
npm install
```

## Test Accounts

```
Admin:    admin@practicesoftwaretesting.com / welcome01
User 1:   customer@practicesoftwaretesting.com / welcome01
User 2:   customer2@practicesoftwaretesting.com / welcome01
User 3:   customer3@practicesoftwaretesting.com / pass123
```

## Running Tests

```bash
# Run all tests
npm test

# Run specific test file
npm run test:auth
npm run test:products
npm run test:cart

# Run in headed mode (see browser)
npm run test:headed

# Run in debug mode (step through tests)
npm run test:debug

# Run on specific browser
npm run test:chrome
npm run test:firefox
npm run test:webkit

# View HTML report
npm run test:report
```

## Test Files

### `auth.spec.ts`
- Login with valid credentials (all 4 accounts)
- Login with invalid credentials
- Logout flow

**Key Learning**: Parameterized tests, error message validation

### `products.spec.ts`
- Browse product list
- Filter by category
- Filter by brand
- Filter by price range
- View product details
- Search functionality

**Key Learning**: Waiting strategies (selector waits, network waits), filter interactions

### `cart.spec.ts`
- Add product to cart
- View cart contents
- Update item quantity
- Remove items from cart
- Proceed to checkout

**Key Learning**: State verification, form interactions, flow continuation

## Key Concepts

### Locators
```typescript
// XPath
page.locator('//button[@type="submit"]')

// CSS
page.locator('.btn-primary')

// Accessible name
page.locator('button:has-text("Add to cart")')
```

### Waiting
```typescript
// Wait for element
await page.waitForSelector('//div[contains(@class, "product")]');

// Wait for navigation
await page.waitForURL('**/*');

// Wait for network to be idle
await page.waitForLoadState('networkidle');
```

### Assertions
```typescript
// Visibility
await expect(element).toBeVisible();

// Text content
await expect(element).toContainText('Expected text');

// Count
expect(count).toBeGreaterThan(0);
```

### Fixtures & Hooks
```typescript
test.beforeEach(async ({ page }) => {
  // Runs before each test
});

test.afterEach(async ({ page }) => {
  // Runs after each test
});
```

## Configuration (playwright.config.ts)

- **Base URL**: https://practicesoftwaretesting.com
- **Test Directory**: ./tests
- **Reporters**: HTML (generated after test run)
- **Screenshot**: Only on failure
- **Trace**: On first retry

Edit config to:
- Add slow timeout: `timeout: 30000`
- Change retry count: `retries: 3`
- Modify browsers: Add/remove from `projects` array
- Change parallelization: Set `workers`

## Debugging

```bash
# Debug mode - browser stops at each step
npm run test:debug

# Headed mode - watch tests run
npm run test:headed

# View traces after failure
npm run test:report
```

Click on failed test → View trace → Step through with timeline

## Next Steps

1. **Modify XPaths**: Site structure might differ; use DevTools to find correct selectors
2. **Add Error Cases**: Test validation, network errors, timeouts
3. **Extend Test Data**: Add more user accounts, products
4. **Refactor**: Extract common login logic into a fixture
5. **API Testing**: Add API calls alongside UI tests
6. **Database Seeding**: Reset test data between runs

## Comparison: CLI vs MCP

See `17-playwright-mcp-learning` for MCP approach:

| Aspect | CLI | MCP |
|--------|-----|-----|
| **Control** | Full via `@playwright/test` | Claude/LLM directs browser |
| **Code** | Write TypeScript manually | AI generates test instructions |
| **Debugging** | Built-in tools, traces | LLM interprets results |
| **Maintenance** | You manage selectors | AI adapts to changes |
| **Learning Curve** | Manual but explicit | Requires MCP understanding |

## Resources

- [Playwright Docs](https://playwright.dev)
- [Locator Guide](https://playwright.dev/docs/locators)
- [Best Practices](https://playwright.dev/docs/best-practices)
- [Test Examples](https://github.com/testsmith-io/practice-software-testing)
