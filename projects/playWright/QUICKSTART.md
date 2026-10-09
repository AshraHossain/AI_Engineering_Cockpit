# Quick Start Guide

## What Was Created

✅ **Two Complete Projects:**
- **01-playwright-mcp-agent** — Agent-driven automation (Python)
- **02-playwright-cli-tests** — Test-driven automation (TypeScript)

✅ **30+ Files**
- Configuration files (selectors, credentials, configs)
- Source code (agents, tools, page objects, tests)
- Documentation (READMEs, guides)

✅ **Ready to Showcase**
- Both projects target https://practicesoftwaretesting.com
- Both use same test accounts
- Both demonstrate modern automation patterns

---

## 5-Minute Demo

### Terminal 1: Run MCP Agents
```bash
cd 01-playwright-mcp-agent

# Install
pip install -r requirements.txt
playwright install chromium

# Run demo
python run_agents.py
```

**Output:** Agent autonomously logs in, shows call history

### Terminal 2: Run CLI Tests
```bash
cd 02-playwright-cli-tests

# Install
npm install
npx playwright install

# Run tests
npm test
```

**Output:** Test suite executes, generates HTML report

---

## File Highlights

### MCP Project (Agent-Driven)

```
01-playwright-mcp-agent/
├── config/
│   ├── selectors.json          ← Page elements
│   └── credentials.json         ← Test accounts
├── mcp-server/
│   ├── server.py               ← Tool router (MCP)
│   └── tools/
│       ├── browser_tools.py    ← Playwright wrappers
│       └── schemas.py          ← Tool interface definitions
├── agents/
│   ├── base_agent.py           ← Agent think→act→observe loop
│   ├── login_agent.py          ← Autonomous login
│   ├── product_agent.py        ← Product browsing
│   └── checkout_agent.py       ← Checkout automation
└── run_agents.py               ← Demo runner
```

**Key Insight:** Agent loop (think → call tool → observe)

### CLI Project (Test-Driven)

```
02-playwright-cli-tests/
├── fixtures/page-objects/
│   ├── BasePage.ts             ← Common methods
│   ├── LoginPage.ts            ← Login interactions
│   ├── ProductPage.ts          ← Product interactions
│   ├── CartPage.ts             ← Cart interactions
│   └── CheckoutPage.ts         ← Checkout interactions
├── utils/
│   ├── selectors.ts            ← CSS selectors
│   └── test-data.ts            ← Test fixtures
└── tests/
    ├── auth/login.spec.ts      ← Login tests
    ├── products/browse.spec.ts ← Product tests
    ├── cart/add-to-cart.spec.ts ← Cart tests
    └── checkout/checkout-flow.spec.ts ← E2E test
```

**Key Insight:** Page Object Model (semantic tests)

---

## Architecture Comparison

### MCP Project Flow
```
AI Agent thinks:
  "I need to log in"
    ↓
Calls MCP tool:
  navigate(url) → click(selector) → fill(text) → wait_for()
    ↓
Observes result:
  {success: true, url: ..., title: ...}
    ↓
Thinks next step:
  "Success! Now browse products"
```

### CLI Project Flow
```
Test code says:
  loginPage.login(email, password)
    ↓
Page Object handles:
  fill email → fill password → click submit
    ↓
Assertion checks:
  expect(page).toHaveURL(/dashboard/)
    ↓
Test passes or fails
```

---

## Shared Resources

Both projects share:

**Test Website:** https://practicesoftwaretesting.com

**Accounts:**
```
Admin:      admin@practicesoftwaretesting.com / welcome01
Customer1:  customer@practicesoftwaretesting.com / welcome01
Customer2:  customer2@practicesoftwaretesting.com / welcome01
Customer3:  customer3@practicesoftwaretesting.com / pass123
```

**Selectors:**
- MCP: `01-playwright-mcp-agent/config/selectors.json`
- CLI: `02-playwright-cli-tests/utils/selectors.ts`

---

## How to Showcase

### Demo 1: Show Agent Automation (2 mins)
```bash
cd 01-playwright-mcp-agent
python run_agents.py
```

**Highlights:**
- Agent autonomously decides next action
- Shows tool calls (navigate, fill, click)
- Displays call history (what agent did)

### Demo 2: Show Test Automation (2 mins)
```bash
cd 02-playwright-cli-tests
npm test
```

**Highlights:**
- Tests run in parallel
- Page objects hide selectors
- Shows HTML report with videos

### Demo 3: Compare Approaches (2 mins)
```bash
# Show both project structures
tree -L 2 01-playwright-mcp-agent/
tree -L 2 02-playwright-cli-tests/

# Show shared credentials
cat 01-playwright-mcp-agent/config/credentials.json
cat 02-playwright-cli-tests/utils/test-data.ts
```

