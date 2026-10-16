# Playwright Learning Guide: CLI vs MCP

Complete guide to learning Playwright testing with both CLI and MCP approaches.

## Quick Start

### For CLI Learning (Traditional Approach)
```bash
cd 16-playwright-cli-learning
npm install
npm test
npm run test:headed  # See tests run in browser
npm run test:report  # View test results
```

### For MCP Learning (LLM-Driven Approach)
```bash
cd 17-playwright-mcp-learning
# Ensure Playwright MCP is enabled: /mcp
# In Claude Code, ask: "Test the login flow using Playwright MCP tools"
```

---

## What to Learn in Each Project

### Project 16: Playwright CLI Learning

Learn how to write traditional Playwright tests in TypeScript.

#### Core Skills
- [ ] **Test Structure**
  - Test suites with `test.describe()`
  - Individual tests with `test()`
  - Fixtures and hooks
  - Test parameterization

- [ ] **Locator Strategies**
  - XPath selectors: `//button[@type="submit"]`
  - CSS selectors: `.btn-primary`
  - Text matching: `button:has-text("Login")`
  - Accessible selectors

- [ ] **Interactions**
  - Clicking elements
  - Filling forms
  - Selecting dropdowns
  - Typing text
  - Pressing keys

- [ ] **Waiting & Navigation**
  - Waiting for selectors
  - Waiting for URL changes
  - Network waits
  - Custom wait conditions

- [ ] **Assertions**
  - Element visibility
  - Text content
  - Element count
  - URL matching

- [ ] **Configuration**
  - Browser selection
  - Retry strategies
  - Screenshots on failure
  - Trace collection
  - Parallel execution

#### Time Commitment
- **Learn basics**: 2-3 hours
- **Write test suite**: 4-6 hours
- **Debug & refine**: 2-4 hours
- **Total**: ~8-13 hours for proficiency

#### Best For
- Critical, high-confidence tests
- Deterministic flows
- CI/CD pipelines
- Complex test logic

---

### Project 17: Playwright MCP Learning

Learn how to use Claude to control browsers via MCP tools.

#### Core Skills
- [ ] **MCP Concept Understanding**
  - What is MCP (Model Context Protocol)
  - How Claude interacts with tools
  - Tool invocation flow
  - Screenshot + analysis loop

- [ ] **Tool Categories**
  - Navigation tools
  - Interaction tools
  - Inspection tools
  - Waiting tools

- [ ] **Practical Usage**
  - Describing elements naturally
  - Handling tool responses
  - Chaining operations
  - Error recovery

- [ ] **Test Scenarios**
  - Authentication flows
  - Product browsing
  - Shopping cart
  - Checkout process

- [ ] **Debugging**
  - Analyzing screenshots
  - Tool failure recovery
  - Network inspection
  - Console logs

#### Time Commitment
- **Learn concept**: 1-2 hours
- **Try first scenario**: 0.5-1 hour
- **Master 5+ scenarios**: 3-5 hours
- **Total**: ~5-8 hours for proficiency

#### Best For
- Exploratory testing
- Quick validation
- Adaptive/dynamic content
- Rapid test authoring
- Real-time debugging

---

## Learning Path

### Week 1: Foundations
- **Day 1-2**: CLI - Basic structure and selectors
  - Read `16-playwright-cli-learning/README.md`
  - Run `npm run test:auth` to see an existing test
  - Modify a test and run it

- **Day 3-4**: CLI - Interactions and waiting
  - Add new test to `products.spec.ts`
  - Use multiple selectors
  - Add waits for dynamic content
  - Run in headed mode to watch

- **Day 5**: MCP - Concept and tools
  - Read `17-playwright-mcp-learning/README.md`
  - Read `MCP-TOOLS-REFERENCE.md`
  - Enable Playwright MCP in Claude Code

### Week 2: Hands-On
- **Day 1-2**: CLI - Complete test suite
  - Write tests for new features
  - Use fixtures for setup
  - Implement error scenarios
  - Get all tests passing

