# AI Integration Guide

## Claude API Integration with Playwright MCP

This guide shows how to add Claude-powered reasoning to Playwright agents.

---

## Setup

### 1. Get API Key
```bash
# Get your key from: https://console.anthropic.com/
# Store it in .env file:
echo "ANTHROPIC_API_KEY=sk-ant-your-key-here" > .env
```

### 2. Install Dependencies
```bash
cd 01-playwright-mcp-agent
pip install -r requirements.txt
playwright install chromium
```

### 3. Test Integration
```bash
# Run LLM demo (requires API key)
python run_llm_agents.py
```

---

## Architecture

### Deterministic Agent (Original)
```
Agent thinks hardcoded:
  state == "start" → navigate
  state == "navigating" → fill email
  state == "filling_email" → fill password
  ...
  
Fixed flow, predictable, no reasoning
```

### LLM-Driven Agent (New)
```
Agent asks Claude:
  "I need to log in. What should I do?"
  
Claude responds:
  "First navigate to the login page"
  
Agent executes: navigate(url=...)
Agent observes result
Agent asks Claude again with new observation
```

---

## Files Added

### 1. `agents/llm_agent.py`
**Base class for LLM-driven agents**

```python
class LLMAgent(BaseAgent):
    async def think(self, observation):
        # Send to Claude
        response = claude.message(prompt)
        # Get decision back
        return parse_claude_response(response)
```