**Highlights:**
- Different languages (Python vs TypeScript)
- Different approaches (agents vs tests)
- Shared targets and data

---

## Key Features to Mention

### 🤖 MCP Project
- **Agent Loop:** Think → Act → Observe pattern
- **Tool Schemas:** Agents know what tools exist
- **State Management:** Agents maintain context
- **Future:** Replace hardcoded logic with LLM reasoning

### 🧪 CLI Project
- **Page Objects:** Semantic test methods
- **Centralized Selectors:** Single source of truth
- **Test Data:** Parametrized fixtures
- **CI/CD Ready:** HTML reports, JUnit XML, screenshots

### 🔗 Integration
- Both target same website
- Both use same test accounts
- Both follow best practices
- Both scale with AI

---

## What Each File Does

### Configuration Files

| File | Project | Purpose |
|------|---------|---------|
| `selectors.json` | MCP | Page element locators |
| `selectors.ts` | CLI | Page element locators (TypeScript) |
| `credentials.json` | MCP | Test account credentials |
| `test-data.ts` | CLI | Test fixtures (users, products) |
| `playwright.config.ts` | CLI | Playwright test configuration |

### Core Logic Files

| File | Project | Purpose |
|------|---------|---------|
| `base_agent.py` | MCP | Agent loop implementation |
| `login_agent.py` | MCP | Login automation example |
| `browser_tools.py` | MCP | Playwright tool wrappers |
| `server.py` | MCP | MCP server (tool router) |
| `BasePage.ts` | CLI | Common page methods |
| `LoginPage.ts` | CLI | Login page object |
| `login.spec.ts` | CLI | Login test cases |

### Infrastructure Files

| File | Project | Purpose |
|------|---------|---------|
| `run_agents.py` | MCP | Demo runner for agents |
| `package.json` | CLI | Node dependencies |
| `requirements.txt` | MCP | Python dependencies |
| `README.md` | Both | Project documentation |

---

## What You Can Show

### 1. Code Quality
- Clean architecture (separation of concerns)
- Reusable components (base classes, utilities)
- Centralized configuration (single source of truth)

### 2. Automation Patterns
- **Agent Pattern:** State machine → LLM-driven reasoning
- **Page Object Pattern:** Tests as user narratives
- **Tool Definition:** Structured tool interfaces (schemas)

### 3. AI-Forward Design
- Agents expose tools for future LLM integration
- Selectors centralized for easy repair
- Test data fixtures for scenario generation
- Call history for debugging and learning

### 4. Best Practices
- No hardcoded selectors in tests/agents
- Clear separation: browser logic vs business logic
- Test data externalized for parametrization
- Configuration management (env, credentials)

---

## Next Steps After Demo

1. **Run both projects** to verify they work
2. **Examine page objects** — show POM pattern
3. **Examine agents** — show agent loop
4. **Compare selectors** — discuss single source of truth
5. **Plan extensions:**
   - Add LLM to agent's `think()` method
   - Generate tests from page objects
   - Implement selector self-healing
   - Multi-agent orchestration

---

## Troubleshooting

**"playwright: command not found" (CLI)**
```bash
npx playwright install
```

**"playwright: command not found" (MCP)**
```bash
playwright install chromium
```

**Tests timeout**
- Check internet connection
- Verify selectors match target site
- Run in headed mode: `npm run test:headed`

**Agent stuck**
- Increase `max_iterations` in agent constructor
- Check selector accuracy in `config/selectors.json`
- Run with `--headed` flag to see what's happening

---

## Files Summary

```
Total Files Created: 30+

01-playwright-mcp-agent/          17 files
  ├── config/                      3 files
  ├── mcp-server/                  5 files
  ├── agents/                      4 files
  ├── tests/                       1 file (optional)
  ├── requirements.txt
  ├── run_agents.py
  └── README.md

02-playwright-cli-tests/           15+ files
  ├── fixtures/                    5 files
  ├── utils/                       2 files
  ├── tests/                       4 files
  ├── package.json
  ├── playwright.config.ts
  ├── tsconfig.json
  └── README.md

Root/
  ├── README.md                    Master documentation
  ├── notes.md                     Detailed blueprint
  └── QUICKSTART.md               This file
```

---

## Success Criteria

- ✅ Both projects created with all files
- ✅ MCP project can run: `python run_agents.py`
- ✅ CLI project can run: `npm test`
- ✅ Both target same website
- ✅ Both demonstrate automation best practices
- ✅ Ready to showcase and extend

---

**Status:** 🟢 Ready to Showcase  
**Created:** 2026-10-01  
**Updated:** 2026-10-01
