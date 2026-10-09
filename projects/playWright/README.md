# Playwright Automation Learning Cockpit

**Two complete projects demonstrating modern web automation with Playwright.**

> **Goal:** Master both agent-driven automation (MCP) and test-driven automation (CLI) with forward-looking AI implementation patterns.

---

## Quick Start

### Project 1: Playwright MCP (Agent-Driven)
Autonomous agents call browser tools via Model Context Protocol.

```bash
cd 01-playwright-mcp-agent
pip install -r requirements.txt
playwright install chromium
python run_agents.py
```

### Project 2: Playwright CLI (Test-Driven)
Traditional test automation using Playwright Test.

```bash
cd 02-playwright-cli-tests
npm install
npx playwright install
npm test
```

---

## Two-Project Architecture

### Project 1: MCP Agent-Driven
**What it teaches:** How AI agents orchestrate browser automation

```
Agent (think → act → observe loop)
  ↓
MCP Server (tool router)
  ↓
Browser Tools (Playwright wrappers)
  ↓
Playwright (browser automation)
```

**Use when:**
- Building autonomous agents
- Learning agent reasoning patterns
- Implementing multi-step workflows
- Experimenting with LLM integration

**Key Files:**
- `agents/base_agent.py` — Agent loop pattern
- `agents/login_agent.py` — Concrete example
- `mcp-server/server.py` — Tool router
- `config/credentials.json` — Shared credentials

---

### Project 2: CLI Test-Driven
**What it teaches:** How to write maintainable test automation

```
Test (semantic narrative)
  ↓
Page Objects (element interactions)
  ↓
Selectors (CSS, stable)
  ↓
Playwright (browser automation)
```

**Use when:**
- Building regression test suites
- Practicing Page Object Model
- Learning Playwright syntax
- Setting up CI/CD pipelines

**Key Files:**
- `fixtures/page-objects/BasePage.ts` — Base class
- `fixtures/page-objects/*Page.ts` — Page objects
- `utils/selectors.ts` — Selector registry
- `tests/**/*.spec.ts` — Test cases

---

## Comparison Table

| Aspect | MCP (Project 1) | CLI (Project 2) |
|--------|-----------------|-----------------|
| **Language** | Python | TypeScript |
| **Driver** | Agent (state machine) | Test runner |
| **Execution** | Sequential (agent decides) | Parallel (test runner decides) |
| **Error Recovery** | Agent can retry/adapt | Hard assertions (pass/fail) |
| **Ideal For** | Autonomous workflows | Regression testing |
| **Maintenance** | Agents + Tools | Tests + Page Objects |
| **Scaling** | Multi-agent coordination | Parallel test execution |

---

## Shared Configuration

Both projects use the same target site and credentials:

**Target:** https://practicesoftwaretesting.com

**Test Accounts:**
```json
{
  "admin": "admin@practicesoftwaretesting.com / welcome01",
  "customer1": "customer@practicesoftwaretesting.com / welcome01",
  "customer2": "customer2@practicesoftwaretesting.com / welcome01",
  "customer3": "customer3@practicesoftwaretesting.com / pass123"
}
```

**Selectors:**
- MCP: `01-playwright-mcp-agent/config/selectors.json`
- CLI: `02-playwright-cli-tests/utils/selectors.ts`

---

## File Organization

```
playWright/
├── notes.md                                   # Comprehensive blueprint
├── README.md                                  # This file

├── 01-playwright-mcp-agent/                   # PROJECT 1: MCP Agent-Driven
│   ├── config/
│   │   ├── selectors.json                    # Page elements
│   │   └── credentials.json                  # Test accounts
│   ├── mcp-server/
│   │   ├── server.py                         # MCP router
│   │   └── tools/
│   │       ├── browser_tools.py              # Playwright wrappers
│   │       └── schemas.py                    # Tool schemas
│   ├── agents/
│   │   ├── base_agent.py                     # Agent loop
│   │   ├── login_agent.py                    # Login automation
│   │   ├── product_agent.py                  # Product browsing
│   │   └── checkout_agent.py                 # Checkout flow
│   ├── run_agents.py                         # Demo runner
│   ├── requirements.txt                      # Dependencies
│   └── README.md                             # Project docs

└── 02-playwright-cli-tests/                   # PROJECT 2: CLI Test-Driven
    ├── utils/
    │   ├── selectors.ts                      # CSS selectors
    │   └── test-data.ts                      # Test fixtures
    ├── fixtures/page-objects/
    │   ├── BasePage.ts                       # Base class
    │   ├── LoginPage.ts
    │   ├── ProductPage.ts
    │   ├── CartPage.ts
    │   └── CheckoutPage.ts
    ├── tests/
    │   ├── auth/
    │   │   └── login.spec.ts                 # Login tests
    │   ├── products/
    │   │   └── browse.spec.ts                # Product tests
    │   ├── cart/
    │   │   └── add-to-cart.spec.ts           # Cart tests
    │   └── checkout/
    │       └── checkout-flow.spec.ts         # E2E checkout
    ├── playwright.config.ts                  # Playwright config
    ├── tsconfig.json                         # TypeScript config
    ├── package.json                          # Dependencies
    └── README.md                             # Project docs
```

