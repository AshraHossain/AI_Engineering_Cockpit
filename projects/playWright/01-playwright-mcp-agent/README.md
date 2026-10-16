# Playwright MCP Agent

Autonomous browser automation using Playwright + Model Context Protocol (MCP).

## Architecture

```
Agent (Python class)
  ↓
MCP Client (call_tool)
  ↓
MCP Server (routes to tool)
  ↓
Browser Tools (Playwright)
  ↓
Playwright (Chromium)
  ↓
Target Website
```

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Download browser
playwright install chromium
```

## Project Structure

```
config/
  ├── selectors.json          # Page element selectors
  ├── credentials.json        # Test account credentials
  └── ...

mcp-server/
  ├── server.py               # MCP server (tool router)
  ├── tools/
  │   ├── browser_tools.py    # Playwright wrappers
  │   └── schemas.py          # Tool schemas

agents/
  ├── base_agent.py           # Base agent class (think → act → observe)
  ├── login_agent.py          # Login automation
  ├── product_agent.py        # Product browsing
  └── checkout_agent.py       # Checkout process

run_agents.py                  # Demo script
```

## Running Agents

### Demo 1: Login Only
```bash
python run_agents.py
```

### Demo 2: Full Checkout Flow
Edit `run_agents.py` and uncomment the full flow section, then run:
```bash
python run_agents.py
```

## How It Works

### Agent Loop (The Core Pattern)

Each agent implements this loop:

```python
while not done:
    # Step 1: Think about goal and current observation
    decision = await agent.think(observation)
    
    # Step 2: Parse decision into tool call
    tool_name, args = parse(decision)
    
    # Step 3: Call tool via MCP
    result = await mcp_client.call_tool(tool_name, args)
    
    # Step 4: Observe result
    observation = result
```

### Example: LoginAgent

```python
class LoginAgent(BaseAgent):
    async def think(self, observation):
        # State machine decides next action
        if self.state == "start":
            return "navigate(url=https://practicesoftwaretesting.com)"
        elif self.state == "navigating":
            return "fill(selector=input#email, text=customer@example.com)"
        # ... etc
```

## Available Tools

All tools are defined in `mcp-server/tools/schemas.py`:

| Tool | Purpose |
|------|---------|
| `navigate(url)` | Go to URL |
| `click(selector)` | Click element |
| `fill(selector, text)` | Fill form field |
| `extract_text(selector)` | Get text content |
| `wait_for(selector, state)` | Wait for element |
| `screenshot()` | Take screenshot |

## Extending the System

### Add a New Agent

1. Create `agents/my_agent.py`:
```python
from base_agent import BaseAgent

class MyAgent(BaseAgent):
    async def think(self, observation):
        # Your logic here
        return "tool_name(arg=value)"
```

2. Use in `run_agents.py`:
```python
my_agent = MyAgent(mcp_client)
result = await my_agent.run_until_done("my goal")
```

### Add a New Tool

1. Implement in `mcp-server/tools/browser_tools.py`:
```python
async def my_tool(self, arg1: str):
    # Use self.page to interact with browser
    return {"success": True, ...}
```

2. Add schema to `mcp-server/tools/schemas.py`:
```python
"my_tool": {
    "name": "my_tool",
    "description": "...",
    "inputSchema": {...}
}
```

3. Route in `mcp-server/server.py`:
```python
elif name == "my_tool":
    return await self.browser_tools.my_tool(**args)
```

## Key Concepts

### State Machine (Current Implementation)
Agents use hardcoded state machines for deterministic behavior.

### Future: LLM-Driven Agents
Replace `think()` to call Claude API:
```python
async def think(self, observation):
    response = await claude.message(f"""
        Goal: {self.goal}
        Current page: {observation}
        Available tools: {self.tools}
        What should you do next?
    """)
    return parse(response.content)
```

### Self-Healing
Agents can recover from failures by catching tool errors and retrying with different selectors or approaches.

## Files Created

| File | Purpose |
|------|---------|
| `config/selectors.json` | Element selectors (single source of truth) |
| `config/credentials.json` | Test account credentials |
| `mcp-server/server.py` | MCP server routing tool calls |
| `mcp-server/tools/browser_tools.py` | Playwright wrappers |
| `mcp-server/tools/schemas.py` | Tool input/output contracts |
| `agents/base_agent.py` | Base agent with think/act/observe loop |
| `agents/login_agent.py` | Autonomous login |
| `agents/product_agent.py` | Product browsing |
| `agents/checkout_agent.py` | Checkout flow |
| `run_agents.py` | Demo runner |

## Troubleshooting

### "Browser not found"
```bash
playwright install chromium
```

### Selector not found
Update selectors in `config/selectors.json` to match current website.

### Agent stuck in loop
Increase `max_iterations` in agent constructor or check selector accuracy.

## Next Steps

1. Test with live website
2. Add more agents (admin dashboard, search, filters)
3. Replace state machines with LLM-driven reasoning
4. Add error recovery and self-healing
5. Integrate with Claude API for autonomous reasoning

---

**Architecture:** Model Context Protocol (MCP) + Playwright  
**Goal:** Demonstrate agent-driven automation patterns  
**AI Forward:** Foundation for LLM-driven browser automation  