- **Day 3-4**: MCP - Run live test scenarios
  - Follow Test Scenarios from doc
  - Start with simple auth test
  - Progress to product browsing
  - Try shopping cart flow

- **Day 5**: Comparison
  - Run same test in both approaches
  - Note differences in code/experience
  - Identify when to use each

### Week 3: Mastery
- **Day 1-2**: CLI Advanced
  - Multi-browser testing
  - Performance testing
  - Network mocking (if applicable)
  - Custom fixtures

- **Day 3-4**: MCP Advanced
  - Set up recurring tests with `/loop`
  - Handle complex scenarios
  - Debug failures interactively
  - Combine with API testing

- **Day 5**: Integration
  - Use CLI for baseline tests
  - Use MCP for exploratory testing
  - Create a hybrid test suite
  - Document your approach

---

## Comparison Matrix

| Aspect | CLI | MCP |
|--------|-----|-----|
| **Language** | TypeScript | Natural English |
| **Setup Time** | 30 min | 5 min |
| **Learning Curve** | Medium | Gentle |
| **Execution Speed** | Fast | Moderate (LLM calls) |
| **Debugging** | Traces + logs | Screenshots + chat |
| **Maintainability** | Fixed code | Adaptive |
| **Scalability** | High (CI/CD) | Medium (tool calls) |
| **Flexibility** | Full control | LLM-constrained |
| **Error Recovery** | Explicit handling | LLM decides |
| **Cost** | Low (local) | API tokens |

---

## Test Site: Practice Software Testing

### URL
https://practicesoftwaretesting.com

### Key Features to Test
1. **Authentication**
   - Login/logout
   - Session management
   - Multi-account testing

2. **Products**
   - Browse product list
   - Filter by category/price/brand
   - Search functionality
   - View product details

3. **Shopping**
   - Add to cart
   - Update quantities
   - Remove items
   - View totals

4. **Checkout**
   - Shipping address
   - Payment info
   - Order confirmation

### Test Accounts
```
Admin:    admin@practicesoftwaretesting.com / welcome01
User 1:   customer@practicesoftwaretesting.com / welcome01
User 2:   customer2@practicesoftwaretesting.com / welcome01
User 3:   customer3@practicesoftwaretesting.com / pass123
```

---

## Resources

