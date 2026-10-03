# Playwright MCP Test Scenarios

Test scenarios to run using Playwright MCP tools via Claude Code.

## How to Use This Document

Each scenario below can be used as a prompt to Claude Code. Example:

```
In the 17-playwright-mcp-learning project, using Playwright MCP tools:

"Test Scenario: Admin Login"
```

Claude will use the MCP tools to execute the scenario, take screenshots, and verify results.

---

## Authentication Scenarios

### Test Scenario: Admin Login
**Goal**: Verify admin can log in successfully

**Steps**:
1. Navigate to https://practicesoftwaretesting.com
2. Find and click login button
3. Fill email: admin@practicesoftwaretesting.com
4. Fill password: welcome01
5. Click submit button
6. Verify page navigates to dashboard/home
7. Look for admin-specific elements (if any)
8. Take final screenshot showing logged-in state

**Verification**:
- [ ] Page navigates after login
- [ ] Logout button appears
- [ ] User menu shows admin name
- [ ] No error messages displayed

---

### Test Scenario: Customer Login
**Goal**: Verify regular customer can log in

**Steps**:
1. Navigate to site
2. Click login
3. Fill customer@practicesoftwaretesting.com / welcome01
4. Submit
5. Verify successful login
6. Navigate to Products page
7. Verify customer can browse products
8. Screenshot showing logged-in state

**Verification**:
- [ ] Login succeeds
- [ ] Can access Products page
- [ ] No admin-only features visible
- [ ] Logout available

---

### Test Scenario: Invalid Login
**Goal**: Verify error handling for wrong credentials

**Steps**:
1. Navigate to login page
2. Enter invalid@test.com / wrongpassword
3. Click submit
4. Take screenshot
5. Look for error message

**Verification**:
- [ ] Error message appears
- [ ] User stays on login page
- [ ] No accidental login occurs

---

### Test Scenario: Logout
**Goal**: Verify logout functionality

**Prerequisites**: User is logged in (run after login test)

**Steps**:
1. Find logout button/link
2. Click it
3. Wait for page redirect
4. Verify redirected to home/login page
5. Try accessing protected page
6. Verify redirected to login

**Verification**:
- [ ] Logout button exists
- [ ] Logout succeeds
- [ ] Session ends
- [ ] Redirected to login

---

### Test Scenario: Multi-Account Sequential Login
**Goal**: Test all 4 accounts can login

**Test Data**:
1. admin@practicesoftwaretesting.com / welcome01
2. customer@practicesoftwaretesting.com / welcome01
3. customer2@practicesoftwaretesting.com / welcome01
4. customer3@practicesoftwaretesting.com / pass123

**Steps** (repeat for each account):
1. Navigate to login
2. Fill credentials
3. Submit
4. Verify success
5. Take screenshot
6. Logout
7. Screenshot showing logged-out state

**Verification**:
- [ ] All 4 accounts login successfully
- [ ] Each shows proper logged-in state
- [ ] Logout works for all

---

## Product Browsing Scenarios

### Test Scenario: View All Products
**Goal**: Verify product list loads and displays correctly

**Steps**:
1. Navigate to home page
2. Click "Products" menu or link
3. Wait for products to load
4. Take screenshot
5. Find all product cards
6. Verify products display:
   - [ ] Product name
   - [ ] Product image
   - [ ] Product price
   - [ ] Add to cart button

**Verification**:
- [ ] Products page loads
- [ ] Multiple products visible
- [ ] Each product has name, image, price
- [ ] Products are clickable

---

### Test Scenario: Browse Product Details
**Goal**: Test clicking product navigates to detail page

**Steps**:
1. Navigate to products page
2. Find first product card
3. Click on product
4. Wait for detail page
5. Verify detail page shows:
   - [ ] Large product image
   - [ ] Product name
   - [ ] Full description
   - [ ] Price
   - [ ] Stock status
   - [ ] Add to cart button
6. Take screenshot

**Verification**:
- [ ] Detail page loads
- [ ] All product info visible
- [ ] Add to cart button enabled

---

### Test Scenario: Product Search
**Goal**: Test search functionality

**Steps**:
1. Navigate to products page
2. Find search input
3. Type "pliers" or similar
4. Press Enter or click search button
5. Wait for results to update
6. Take screenshot
7. Verify results contain searched term

**Verification**:
- [ ] Search input found
- [ ] Results update after search
- [ ] Results match search term
- [ ] Irrelevant products filtered out

---

### Test Scenario: Filter by Category
**Goal**: Test category filtering

**Steps**:
1. Go to products page
2. Find category filter/sidebar
3. Click a category (e.g., "Pliers")
4. Wait for products to update
5. Take screenshot
6. Find products displayed
7. Verify all belong to selected category

**Verification**:
- [ ] Category filter available
- [ ] Products update when filter applied
- [ ] Displayed products match category
- [ ] Count changes appropriately

---

### Test Scenario: Filter by Price Range
**Goal**: Test price range filtering

