# Playwright MCP Learning Project

Model Context Protocol (MCP) integration for test automation using Claude Code. This project demonstrates how Claude can directly control browsers via MCP tools to perform testing tasks.

## What You'll Learn

- **MCP Concept**: How Claude Code integrates with external tools via MCP
- **Browser Control**: Programmatic browser automation via MCP tools
- **AI-Driven Testing**: Let Claude/LLM plan and execute test scenarios
- **Tool Composition**: Combining multiple MCP tools for complex flows
- **Adaptive Testing**: AI adjusts test steps based on actual DOM state
- **Real-time Feedback**: Screenshots and page analysis guide test execution

## Playwright MCP Tools Available

When the Playwright MCP plugin is enabled in Claude Code, these tools become available:

### Navigation
- `browser_navigate` - Navigate to URL
- `browser_navigate_back` - Go back in history
- `browser_tabs` - List open browser tabs
- `browser_new_page` - Open new page/tab

### Interaction
- `browser_click` - Click an element (by selector/coordinates)
- `browser_fill_form` - Fill form fields
- `browser_type` - Type text into focused element
- `browser_select_option` - Select dropdown option
- `browser_press_key` - Press keyboard keys
- `browser_drag` - Drag element
- `browser_drop` - Drop element
- `browser_file_upload` - Upload file to input

### Inspection
- `browser_find` - Find elements by selector/text
- `browser_take_screenshot` - Take screenshot
- `browser_snapshot` - Get page snapshot
- `browser_evaluate` - Run JavaScript in page context

### Waiting & Checking
- `browser_wait_for` - Wait for condition (selector, navigation, etc)
- `browser_network_requests` - Get network requests
- `browser_console_messages` - Get console logs
- `browser_handle_dialog` - Handle alerts/prompts

## How It Works

```
You (Claude Code Session)
    ↓
MCP Request → Playwright MCP Server
    ↓
Playwright Controls Real Browser
    ↓
Screenshot/Data → MCP Response
    ↓
Claude Analyzes & Plans Next Step
```

## Getting Started

1. Ensure Playwright MCP plugin is enabled in Claude Code (`/mcp`)
2. Create or open a Claude Code session in this project
3. Use the tool references below to control the browser

## Example Workflows

### Workflow 1: Login Test

```
1. Navigate to https://practicesoftwaretesting.com
2. Click login button
3. Fill email: customer@practicesoftwaretesting.com
4. Fill password: welcome01
5. Click submit
6. Wait for redirect
7. Take screenshot to verify logged in state
8. Look for user menu or logout button
```

### Workflow 2: Product Search & Filter

```
1. Navigate to products page
2. Find search input by placeholder "Search"
3. Type "pliers"
4. Press Enter
5. Wait for results to load
6. Take screenshot of filtered results
7. Extract product names and prices
8. Verify results match search term
```

### Workflow 3: Shopping Cart Flow

```
1. Login (see Workflow 1)
2. Navigate to products
3. Find first product card
4. Click it to view details
5. Click "Add to cart"
6. Navigate to cart
7. Verify item is in cart
8. Update quantity
9. Verify total updated
10. Click checkout
```

## Test Accounts

```
Admin:    admin@practicesoftwaretesting.com / welcome01
User 1:   customer@practicesoftwaretesting.com / welcome01
User 2:   customer2@practicesoftwaretesting.com / welcome01
User 3:   customer3@practicesoftwaretesting.com / pass123
```

## Using MCP with Claude Code

### Option 1: Interactive Testing
In Claude Code session, you can ask Claude to test scenarios:

```
"Test the login flow for customer@practicesoftwaretesting.com"
```

Claude will:
1. Invoke `browser_navigate` to go to site
2. Use `browser_find` to locate login button
3. Use `browser_click` to click it
4. Use `browser_fill_form` to enter credentials
5. Use `browser_click` to submit
6. Use `browser_take_screenshot` to capture state
7. Analyze and report results

### Option 2: Scripted Testing (via `/loop`)
Set up a recurring test cycle:

```
/loop 1h "Use Playwright MCP tools to run a full authentication test against practicesoftwaretesting.com with all four test accounts, take screenshots of success/failure, and report results"
```

### Option 3: Manual Tool Usage
Directly invoke MCP tools:

```
browser_navigate("https://practicesoftwaretesting.com")
browser_take_screenshot()
browser_find("login")
browser_click(ref: "ref_1")
```

## Selectors & Finding Elements

MCP provides multiple ways to find elements:

### By Text
```
Find "Login" button
Find "Add to cart" text
```

### By CSS Selector
```
Find ".btn-primary"
Find "input[type='email']"
```

### By XPath
```
Find "//button[@type='submit']"
Find "//div[contains(@class, 'product')]"
```