---

## Learning Path

### Step 1: Understand CLI First (Simpler)
1. Read `02-playwright-cli-tests/README.md`
2. Examine page objects (`fixtures/page-objects/`)
3. Run tests: `cd 02-playwright-cli-tests && npm test`
4. View report: `npm run test:report`

**Key Takeaway:** Test structure, Page Object Model, Playwright API

### Step 2: Understand MCP Project (More Complex)
1. Read `01-playwright-mcp-agent/README.md`
2. Examine agent loop (`agents/base_agent.py`)
3. Review tool schemas (`mcp-server/tools/schemas.py`)
4. Run agents: `cd 01-playwright-mcp-agent && python run_agents.py`

**Key Takeaway:** Agent reasoning, tool calling, autonomous workflows

### Step 3: Compare & Integrate
1. Notice both projects share selectors (single source of truth)
2. CLI tests validate MCP agent workflows
3. MCP tools could be exposed to LLM agents

**Key Takeaway:** Separation of concerns, reusability, composition

### Step 4: Extend for AI (Future)
1. Add LLM reasoning to MCP agents
2. Generate tests from existing page objects
3. Implement self-healing selectors
4. Multi-agent orchestration

---

## Showcase Checklist

**To demonstrate both projects:**

- [ ] **MCP Project:**
  - [ ] Show `config/selectors.json` (element registry)
  - [ ] Show `agents/base_agent.py` (agent loop)
  - [ ] Run `python run_agents.py` (login demo)
  - [ ] Show call history (tool calls)

- [ ] **CLI Project:**
  - [ ] Show `fixtures/page-objects/` (Page Object Model)
  - [ ] Show `utils/selectors.ts` (selector management)
  - [ ] Run `npm test` (test suite)
  - [ ] Show `npm run test:report` (HTML report)

- [ ] **Integration Points:**
  - [ ] Both share `selectors` (different formats)
  - [ ] Both share `credentials`
  - [ ] Both target same website
  - [ ] Selectors easily synced between projects

---

## Key Concepts

### 1. Page Elements Registry
Single source of truth for selectors:
- **MCP:** `config/selectors.json`
- **CLI:** `utils/selectors.ts`

**Why:** Easy to update, audit, and repair selectors

### 2. Agent Loop (MCP Only)
```python
for iteration in range(max_iterations):
    decision = await agent.think(observation)  # Reasoning
    tool_name, args = parse(decision)          # Decision → Tool
    result = await call_tool(tool_name, args)  # Execution
    observation = result                       # Observation
```

**Why:** Foundation for LLM-driven agents

### 3. Page Object Model (CLI Only)
```typescript
const loginPage = new LoginPage(page);
await loginPage.login(email, password);
await loginPage.assertLoginSuccess();
```

**Why:** Tests read like user narratives, not technical scripts

### 4. Test Data Centralization (CLI)
```typescript
const user = TestUsers.customer1;
const checkout = CheckoutData.validCheckout();
```

**Why:** Easy to parametrize, generate, and manage test variations

---

## AI-Forward Implementation

### Future: LLM-Driven Agents
Replace MCP agent's hardcoded state machine with Claude API:

```python
async def think(self, observation):
    response = await claude.message(f"""
        Goal: {self.goal}
        Current page: {observation}
        Available tools: {self.tools}
        Next action?
    """)
    return response.content
```