**Steps**:
1. Go to products page
2. Find price range filter
3. Set min price: 50
4. Set max price: 200
5. Apply filter (or auto-applies)
6. Wait for results
7. Take screenshot
8. Verify all prices within range

**Verification**:
- [ ] Price filter inputs found
- [ ] Filter applies
- [ ] All displayed products in price range
- [ ] Out-of-range products hidden

---

### Test Scenario: Filter by Multiple Criteria
**Goal**: Test combining multiple filters

**Steps**:
1. Go to products page
2. Select category: "Pliers"
3. Select brand: "Stanley" (if available)
4. Set price: 50-200
5. Take screenshot
6. Verify results match ALL criteria

**Verification**:
- [ ] Multiple filters can be set simultaneously
- [ ] Results respect all filters
- [ ] Product count decreases with more filters
- [ ] Filters can be removed individually

---

## Shopping Cart Scenarios

### Test Scenario: Add Product to Cart
**Goal**: Verify add to cart functionality

**Prerequisites**: Must be logged in

**Steps**:
1. Login with customer account
2. Navigate to products
3. Click on a product
4. Click "Add to cart" button
5. Wait for cart update
6. Take screenshot
7. Verify cart count increases

**Verification**:
- [ ] Add to cart button found
- [ ] Click succeeds (no errors)
- [ ] Cart indicator updates
- [ ] Product added (verify in cart)

---

### Test Scenario: View Cart Contents
**Goal**: Verify cart displays added items

**Prerequisites**: Product added to cart

**Steps**:
1. Click cart icon/link
2. Wait for cart page to load
3. Take screenshot
4. Verify cart contains:
   - [ ] Product name
   - [ ] Product price
   - [ ] Quantity
   - [ ] Subtotal

**Verification**:
- [ ] Cart page loads
- [ ] Added product visible
- [ ] All product info displayed
- [ ] Prices calculated correctly

---

### Test Scenario: Update Cart Quantity
**Goal**: Test changing item quantity

**Prerequisites**: Item in cart

**Steps**:
1. Go to cart page
2. Find quantity input for product
3. Change quantity from 1 to 3
4. Wait for update
5. Take screenshot
6. Verify subtotal updated

**Verification**:
- [ ] Quantity input found
- [ ] Quantity updates
- [ ] Subtotal recalculates
- [ ] Total updated correctly

---

### Test Scenario: Remove Item from Cart
**Goal**: Test removing items from cart

**Prerequisites**: Items in cart

**Steps**:
1. Go to cart page
2. Take screenshot (before removal)
3. Find remove button for first item
4. Click remove
5. Wait for update
6. Take screenshot (after removal)
7. Verify item no longer in cart

**Verification**:
- [ ] Remove button found
- [ ] Item removed after click
- [ ] Cart count decreases
- [ ] Totals updated
- [ ] Empty cart state if all removed

---

### Test Scenario: Empty Cart Behavior
**Goal**: Test empty cart state

**Steps**:
1. Remove all items from cart
2. Take screenshot
3. Look for "Empty cart" message
4. Verify continue shopping option
5. Click continue shopping
6. Verify navigates to products

**Verification**:
- [ ] Empty cart message appears
- [ ] No products listed
- [ ] Continue shopping option visible
- [ ] Navigation works

---

### Test Scenario: Add Multiple Products
**Goal**: Test cart with multiple different products

**Prerequisites**: Must be logged in

**Steps**:
1. Go to products
2. Add first product to cart
3. Go back to products
4. Add second product
5. Navigate to cart
6. Take screenshot
7. Verify both products in cart

**Verification**:
- [ ] Both products visible in cart
- [ ] Correct quantities
- [ ] Correct individual prices
- [ ] Subtotal = sum of products
- [ ] Remove works per item

---

## Checkout Scenarios

### Test Scenario: Proceed to Checkout
**Goal**: Test checkout initiation

**Prerequisites**: Items in cart

**Steps**:
1. Go to cart page
2. Find "Proceed to checkout" button
3. Click it
4. Wait for checkout page
5. Take screenshot
6. Verify on checkout page

**Verification**:
- [ ] Checkout button found
- [ ] Click succeeds
- [ ] Navigates to checkout
- [ ] Checkout form visible

---

### Test Scenario: Checkout Form Fields
**Goal**: Verify all required checkout fields

**Prerequisites**: On checkout page

**Steps**:
1. Take screenshot of checkout form
2. Identify all form fields:
   - [ ] First name
   - [ ] Last name
   - [ ] Address
   - [ ] City
   - [ ] State/Province
   - [ ] Zip code
   - [ ] Email
   - [ ] Phone
   - [ ] Billing address
   - [ ] Shipping method
   - [ ] Payment method

**Verification**:
- [ ] All expected fields present
- [ ] Fields are required/optional as expected
- [ ] Form is accessible
- [ ] Input types correct (email, tel, etc)

---

### Test Scenario: Complete Checkout
**Goal**: Complete full order process (if test environment allows)

**Prerequisites**: 
- Logged in as customer
- Items in cart
- Test payment info available