### By Accessibility
```
Find "Sign in" button (by accessible name)
Find heading "Products"
```

MCP intelligently matches your description to DOM elements.

## Advantages of MCP Approach

| Aspect | CLI | MCP |
|--------|-----|-----|
| **Setup Time** | Install npm packages | Just enable plugin |
| **Selector Writing** | Manual XPath/CSS | AI matches descriptions |
| **Test Adaptation** | Fixed selectors fail if DOM changes | AI adapts on the fly |
| **Debugging** | Read test code + traces | Real-time screenshots & interaction |
| **Natural Language** | No, use TypeScript | Yes, describe what to test |
| **Exploratory Testing** | Difficult | Natural fit |
| **Complex Flows** | Verbose test code | Conversational narration |
| **Maintenance** | Update selectors manually | AI adjusts automatically |

## Testing Scenarios

### Authentication
- [ ] Login all 4 accounts successfully
- [ ] Test invalid credentials
- [ ] Verify logout works
- [ ] Test session persistence

### Products
- [ ] Browse product list
- [ ] Filter by category
- [ ] Filter by price range
- [ ] Search for products
- [ ] View product details
- [ ] Verify product images load
- [ ] Check product availability

### Shopping
- [ ] Add products to cart
- [ ] Update quantities
- [ ] Remove items
- [ ] View cart subtotals
- [ ] Proceed to checkout
- [ ] Verify checkout form

### Advanced
- [ ] Test with multiple products
- [ ] Test without JavaScript (if applicable)
- [ ] Test mobile viewport
- [ ] Check for accessibility issues
- [ ] Verify error messages
- [ ] Test network disconnection

## Tips for MCP Testing

1. **Take Screenshots Often**
   - After navigation
   - After key interactions
   - On assertion failures
   - To capture state changes

2. **Use Waits Strategically**
   - Wait after clicks that cause navigation
   - Wait for selectors before interacting
   - Don't wait if element is already visible

3. **Describe Elements Naturally**
   ```
   Bad:  //button[@class='btn' and contains(text(), 'Add')]
   Good: "Add to cart" button
   
   Bad:  input[type="email"][name="user_email"]
   Good: Email input field
   ```

4. **Leverage Screenshots for Debugging**
   - Asks Claude to analyze what it sees
   - Helps identify layout changes
   - Captures unexpected states

5. **Build Reusable Flows**
   - Login flow (used by multiple tests)
   - Product search (foundation for filtering)
   - Checkout (foundation for order tests)

## Comparison: CLI vs MCP

### CLI Approach (16-playwright-cli-learning)
✅ Pros:
- Full control over test logic
- Can implement complex algorithms
- No external tool dependencies
- Familiar to developers

❌ Cons:
- Must update selectors when DOM changes
- Verbose test code
- Manual error handling

### MCP Approach (17-playwright-mcp-learning)
✅ Pros:
- Natural language test descriptions
- Adaptive to DOM changes
- Quick to write and modify
- Great for exploratory testing
- Screenshots aid debugging

❌ Cons:
- Depends on Claude's understanding of context
- Less predictable than hardcoded tests
- More tokens/API calls per test
- Harder to test edge cases

## Next Steps

1. **Try Interactive Testing**
   - Open Claude Code in this project
   - Ask it to test a login flow
   - Watch it use MCP tools in real-time

2. **Set Up Automation**
   - Use `/loop` to run tests on schedule
   - Set up recurring test cycles
   - Monitor results over time

3. **Combine Approaches**
   - Use CLI for critical, stable tests
   - Use MCP for exploratory/regression testing
   - Hybrid approach gives best of both

4. **Extend Coverage**
   - Add API integration tests
   - Test error scenarios
   - Performance testing

## Resources

- [Playwright MCP on GitHub](https://github.com/agenthub-ai/playwright-mcp)
- [Claude Code Documentation](https://claude.com/claude-code)
- [MCP Protocol](https://modelcontextprotocol.io)
- [Practice Software Testing](https://practicesoftwaretesting.com)
- [Test Reference Repo](https://github.com/testsmith-io/practice-software-testing)

## Troubleshooting

### Tool Not Found
Ensure Playwright MCP plugin is enabled:
```
/mcp
# Find "playwright" plugin and enable it
```

### Screenshots Blank
- Add wait before screenshot: `browser_wait_for("selector")`
- Check if page is navigating slowly
- Increase wait timeout

### Selectors Not Matching
MCP is fuzzy; try:
- More descriptive text: "Submit" button vs "Send"
- Include visible context: "Email input in login form"
- Use visible labels instead of hidden ones

### Tests Failing Unexpectedly
- Take screenshot to see current state
- Check browser console for errors
- Verify test credentials still work
- Check if site structure changed
