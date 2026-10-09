# Playwright MCP Tools Reference

Complete reference for all Playwright MCP tools available in Claude Code.

## Navigation Tools

### `browser_navigate`
Navigate to a URL.

**Usage**: "Go to https://practicesoftwaretesting.com"

**Returns**: Page loaded, ready for interaction

### `browser_navigate_back`
Go back in browser history.

**Usage**: "Go back to previous page"

**Returns**: Previous page loaded

### `browser_tabs`
List all open browser tabs/pages.

**Usage**: "Show me open tabs"

**Returns**: Array of tab info with IDs and URLs

### `browser_new_page`
Open a new browser tab.

**Usage**: "Open a new tab"

**Returns**: New page ID for future interactions

### `browser_close`
Close the current page/tab or browser.

**Usage**: "Close the browser"

**Returns**: Browser closed

---

## Interaction Tools

### `browser_click`
Click an element.

**Usage**: 
- "Click the login button"
- "Click on 'Add to cart'"
- "Click element with ref_5"

**Options**:
- Click by text description (AI finds it)
- Click by selector (CSS/XPath)
- Click by ref ID (from previous find)
- Click by coordinates (x, y)

**Returns**: Success/failure, updated page state

### `browser_type`
Type text into focused element.

**Usage**: "Type 'customer@practicesoftwaretesting.com'"

**Prerequisites**: Element must be focused (use click first)

**Returns**: Text typed, focus still on element

### `browser_fill_form`
Fill multiple form fields at once.

**Usage**: 
```
Fill the login form:
- email: customer@practicesoftwaretesting.com
- password: welcome01
```

**Returns**: All fields filled, form ready to submit

### `browser_select_option`
Select option from dropdown/select element.

**Usage**: "Select 'USA' from country dropdown"

**Works With**: `<select>` elements, custom dropdowns with aria-listbox

**Returns**: Option selected

### `browser_press_key`
Press keyboard keys.

**Usage**: 
- "Press Enter"
- "Press Tab"
- "Press Escape"
- "Press Ctrl+A"

**Keys**: Enter, Tab, Escape, ArrowDown, ArrowUp, Delete, Backspace, etc.

**Returns**: Key pressed, page responds

### `browser_drag`
Drag element from one position to another.

**Usage**: "Drag the slider to 50"

**Returns**: Element dragged, new state captured

### `browser_drop`
Drop dragged element.

**Usage**: Combined with drag for complete drag-drop sequence

**Returns**: Element dropped at location

### `browser_file_upload`
Upload file via file input.

**Usage**: "Upload 'test.pdf' to the file input"

**Prerequisites**: File must exist on system

**Returns**: File uploaded, form updated

### `browser_hover`
Hover mouse over element (triggers hover states).

**Usage**: "Hover over the help icon"

**Returns**: Element hovered, tooltips visible

---

## Inspection Tools

### `browser_find`
Find elements on page by description, selector, or text.

**Usage**:
- "Find the 'Add to cart' button"
- "Find input field for email"
- "Find product cards"

**Returns**: Element refs (ref_1, ref_2, etc.) for use in other tools

**Best Practices**:
- Describe what you see visually
- Include context: "email input in login form"
- Be specific: "Submit button" vs just "button"

### `browser_take_screenshot`
Take screenshot of current page state.

**Usage**: "Take a screenshot"

**Returns**: PNG image of current viewport

**Use After**:
- Navigation
- Key interactions
- Errors
- State changes you want to verify

### `browser_snapshot`
Get full page snapshot (HTML structure).

**Usage**: "Get page snapshot to see structure"

**Returns**: HTML of rendered page

**Use For**:
- Analyzing page structure
- Checking if element exists
- Understanding DOM hierarchy

### `browser_evaluate`
Run JavaScript in page context.

**Usage**: "Run JavaScript to get all product prices"

**Returns**: Result of JavaScript execution

**Example**:
```
Evaluate: 
  document.querySelectorAll('.product-price')
    .map(el => el.textContent)
```

### `browser_console_messages`
Get messages from browser console (logs, errors, warnings).

**Usage**: "Show console messages"

**Returns**: Array of console.log, console.error, etc.

**Use For**:
- Debugging JavaScript errors
- Finding API calls
- Checking for warnings

### `browser_network_requests`
Get network requests made by page.

**Usage**: "Show network requests"

**Returns**: List of API calls, their responses

**Use For**:
- Verifying API calls
- Checking response data
- Debugging failed requests

---

## Waiting Tools

### `browser_wait_for`
Wait for a condition (selector, navigation, text, etc).

**Usage**:
- "Wait for login button to appear"
- "Wait for page to navigate"
- "Wait for 'No items in cart' text"
- "Wait 5 seconds"

**Returns**: Condition met, continues execution

**Use Before**:
- Clicking on dynamically loaded elements
- Interacting after async operations
- Assertions on loaded content

### `browser_handle_dialog`
Handle JavaScript dialogs (alert, confirm, prompt).

**Usage**: "Click OK on the alert"

**Returns**: Dialog handled, page continues

**Dialog Types**:
- Alert (information only)
- Confirm (Yes/No choice)
- Prompt (user input)

---

## Analysis Tools

### `browser_resize`
Change viewport size (responsive testing).

**Usage**: 
- "Resize to mobile: 375x667"
- "Resize to tablet: 768x1024"
- "Resize to desktop: 1920x1080"