### Documentation
- [Playwright Official Docs](https://playwright.dev)
- [Playwright Best Practices](https://playwright.dev/docs/best-practices)
- [MCP Protocol](https://modelcontextprotocol.io)

### Learning Materials in This Guide
- `16-playwright-cli-learning/README.md` - CLI learning guide
- `16-playwright-cli-learning/tests/` - Example tests
- `17-playwright-mcp-learning/README.md` - MCP learning guide
- `17-playwright-mcp-learning/MCP-TOOLS-REFERENCE.md` - Complete tool reference
- `17-playwright-mcp-learning/TEST-SCENARIOS.md` - Test scenarios

### Practice
- [Test Reference Repo](https://github.com/testsmith-io/practice-software-testing)
- Practice Software Testing Site: https://practicesoftwaretesting.com

---

## Common Questions

### Q: Do I need both approaches?
**A**: No, but knowing both gives you flexibility:
- Use CLI for critical production tests
- Use MCP for exploratory/regression testing

### Q: Which should I learn first?
**A**: Start with MCP (lower barrier, faster results), then CLI (deeper understanding).

### Q: Can I use MCP for production tests?
**A**: Not recommended due to API costs and LLM consistency. Use for development/exploratory testing.

### Q: How do I debug test failures?
**CLI**: Check traces, screenshots, error messages in test output
**MCP**: Ask Claude "What happened?" - it analyzes screenshots and explains

### Q: Can I run tests on schedule?
**CLI**: Yes, via npm scripts in CI/CD
**MCP**: Yes, via `/loop` command in Claude Code

### Q: How do I handle flaky tests?
**CLI**: Add waits, improve selectors, increase retries
**MCP**: Ask Claude to be more thorough, add extra verification steps

---

## Next Steps

### Immediate (Today)
1. Open `16-playwright-cli-learning`
2. Run `npm test` to see existing tests
3. Read a test file to understand structure
4. Make small modification and re-run

### Short-term (This Week)
1. Write your own test for one flow
2. Enable Playwright MCP in Claude Code
3. Ask Claude to run a test scenario
4. Compare the two experiences

### Medium-term (This Month)
1. Build complete test suite (CLI)
2. Create comprehensive test scenarios (MCP)
3. Analyze when each is most useful
4. Integrate into your workflow

---

## File Structure

```
/projects/
├── 16-playwright-cli-learning/
│   ├── playwright.config.ts      # Configuration
│   ├── package.json              # Dependencies & scripts
│   ├── README.md                 # Learning guide
│   └── tests/
│       ├── auth.spec.ts          # Authentication tests
│       ├── products.spec.ts      # Product browsing tests
│       └── cart.spec.ts          # Shopping cart tests
│
├── 17-playwright-mcp-learning/
│   ├── package.json              # Project metadata
│   ├── README.md                 # MCP learning guide
│   ├── MCP-TOOLS-REFERENCE.md    # Tool reference
│   ├── TEST-SCENARIOS.md         # Test scenarios to run
│   └── (no code - uses MCP via Claude Code)
│
└── PLAYWRIGHT-LEARNING-GUIDE.md  # This file
```

---

## Success Metrics

### CLI Learning Success
- [ ] Can write a basic test
- [ ] Understand test hooks and fixtures
- [ ] Can add assertions effectively
- [ ] Can run tests and read reports
- [ ] Can debug a failing test
- [ ] Can create test scenarios
- [ ] Can configure playwright.config.ts

### MCP Learning Success
- [ ] Understand MCP concept
- [ ] Can ask Claude to test a scenario
- [ ] Can read and interpret screenshots
- [ ] Can handle tool failures
- [ ] Can create complex flows via conversation
- [ ] Can set up automated test loops
- [ ] Can debug with Claude's help

### Dual Proficiency
- [ ] Know when to use each approach
- [ ] Can create hybrid test suite
- [ ] Can explain trade-offs
- [ ] Can teach others both methods
- [ ] Can integrate into workflow effectively

---

## Tips for Success

### For CLI Learning
1. **Start Simple**: Login test is easiest
2. **Selectors Matter**: Spend time getting them right
3. **Use Headed Mode**: `npm run test:headed` to watch
4. **Read Traces**: HTML report shows exactly what happened
5. **Incrementally Build**: Add tests one by one

### For MCP Learning
1. **Be Descriptive**: "Add to cart button" vs "button"
2. **Take Screenshots**: After each major step
3. **Iterate Quickly**: Ask Claude to fix issues
4. **Start Small**: Test login before complex flows
5. **Learn from Failures**: Claude explains what went wrong

### General Tips
1. **Keep Both Projects**: Reference each approach
2. **Document Your Learning**: Add notes to README
3. **Experiment Freely**: No risk in this learning environment
4. **Share Knowledge**: Explain both to colleagues
5. **Practice Regularly**: Run tests weekly to maintain skills

---

## Troubleshooting

### CLI Issues
See `16-playwright-cli-learning/README.md` Troubleshooting section

### MCP Issues
See `17-playwright-mcp-learning/README.md` Troubleshooting section

### Still Stuck?
1. Take a screenshot (CLI: enable trace, MCP: ask Claude)
2. Check console for errors (CLI: `npm run test:debug`, MCP: ask for console logs)
3. Simplify the test (remove extra steps)
4. Review relevant docs from resources above
5. Try alternative approach (if CLI failing, try MCP to verify flow works)

---

## Conclusion

By learning both approaches, you'll have:
- ✅ Deep knowledge of Playwright internals (CLI)
- ✅ Understanding of AI-driven testing (MCP)
- ✅ Ability to choose the right tool for each situation
- ✅ Skills to teach others effective testing strategies
- ✅ Confidence in automation across different scenarios

Happy learning! 🚀