**Steps**:
1. Navigate to cart
2. Proceed to checkout
3. Fill shipping address:
   - Name: Test User
   - Address: 123 Test St
   - City: Test City
   - State: CA
   - Zip: 12345
   - Email: customer@practicesoftwaretesting.com
4. Select shipping method
5. Fill payment info (test card if available)
6. Click place order
7. Wait for confirmation
8. Take screenshot

**Verification**:
- [ ] Form fills successfully
- [ ] Validation passes
- [ ] Order processes
- [ ] Confirmation page shown
- [ ] Order number displayed

---

## Error & Edge Cases

### Test Scenario: Handle Page Errors
**Goal**: Test behavior when page errors occur

**Steps**:
1. Navigate to page
2. Check console for errors
3. Reload page if errors
4. Verify page still functional
5. Take screenshot

**Verification**:
- [ ] Page loads despite errors
- [ ] Functionality unaffected
- [ ] User can continue testing

---

### Test Scenario: Handle Missing Elements
**Goal**: Test graceful degradation

**Steps**:
1. Navigate to products
2. Try to click non-existent filter
3. Verify error handling
4. Try alternate flow
5. Verify can still browse

**Verification**:
- [ ] No crashes on missing elements
- [ ] Alternative methods work
- [ ] Error messages are clear

---

### Test Scenario: Network Error Handling
**Goal**: Test behavior with slow/failed requests

**Steps**:
1. Use DevTools to throttle network
2. Navigate and interact with site
3. Take screenshots
4. Note any error messages
5. Verify site remains usable

**Verification**:
- [ ] Loading indicators appear
- [ ] User feedback provided
- [ ] No silent failures
- [ ] Can retry or continue

---

## Responsive & Accessibility

### Test Scenario: Mobile Responsiveness
**Goal**: Test site on mobile viewport

**Steps**:
1. Resize browser to mobile: 375x667
2. Navigate to products
3. Take screenshot
4. Verify:
   - [ ] Menu accessible (hamburger)
   - [ ] Products displayed
   - [ ] Search works
   - [ ] Add to cart works
5. Navigate to cart
6. Take screenshot
7. Verify checkout flow works

**Verification**:
- [ ] Layout responsive
- [ ] Touch targets adequate
- [ ] No horizontal scroll
- [ ] All functions accessible

---

### Test Scenario: Tablet Responsiveness
**Goal**: Test on tablet viewport

**Steps**:
1. Resize to tablet: 768x1024
2. Run product browsing flow
3. Run shopping flow
4. Take screenshots at key points
5. Verify usability

**Verification**:
- [ ] Layout optimized for tablet
- [ ] Touch-friendly
- [ ] All content accessible
- [ ] Good use of space

---

### Test Scenario: Dark Mode (if supported)
**Goal**: Test dark mode styling

**Steps**:
1. Emulate dark mode
2. Navigate through site
3. Take screenshots
4. Verify:
   - [ ] Text readable
   - [ ] Contrast sufficient
   - [ ] Images display
   - [ ] Interactive elements visible

**Verification**:
- [ ] Dark mode renders correctly
- [ ] All text readable
- [ ] No missing images/elements
- [ ] Color contrast adequate

---

## How to Run These Scenarios

### Option 1: Individual Interactive Testing
```
In Claude Code, ask:
"Using Playwright MCP tools, run this test scenario: [Scenario Name]"
```

### Option 2: Automated Loop Testing
```
/loop 1h "Run: Test Scenario: Admin Login using Playwright MCP tools and report results"
```

### Option 3: Batch Testing
```
"Run these scenarios in sequence using Playwright MCP tools:
1. Test Scenario: Admin Login
2. Test Scenario: View All Products
3. Test Scenario: Add Product to Cart
4. Test Scenario: View Cart Contents

Take screenshots after each scenario and summarize results."
```

### Option 4: Scripted via .claude/hooks
Set up automated testing schedule via Claude Code settings.

---

## Success Criteria

Each test scenario passes when:
- ✅ All verification checkboxes checked
- ✅ No unexpected errors occurred
- ✅ Screenshots show expected state
- ✅ Site remains functional after test
- ✅ User can complete intended flow

---

## Reporting Results

When a scenario completes, report:

```
**Test Scenario**: [Name]
**Status**: ✅ PASS / ❌ FAIL
**Duration**: X seconds
**Screenshots**: [Attached]
**Issues Found**: [None / List of issues]
**Next Steps**: [Retry / Next scenario / Debug]
```

---

## Troubleshooting Scenarios

If a scenario fails:

1. **Take a screenshot** to see current state
2. **Check console** for JavaScript errors
3. **Review the flow** - did page navigation succeed?
4. **Try again** - transient failures happen
5. **Ask Claude** - "Debug this failure, show me what's happening"

---

## Performance Baseline

Expected performance (on good connection):
- Login: < 3 seconds
- Load products: < 2 seconds
- Search: < 2 seconds
- Add to cart: < 1 second
- Load cart: < 2 seconds
- Complete checkout: < 5 seconds