### Future: Test Generation
Analyze page objects and generate test cases:

```python
# Given: ProductPage page object
# AI generates: 5 new test cases covering edge cases
# Outputs: tests/products/generated_*.spec.ts
```

### Future: Selector Repair
When selector breaks, use vision + reasoning:

```python
# Page object calls click(selector)
# Selector not found
# Vision model analyzes screenshot
# Suggests new selector
# Updates selectors.json automatically
```

---

## Project Dependencies

### MCP Project
```
playwright==1.48.0       # Browser automation
pytest==7.4.3           # Testing framework
python-dotenv==1.0.0    # Environment variables
```

### CLI Project
```
@playwright/test==1.48.0 # Playwright Test runner
typescript==5.0.0        # TypeScript compiler
```

---

## Common Tasks

### Run Specific Test
```bash
# CLI: Login tests only
cd 02-playwright-cli-tests
npm run test:auth

# MCP: Login agent demo
cd 01-playwright-mcp-agent
python run_agents.py
```

### Update Selectors
```bash
# Both projects:
# Find your selector in config/selectors.json (MCP)
# Find your selector in utils/selectors.ts (CLI)
# Update both to stay in sync
```

### Debug Failures
```bash
# CLI: Interactive debugging
npm run test:debug

# MCP: Check call history
# Examine call_history in agent.call_history
```

### View Test Report
```bash
# CLI: HTML report
npm run test:report

# MCP: Console output and call logs
```

---

## Troubleshooting

### "Browser not found" (MCP)
```bash
cd 01-playwright-mcp-agent
playwright install chromium
```

### "Timeout waiting for selector" (Both)
- Check selectors are accurate
- Verify target website hasn't changed
- Run in headed mode to see actual page

### "npm: command not found" (CLI)
```bash
# Install Node.js from nodejs.org
# Or use nvm: https://github.com/nvm-sh/nvm
```

### Parallel execution issues (CLI)
Set `workers: 1` in `playwright.config.ts` for serial execution

---

## Resources

### Playwright Documentation
- [Playwright Official Docs](https://playwright.dev)
- [Playwright Test API](https://playwright.dev/docs/api/class-test)
- [Selectors Guide](https://playwright.dev/docs/selectors)

### Model Context Protocol
- [MCP Specification](https://modelcontextprotocol.io)
- [Building MCP Servers](https://modelcontextprotocol.io/docs/concepts/client)

### Best Practices
- [Page Object Model Pattern](https://playwright.dev/docs/pom)
- [Test Organization](https://playwright.dev/docs/test-structure)
- [Debugging Guide](https://playwright.dev/docs/debug)

### This Project
- [Detailed Blueprint](./notes.md) — Complete file creation guide
- [MCP Project README](./01-playwright-mcp-agent/README.md) — Agent architecture
- [CLI Project README](./02-playwright-cli-tests/README.md) — Test patterns

---

## File Summary

**Total Files Created:** 30+

### MCP Project (17 files)
- Configuration: 3 files (selectors, credentials, env)
- Server: 5 files (server, tools, schemas, middleware)
- Agents: 4 files (base, login, product, checkout)
- Infrastructure: 2 files (requirements, runner)
- Documentation: 1 file (README)

### CLI Project (15+ files)
- Configuration: 3 files (playwright.config, tsconfig, package.json)
- Utilities: 2 files (selectors, test-data)
- Page Objects: 5 files (base, login, product, cart, checkout)
- Tests: 4 files (login, browse, cart, checkout)
- Documentation: 1 file (README)

---

## Next Steps

1. **Run both projects** to see them working
2. **Compare the approaches** — agent-driven vs test-driven
3. **Identify reusable patterns** — selectors, test data, page structure
4. **Plan AI extensions** — LLM agents, test generation, selector repair
5. **Integrate with CI/CD** — GitHub Actions, GitLab CI, Jenkins

---

## Questions?

- See `notes.md` for detailed file-by-file breakdown
- Check `01-playwright-mcp-agent/README.md` for agent questions
- Check `02-playwright-cli-tests/README.md` for test questions

---

**Created:** 2026-10-01  
**Architecture:** MCP + Playwright CLI (dual approach)  
**Purpose:** Learning + Showcasing + Foundation for AI automation  
**Status:** Ready to run and demonstrate