**Key Features:**
- Conversation history (multi-turn)
- Tool descriptions (Claude knows available tools)
- Decision parsing (extracts tool calls from Claude's response)

### 2. `agents/llm_login_agent.py` 
**Concrete implementation: LLM-driven login**

```python
class LLMLoginAgent(LLMAgent):
    async def login(self, account_key, accounts):
        self.goal = f"Log in as {account_key}"
        return await self.run_until_done(self.goal)
```

### 3. `run_llm_agents.py`
**Demo comparing both approaches**

```bash
python run_llm_agents.py
# Shows:
# - Deterministic agent (fast, predictable)
# - LLM-driven agent (adaptive, reasoned)
# - Comparison table
```

---

## How It Works

### Step-by-Step: LLM-Driven Login

**Step 1: User Calls Agent**
```python
agent = LLMLoginAgent(mcp_client, api_key)
result = await agent.login("customer1", accounts)
```

**Step 2: Agent Asks Claude**
```
"Goal: Log in as customer1 (customer@example.com)

Available Tools:
- navigate(url)
- fill(selector, text)
- click(selector)
- extract_text(selector)
- wait_for(selector, state)

What is your next action?"
```

**Step 3: Claude Reasons**
```
Claude's Response:
"First, I need to navigate to the login page at 
https://practicesoftwaretesting.com

navigate(url=https://practicesoftwaretesting.com)"
```

**Step 4: Agent Executes**
```python
result = await agent.call_tool("navigate", {
    "url": "https://practicesoftwaretesting.com"
})
```

**Step 5: Agent Observes**
```
Result: {
    "success": true,
    "url": "https://practicesoftwaretesting.com",
    "title": "Practice Software Testing"
}
```

**Step 6: Loop Repeats**
Agent shows Claude the result and asks what's next...

---

## Key Differences

| Aspect | Deterministic | LLM-Driven |
|--------|---------------|-----------|
| **Logic** | Hardcoded state machine | Claude reasoning |
| **Adaptability** | Fixed flow | Adapts to observations |
| **New scenarios** | Code changes needed | Claude handles automatically |
| **Speed** | Very fast | Slower (API calls) |
| **Reliability** | Predictable | Can make mistakes |
| **Error recovery** | Fails at first error | Tries alternative approaches |

---

## Use Cases

### ✅ Use Deterministic When:
- Flow is always the same
- Speed is critical
- Network unavailable
- No API key available

### ✅ Use LLM-Driven When:
- Handling unknown/changing UIs
- Need adaptive behavior
- Error recovery is important
- Exploring new workflows

### ✅ Hybrid Approach:
Use deterministic for known flows, LLM for edge cases

```python
try:
    # Fast deterministic path
    result = await det_agent.login()
except:
    # Fallback to LLM
    result = await llm_agent.login()
```

---

## Customizing Prompts

The prompt Claude sees can be customized in `llm_agent.py`:

### Current Prompt
```python
def _build_prompt(self, observation):
    return f"""You are an autonomous browser automation agent...
    Available Tools: {tools_desc}
    Current Goal: {self.goal}
    Last Result: {observation}
    What is your next action?
    """
```

### Custom Prompt Example
```python
def _build_prompt(self, observation):
    return f"""You are a web testing expert automating QA scenarios.
    Goal: {self.goal}
    Tools: {tools_desc}
    
    Rules:
    1. Always check for errors first
    2. Use explicit waits
    3. Extract validation messages
    
    Last observation: {observation}
    What's your next step?
    """
```

---

## Advanced: Multi-Agent Orchestration

Coordinate multiple agents for complex workflows:

```python
# Sequential agents
login_agent = LLMLoginAgent(mcp_client, api_key)
product_agent = LLMProductAgent(mcp_client, api_key)
checkout_agent = LLMCheckoutAgent(mcp_client, api_key)

# Run in sequence
await login_agent.login("customer1", accounts)
await product_agent.browse_products()
await checkout_agent.checkout(checkout_data)
```

---

## Troubleshooting

### "ANTHROPIC_API_KEY not set"
```bash
# Option 1: Set in .env
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# Option 2: Set in environment
export ANTHROPIC_API_KEY=sk-ant-...

# Option 3: Pass directly
agent = LLMLoginAgent(mcp_client, api_key="sk-ant-...")
```

### "Rate limit exceeded"
Claude has rate limits. Wait before retrying:
```python
import time
time.sleep(60)  # Wait 1 minute
result = await agent.login()
```

### Claude makes wrong decisions
Improve the prompt with more context:
```python
# Include error messages
prompt += f"Error encountered: {last_error}\n"

# Include page state
prompt += f"Page elements visible: {elements}\n"

# Be more specific
prompt += "Focus on: email field, password field, submit button\n"
```

---

## Cost Considerations

Each Claude API call costs money (varies by model):
- **claude-3-5-sonnet**: ~$3 per million input tokens
- **Typical agent call**: 2-5 tokens (negligible cost)
- **Full login flow**: 3-5 API calls = ~$0.00001

Deterministic (no API calls) = Free
LLM-Driven (5 calls per flow) = Negligible cost

---

## Extending the System

### Add a New Tool
1. Implement in `browser_tools.py`
2. Add to `schemas.py` 
3. Route in `server.py`
4. Update `_get_available_tools_desc()` in `llm_agent.py`

Example:
```python
# In browser_tools.py
async def hover(self, selector: str):
    await self.page.hover(selector)
    return {"success": True}

# In _get_available_tools_desc()
tools = [
    ...,
    "hover(selector=...) - Hover over an element"
]
```

### Add a New Agent
```python
class LLMCustomAgent(LLMAgent):
    def __init__(self, mcp_client, api_key):
        super().__init__(mcp_client, "CustomAgent", api_key)
        self.goal = "Do something custom"
    
    async def run_task(self):
        return await self.run_until_done(self.goal)
```

---

## Monitoring & Debugging

### View Claude's Reasoning
```python
agent = LLMLoginAgent(mcp_client, api_key)
result = await agent.login("customer1", accounts)

# Print conversation history
for i, msg in enumerate(agent.conversation_history):
    role = msg["role"].upper()
    content = msg["content"][:100]
    print(f"{i}. [{role}] {content}...")
```

### Log Tool Calls
```python
# All tool calls are logged in call_history
for call in agent.call_history:
    print(f"Tool: {call['tool']}")
    print(f"Args: {call['arguments']}")
    print(f"Result: {call['result']}")
```

---

## Next Steps

1. **Get API Key** — https://console.anthropic.com/
2. **Set Environment** — `export ANTHROPIC_API_KEY=sk-ant-...`
3. **Run Demo** — `python run_llm_agents.py`
4. **Customize** — Edit prompts in `llm_agent.py`
5. **Build** — Add new agents for your workflows

---

## Resources

- [Anthropic API Docs](https://docs.anthropic.com/)
- [Claude Models](https://docs.anthropic.com/en/docs/models/overview)
- [Prompt Engineering Guide](https://docs.anthropic.com/en/docs/build-a-prototype/guide-to-prompting)

---

**Status:** ✅ Claude API integration complete  
**Created:** 2026-10-06  
**Ready:** Yes - test with `python run_llm_agents.py`