**Returns**: Page resized, reflow applied

### `browser_emulate_media`
Emulate media queries (light/dark mode, print, etc).

**Usage**: "Emulate dark mode"

**Returns**: Page in dark mode styling

---

## Tool Combinations

### Complete Login Flow
```
1. browser_navigate("https://practicesoftwaretesting.com")
2. browser_find("login button")
3. browser_click(ref: "ref_1")
4. browser_fill_form({
     email: "customer@practicesoftwaretesting.com",
     password: "welcome01"
   })
5. browser_find("submit button")
6. browser_click(ref: "ref_2")
7. browser_wait_for("logout link")
8. browser_take_screenshot()
```

### Product Search & Filter
```
1. browser_navigate("/products")
2. browser_find("search input")
3. browser_click(ref: "ref_1")
4. browser_type("pliers")
5. browser_press_key("Enter")
6. browser_wait_for("product results")
7. browser_take_screenshot()
8. browser_find("price filter")
```

### Shopping Cart Add
```
1. browser_navigate("/products")
2. browser_find("first product")
3. browser_click(ref: "ref_1")
4. browser_wait_for("Add to cart button")
5. browser_click("Add to cart")
6. browser_wait_for("cart icon updated")
7. browser_take_screenshot()
```

---

## Common Patterns

### Pattern: Click and Wait
Use when click causes navigation or dynamic loading:
```
browser_click("button")
browser_wait_for("new element")
browser_take_screenshot()
```

### Pattern: Fill and Submit
For form submission:
```
browser_fill_form({
  username: "user@example.com",
  password: "pass123"
})
browser_find("submit button")
browser_click(ref: "ref_1")
browser_wait_for("redirect or success")
```

### Pattern: Verify State
To confirm current state:
```
browser_take_screenshot()
browser_find("expected element")
# If find returns results, element exists
```

### Pattern: Navigate and Find
For page navigation:
```
browser_navigate("/path")
browser_wait_for("main content")
browser_find("specific element")
```

---

## Tips & Tricks

### Use Descriptive Find Queries
```
Good:  "Email input in login form"
Bad:   "input"

Good:  "Add to cart button with price"
Bad:   "button"
```

### Chain Waits for Reliability
```
browser_click("button")
browser_wait_for("loading spinner")  # Wait for spinner to appear
browser_wait_for("results visible")  # Wait for spinner to disappear
browser_take_screenshot()
```

### Handle Async Operations
```
browser_click("filter checkbox")
browser_wait_for("products list refresh")  # Don't assume instant update
browser_find("filtered products")
```

### Take Screenshots at Key Points
```
After: Login, logout, add to cart, filter, search, checkout
On: Errors, unexpected states, assertions
```

### Use Console for Complex Selectors
```
browser_evaluate("
  document.querySelectorAll('.product')
    .filter(el => el.textContent.includes('pliers'))
    .length
")
```

---

## Error Handling

### Element Not Found
When `browser_find()` returns no results:
1. Take screenshot to see current state
2. Try alternative description
3. Check if page loaded completely
4. Use `browser_wait_for()` first

### Click Fails
When click doesn't register:
1. Take screenshot to see element
2. Check if element is visible/enabled
3. Try hovering first
4. Verify exact text matches

### Navigation Fails
When page doesn't navigate:
1. Check console for JavaScript errors
2. Verify URL is correct
3. Check network requests
4. Try browser_navigate_back() then forward

---

## Performance Considerations

### Minimize Screenshots
Only take when necessary for verification or debugging.

### Batch Operations
Use `browser_fill_form()` instead of multiple `browser_type()` calls.

### Efficient Waiting
Use `browser_wait_for()` with specific conditions, not arbitrary delays.

### Selector Specificity
Specific descriptions reduce Claude's search time.

---

## Real-World Example: Complete Test

```
Test: User can search for products and add to cart

1. Navigate to site
   browser_navigate("https://practicesoftwaretesting.com")

2. Search for products
   browser_find("search input")
   browser_click(ref: "ref_1")
   browser_type("pliers")
   browser_press_key("Enter")
   browser_wait_for("product results")

3. Select first result
   browser_find("first product result")
   browser_click(ref: "ref_2")
   browser_wait_for("product detail page")

4. Add to cart
   browser_find("add to cart button")
   browser_click(ref: "ref_3")
   browser_wait_for("cart updated")

5. Verify
   browser_take_screenshot()
   browser_find("cart icon shows 1 item")
   
6. View cart
   browser_find("cart link")
   browser_click(ref: "ref_4")
   browser_wait_for("cart contents")
   browser_take_screenshot()

7. Assert
   - Screenshot shows product in cart
   - Quantity is 1
   - Price displayed correctly
```

---

## Troubleshooting Reference

| Issue | Solution |
|-------|----------|
| Element not found | Use `browser_wait_for()` first, or adjust description |
| Click doesn't work | Element may be hidden, try `browser_find()` first |
| Page not loading | Check URL, use `browser_wait_for()` for content |
| Screenshot blank | Add `browser_wait_for()` before screenshot |
| Network errors | Use `browser_network_requests()` to debug |
| Console errors | Use `browser_console_messages()` to see them |
| Text not found | May be in different format, use `browser_evaluate()` |
| Dropdown not selecting | Verify correct option text, try `browser_hover()` first |
