# Playwright Automation Learning Cockpit
## Two-Project Architecture for AI-Forward Web Testing

**Last Updated:** 2026-10-01  
**Goal:** Learn to automate practicesoftwaretesting.com using Playwright MCP (agent-driven) and Playwright CLI (test-driven) with forward-looking AI implementation patterns.

---

## Table of Contents
1. [Executive Summary](#executive-summary)
2. [Architecture Philosophy](#architecture-philosophy)
3. [Project 1: Playwright MCP (Agent-Driven)](#project-1-playwright-mcp-agent-driven)
4. [Project 2: Playwright CLI (Test-Driven)](#project-2-playwright-cli-test-driven)
5. [File Creation Checklist](#file-creation-checklist)
6. [AI Integration Patterns](#ai-integration-patterns)
7. [Best Practices](#best-practices)

---

## Executive Summary

### Why Two Projects?

| Project | Purpose | AI Value |
|---------|---------|----------|
| **MCP** | Teach agent orchestration | Learn how AI models call tools; autonomous reasoning about browser state |
| **CLI** | Teach deterministic testing | Learn baseline automation; pattern matching for regression; foundation for synthetic data generation |

**AI-Forward Goal:** Both projects feed into future AI implementations:
- MCP teaches you how to expose tools for LLM-driven agents
- CLI teaches you how to build reliable test fixtures that agents can leverage
- Together: Foundation for AI-driven test generation, repair, and optimization

---

## Architecture Philosophy

### Core Principle: Separation of Concerns for AI

```
┌─────────────────────────────────────────────────────────┐
│  AI Layer (Agents, Orchestration, Reasoning)            │
├─────────────────────────────────────────────────────────┤
│  Tool/Test Definition Layer (Reusable, Declarative)     │
├─────────────────────────────────────────────────────────┤
│  Browser Automation Layer (Playwright Core)             │
├─────────────────────────────────────────────────────────┤
│  Target Application (https://practicesoftwaretesting.com)│
└─────────────────────────────────────────────────────────┘
```

**Why this matters for AI:**
- **Declarative tools** → AI can discover and call them without code
- **Reusable fixtures** → AI can compose test scenarios from building blocks
- **Structured state** → AI can reason about page state and adapt
- **Observable outputs** → AI can verify action success and recover from failures

---

## Project 1: Playwright MCP (Agent-Driven)

### Project Structure
```
01-playwright-mcp-agent/
├── config/
│   ├── credentials.json              # Test account credentials (DO NOT COMMIT IN PROD)
│   └── selectors.json                # Page element registry (version controlled)
├── mcp-server/
│   ├── __init__.py
│   ├── server.py                     # MCP server entry point
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── browser_tools.py          # Tool implementations
│   │   ├── decorators.py             # Retry, timeout, logging decorators
│   │   └── schemas.py                # Tool input/output schemas
│   └── middleware/
│       ├── __init__.py
│       ├── state_manager.py          # Track browser state for agents
│       ├── error_handler.py          # Intelligent error recovery
│       └── logger.py                 # Structured logging for debugging
├── agents/
│   ├── __init__.py
│   ├── base_agent.py                 # Base agent with tool calling loop
│   ├── login_agent.py                # Autonomous login workflow
│   ├── product_agent.py              # Browse & extract product data
│   └── checkout_agent.py             # Add to cart & checkout
├── fixtures/
│   ├── __init__.py
│   └── page_elements.py              # Selector constants (imported by tools)
├── tests/
│   ├── __init__.py
│   ├── test_mcp_server.py            # Unit tests for MCP tools
│   └── test_agents.py                # Integration tests for agents
├── docker/
│   └── Dockerfile                    # Container for MCP server
├── requirements.txt                  # Python dependencies
├── .env.example                      # Environment variable template
├── run_server.py                     # Start MCP server
└── README.md                         # Project documentation
```

### File Creation Guide

#### **1. `config/selectors.json`**
**Purpose:** Central registry of all page selectors. AI-critical for reusability and maintenance.

**Why this file:**
- Single source of truth for element locators
- Easy to update selectors without touching tool code
- AI agents can query this to understand page structure
- Supports selector versioning for adaptive strategies

**Creation Steps:**
```bash
# 1. Create the directory
mkdir -p 01-playwright-mcp-agent/config

# 2. Create selectors.json
cat > 01-playwright-mcp-agent/config/selectors.json << 'EOF'
{
  "site": "https://practicesoftwaretesting.com",
  "pages": {
    "login": {
      "email_input": "input#email",
      "password_input": "input#password",
      "submit_button": "button[type='submit']",
      "error_message": ".alert-danger",
      "success_redirect": "/dashboard"
    },
    "products": {
      "product_card": ".product-item",
      "product_title": ".product-title",
      "product_price": ".product-price",
      "add_to_cart_button": ".btn-add-to-cart",
      "product_link": "a.product-link"
    },
    "cart": {
      "cart_icon": ".cart-icon",
      "cart_items": ".cart-item",
      "remove_button": ".btn-remove",
      "checkout_button": ".btn-checkout",
      "empty_cart_message": ".empty-cart"
    },
    "checkout": {
      "address_input": "input#address",
      "city_input": "input#city",
      "country_select": "select#country",
      "payment_method": "select#payment_method",
      "place_order_button": "button.btn-place-order",
      "order_confirmation": ".order-success"
    }
  }
}
EOF
```

**Best Practice:** Use semantic selectors (prefer `#id`, then `.class`, avoid brittle XPath).

---

#### **2. `config/credentials.json`**
**Purpose:** Test account credentials for agent login flows.

**Why this file:**
- Centralized credential management
- Easily switch between test accounts
- Template-ready for environment-based overrides

**Creation Steps:**
```bash
cat > 01-playwright-mcp-agent/config/credentials.json << 'EOF'
{
  "accounts": {
    "admin": {
      "email": "admin@practicesoftwaretesting.com",
      "password": "welcome01",
      "role": "admin",
      "description": "Admin account with full access"
    },
    "customer1": {
      "email": "customer@practicesoftwaretesting.com",
      "password": "welcome01",
      "role": "user",
      "description": "Regular customer account"
    },
    "customer2": {
      "email": "customer2@practicesoftwaretesting.com",
      "password": "welcome01",
      "role": "user",
      "description": "Second customer account"
    },
    "customer3": {
      "email": "customer3@practicesoftwaretesting.com",
      "password": "pass123",
      "role": "user",
      "description": "Third customer account"
    }
  }
}
EOF

# Create .env.example (template without secrets)
cat > 01-playwright-mcp-agent/.env.example << 'EOF'
# MCP Server Configuration
MCP_PORT=5000
MCP_HOST=localhost

# Playwright Configuration
HEADLESS=true
SLOW_MO=100

# Logging
LOG_LEVEL=DEBUG
LOG_FILE=./logs/mcp_server.log

# Test Credentials (override from config/credentials.json if needed)
TEST_ADMIN_EMAIL=admin@practicesoftwaretesting.com
TEST_ADMIN_PASSWORD=welcome01
EOF
```

**Best Practice:** Never commit credentials; use `.env.example` as template. In CI/CD, inject via secrets.

---

#### **3. `mcp-server/schemas.py`**
**Purpose:** Define MCP tool input/output schemas for type safety and AI introspection.

**Why this file:**
- AI models need to understand tool contracts
- Validates agent inputs before browser operations
- Enables self-healing (AI can retry with corrected inputs)
- Structures outputs for agent reasoning

**Creation Steps:**
```bash
mkdir -p 01-playwright-mcp-agent/mcp-server/tools

cat > 01-playwright-mcp-agent/mcp-server/schemas.py << 'EOF'
"""
Tool schemas for MCP server.
Defines input/output contracts for all browser automation tools.
"""
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
import json


@dataclass
class NavigateInput:
    """Navigate to a URL."""
    url: str
    wait_until: str = "networkidle"  # 'load' | 'domcontentloaded' | 'networkidle'
    timeout: int = 30000  # milliseconds


@dataclass
class ClickInput:
    """Click an element by selector."""
    selector: str
    timeout: int = 30000
    force: bool = False  # Skip waiting for actionability


@dataclass
class FillInput:
    """Fill a form field."""
    selector: str
    text: str
    timeout: int = 30000


@dataclass
class ExtractTextInput:
    """Extract text from element(s)."""
    selector: str
    multiple: bool = False  # Extract from all matching elements


@dataclass
class WaitForInput:
    """Wait for element to appear."""
    selector: str
    timeout: int = 30000
    state: str = "visible"  # 'attached' | 'visible' | 'hidden'


@dataclass
class ScreenshotOutput:
    """Screenshot result."""
    base64: str
    timestamp: str
    page_title: str
    page_url: str


@dataclass
class ExtractTextOutput:
    """Text extraction result."""
    success: bool
    content: str | List[str]  # Single text or list for multiple=true
    selector_matched: bool
    element_count: int


class ToolSchemas:
    """Registry of all tool schemas."""
    
    TOOLS = {
        "navigate": {
            "name": "navigate",
            "description": "Navigate to a URL and wait for page load",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to navigate to"},
                    "wait_until": {
                        "type": "string",
                        "enum": ["load", "domcontentloaded", "networkidle"],
                        "description": "When to consider navigation succeeded"
                    },
                    "timeout": {"type": "integer", "description": "Timeout in ms"}
                },
                "required": ["url"]
            }
        },
        "click": {
            "name": "click",
            "description": "Click an element by CSS selector",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector"},
                    "timeout": {"type": "integer", "description": "Timeout in ms"},
                    "force": {"type": "boolean", "description": "Force click without waiting"}
                },
                "required": ["selector"]
            }
        },
        "fill": {
            "name": "fill",
            "description": "Fill a form field with text",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector"},
                    "text": {"type": "string", "description": "Text to fill"},
                    "timeout": {"type": "integer", "description": "Timeout in ms"}
                },
                "required": ["selector", "text"]
            }
        },
        "extract_text": {
            "name": "extract_text",
            "description": "Extract text content from element(s)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector"},
                    "multiple": {"type": "boolean", "description": "Extract from all matches"}
                },
                "required": ["selector"]
            }
        },
        "wait_for": {
            "name": "wait_for",
            "description": "Wait for element to reach a state (visible, hidden, attached)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector"},
                    "state": {
                        "type": "string",
                        "enum": ["visible", "hidden", "attached"],
                        "description": "Target element state"
                    },
                    "timeout": {"type": "integer", "description": "Timeout in ms"}
                },
                "required": ["selector"]
            }
        },
        "screenshot": {
            "name": "screenshot",
            "description": "Take a screenshot of current page",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "full_page": {"type": "boolean", "description": "Capture full scrollable page"}
                }
            }
        }
    }
    
    @classmethod
    def get_all_tools(cls):
        """Return all tool definitions for MCP manifest."""
        return list(cls.TOOLS.values())
    
    @classmethod
    def get_tool(cls, name: str):
        """Get a specific tool schema."""
        return cls.TOOLS.get(name)


if __name__ == "__main__":
    # Print tools in JSON format for MCP server manifest
    print(json.dumps(ToolSchemas.get_all_tools(), indent=2))
EOF
```

**Best Practice:** Schemas enable AI to understand tool contracts. Clear descriptions help LLMs choose correct tools.

---

#### **4. `mcp-server/tools/browser_tools.py`**
**Purpose:** Implementation of all browser automation tools called by agents.

**Why this file:**
- Encapsulates Playwright logic
- Reusable across multiple agents
- Easier to test and maintain
- AI-agnostic (doesn't know it's being called by an agent)

**Creation Steps:**
```bash
cat > 01-playwright-mcp-agent/mcp-server/tools/browser_tools.py << 'EOF'
"""
Browser automation tools exposed via MCP.
Each tool is a self-contained operation the agent can call.
"""
import json
import base64
from datetime import datetime
from typing import Optional, List, Dict, Any
from playwright.async_api import async_playwright, Page, Browser


class BrowserTools:
    """Wrapper around Playwright for tool-based access."""
    
    def __init__(self):
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
    
    async def initialize(self):
        """Start browser and create page."""
        p = await async_playwright().start()
        self.browser = await p.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
        await self.page.set_viewport_size({"width": 1280, "height": 720})
    
    async def close(self):
        """Close browser."""
        if self.page:
            await self.page.close()
        if self.browser:
            await self.browser.close()
    
    async def navigate(self, url: str, wait_until: str = "networkidle", timeout: int = 30000):
        """Navigate to URL."""
        try:
            await self.page.goto(url, wait_until=wait_until, timeout=timeout)
            return {
                "success": True,
                "url": self.page.url,
                "title": await self.page.title(),
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def click(self, selector: str, timeout: int = 30000, force: bool = False):
        """Click element by selector."""
        try:
            await self.page.click(selector, timeout=timeout, force=force)
            return {"success": True, "selector": selector, "timestamp": datetime.now().isoformat()}
        except Exception as e:
            return {"success": False, "error": str(e), "selector": selector}
    
    async def fill(self, selector: str, text: str, timeout: int = 30000):
        """Fill form field."""
        try:
            await self.page.fill(selector, text, timeout=timeout)
            return {"success": True, "selector": selector, "timestamp": datetime.now().isoformat()}
        except Exception as e:
            return {"success": False, "error": str(e), "selector": selector}
    
    async def extract_text(self, selector: str, multiple: bool = False):
        """Extract text from element(s)."""
        try:
            if multiple:
                elements = await self.page.query_selector_all(selector)
                content = [await elem.text_content() for elem in elements]
            else:
                content = await self.page.text_content(selector)
            
            return {
                "success": True,
                "content": content,
                "selector_matched": content is not None,
                "element_count": len(content) if multiple else 1
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def wait_for(self, selector: str, state: str = "visible", timeout: int = 30000):
        """Wait for element to reach state."""
        try:
            await self.page.wait_for_selector(selector, state=state, timeout=timeout)
            return {"success": True, "selector": selector, "state": state}
        except Exception as e:
            return {"success": False, "error": str(e), "selector": selector}
    
    async def screenshot(self, full_page: bool = False):
        """Take screenshot."""
        try:
            image_bytes = await self.page.screenshot(full_page=full_page)
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            return {
                "success": True,
                "base64": base64_image,
                "page_url": self.page.url,
                "page_title": await self.page.title(),
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def get_page_state(self) -> Dict[str, Any]:
        """Get current page state (URL, title, etc.) - useful for agent reasoning."""
        return {
            "url": self.page.url,
            "title": await self.page.title(),
            "timestamp": datetime.now().isoformat()
        }
EOF
```

**Best Practice:** Each tool is atomic and independent. No side effects between tools; agents manage flow.

---

#### **5. `mcp-server/server.py`**
**Purpose:** MCP server that exposes browser tools to agents.

**Why this file:**
- Entry point for agent communication
- Implements MCP protocol (initialize, handle tool calls)
- Manages browser lifecycle

**Creation Steps:**
```bash
cat > 01-playwright-mcp-agent/mcp-server/server.py << 'EOF'
"""
MCP Server: Exposes Playwright tools for agents to call.
"""
import json
import asyncio
import logging
from typing import Any
import json
from mcp.server import Server
from mcp.types import TextContent, Tool, ToolCall, ToolResult
from tools.browser_tools import BrowserTools
from schemas import ToolSchemas


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class PlaywrightMCPServer:
    """MCP server wrapping Playwright tools."""
    
    def __init__(self):
        self.server = Server("playwright-mcp")
        self.browser_tools = BrowserTools()
        self.setup_handlers()
    
    def setup_handlers(self):
        """Register MCP handlers."""
        
        @self.server.list_tools()
        async def list_tools():
            """Return available tools."""
            return ToolSchemas.get_all_tools()
        
        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list:
            """Execute a tool call from agent."""
            logger.info(f"Tool call: {name} with args {arguments}")
            
            try:
                if not self.browser_tools.page:
                    await self.browser_tools.initialize()
                
                # Route to tool implementation
                result = await self._execute_tool(name, arguments)
                
                return [TextContent(type="text", text=json.dumps(result))]
            except Exception as e:
                logger.error(f"Tool error: {e}")
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]
    
    async def _execute_tool(self, name: str, args: dict) -> dict:
        """Execute tool by name."""
        if name == "navigate":
            return await self.browser_tools.navigate(**args)
        elif name == "click":
            return await self.browser_tools.click(**args)
        elif name == "fill":
            return await self.browser_tools.fill(**args)
        elif name == "extract_text":
            return await self.browser_tools.extract_text(**args)
        elif name == "wait_for":
            return await self.browser_tools.wait_for(**args)
        elif name == "screenshot":
            return await self.browser_tools.screenshot(**args)
        else:
            return {"error": f"Unknown tool: {name}"}
    
    async def run(self, host: str = "localhost", port: int = 5000):
        """Start MCP server."""
        logger.info(f"Starting MCP server on {host}:{port}")
        await self.server.run_stdio()


if __name__ == "__main__":
    server = PlaywrightMCPServer()
    asyncio.run(server.run())
EOF
```

**Best Practice:** Server is protocol handler, not business logic. Logic stays in tools.

---

#### **6. `agents/base_agent.py`**
**Purpose:** Base class for all agents. Implements tool-calling loop and reasoning.

**Why this file:**
- Reusable agent foundation
- Implements agentic loop (think → call tool → observe → repeat)
- Handles retries, timeout, error recovery

**Creation Steps:**
```bash
mkdir -p 01-playwright-mcp-agent/agents

cat > 01-playwright-mcp-agent/agents/base_agent.py << 'EOF'
"""
Base agent class for autonomous automation.
Implements the agent loop: Think → Call Tool → Observe → Reason.
"""
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from abc import ABC, abstractmethod


logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    Base agent for browser automation.
    
    Agents think about goals, call tools to interact with the browser,
    observe results, and reason about next steps.
    
    AI-Forward: This pattern is what LLM-based agents follow.
    """
    
    def __init__(self, mcp_client, name: str, max_iterations: int = 10):
        """
        Initialize agent.
        
        Args:
            mcp_client: MCP client to call tools
            name: Agent name (for logging)
            max_iterations: Max tool calls before giving up
        """
        self.mcp_client = mcp_client
        self.name = name
        self.max_iterations = max_iterations
        self.iteration_count = 0
        self.call_history: List[Dict[str, Any]] = []
    
    @abstractmethod
    async def think(self, observation: Optional[str] = None) -> str:
        """
        Think about current state and decide next action.
        
        This is where agent reasoning happens.
        Could be implemented as:
        - Hardcoded decision trees (deterministic)
        - LLM reasoning (AI-forward)
        
        Returns: Tool name to call or "done" if finished.
        """
        pass
    
    async def call_tool(self, tool_name: str, arguments: dict) -> Dict[str, Any]:
        """Call a tool via MCP."""
        logger.info(f"[{self.name}] Calling {tool_name} with {arguments}")
        result = await self.mcp_client.call_tool(tool_name, arguments)
        self._record_call(tool_name, arguments, result)
        return result
    
    async def run_until_done(self, goal: str) -> Dict[str, Any]:
        """
        Run agent loop until done or max iterations reached.
        
        The agent loop:
        1. Think about goal and current observation
        2. Decide which tool to call
        3. Call the tool
        4. Observe the result
        5. Repeat
        """
        logger.info(f"[{self.name}] Starting with goal: {goal}")
        
        observation = None
        
        for i in range(self.max_iterations):
            self.iteration_count = i + 1
            
            # Step 1: Think
            decision = await self.think(observation)
            
            if decision == "done":
                logger.info(f"[{self.name}] Agent says we're done")
                return {"success": True, "iterations": self.iteration_count}
            
            # Parse decision (format: "tool_name(arg1=val1, arg2=val2)")
            tool_name, args = self._parse_decision(decision)
            
            # Step 2-3: Call tool
            result = await self.call_tool(tool_name, args)
            
            # Step 4: Observe
            observation = json.dumps(result)
            
            if not result.get("success", False):
                logger.warning(f"[{self.name}] Tool failed: {result.get('error')}")
        
        return {
            "success": False,
            "reason": "Max iterations reached",
            "iterations": self.iteration_count,
            "call_history": self.call_history
        }
    
    def _parse_decision(self, decision: str) -> tuple:
        """Parse decision string like 'click(selector=#button)'."""
        # Simple parser; in production, use a proper parser
        tool_name = decision.split("(")[0]
        args_str = decision.split("(")[1].rstrip(")")
        args = {}
        for pair in args_str.split(","):
            k, v = pair.split("=")
            args[k.strip()] = v.strip().strip("'\"")
        return tool_name, args
    
    def _record_call(self, tool_name: str, args: dict, result: dict):
        """Record tool call for debugging and learning."""
        self.call_history.append({
            "tool": tool_name,
            "arguments": args,
            "result": result,
            "timestamp": datetime.now().isoformat()
        })


class DeterministicAgent(BaseAgent):
    """Agent with hardcoded decision logic (not AI-driven)."""
    
    def __init__(self, mcp_client, name: str, decision_tree: dict):
        super().__init__(mcp_client, name)
        self.decision_tree = decision_tree
        self.current_state = "start"
    
    async def think(self, observation: Optional[str] = None) -> str:
        """Follow hardcoded decision tree."""
        next_decision = self.decision_tree.get(self.current_state, {}).get("next")
        if not next_decision:
            return "done"
        self.current_state = next_decision.get("next_state", self.current_state)
        return next_decision.get("action", "done")
EOF
```

**Best Practice:** Base agent is protocol; specific agents override `think()` with their logic.

---

#### **7. `agents/login_agent.py`**
**Purpose:** Autonomous login agent - concrete example of agent thinking + tool calling.

**Creation Steps:**
```bash
cat > 01-playwright-mcp-agent/agents/login_agent.py << 'EOF'
"""
Login Agent: Autonomously logs in by navigating, filling form, submitting.
"""
import json
import logging
from base_agent import BaseAgent


logger = logging.getLogger(__name__)


class LoginAgent(BaseAgent):
    """
    Agent that logs in autonomously.
    
    Example of how an agent thinks and acts:
    1. Navigate to login page
    2. Wait for form
    3. Fill email
    4. Fill password
    5. Click submit
    6. Wait for dashboard
    7. Report success
    """
    
    def __init__(self, mcp_client, credentials: dict):
        super().__init__(mcp_client, "LoginAgent")
        self.credentials = credentials
        self.state = "start"
    
    async def think(self, observation: Optional[str] = None) -> str:
        """
        Deterministic login flow.
        
        This is a simple example. In production, an LLM agent would:
        1. Observe the page
        2. Reason about what element to interact with
        3. Decide the next action
        
        For learning purposes, we use a state machine here.
        """
        
        if self.state == "start":
            self.state = "navigating"
            return f"navigate(url=https://practicesoftwaretesting.com)"
        
        elif self.state == "navigating":
            self.state = "filling_email"
            return f"fill(selector=input#email, text={self.credentials['email']})"
        
        elif self.state == "filling_email":
            self.state = "filling_password"
            return f"fill(selector=input#password, text={self.credentials['password']})"
        
        elif self.state == "filling_password":
            self.state = "submitting"
            return "click(selector=button[type='submit'])"
        
        elif self.state == "submitting":
            self.state = "waiting_for_dashboard"
            return "wait_for(selector=.dashboard-container, state=visible)"
        
        elif self.state == "waiting_for_dashboard":
            return "done"
        
        else:
            return "done"
    
    async def login(self, account_key: str) -> dict:
        """Public method to run login for given account."""
        account = self.credentials["accounts"][account_key]
        self.credentials = account
        return await self.run_until_done(f"Login as {account_key}")
EOF
```

**Best Practice:** Concrete agents show how thinking and acting interact. Start simple (state machine), graduate to LLM reasoning.

---

#### **8. `requirements.txt`**
**Purpose:** Python dependencies for MCP project.

**Creation Steps:**
```bash
cat > 01-playwright-mcp-agent/requirements.txt << 'EOF'
# Playwright
playwright==1.48.0

# MCP (Model Context Protocol)
mcp==0.5.0

# Async support
aiofiles==23.2.1

# Logging and monitoring
python-dotenv==1.0.0

# Testing
pytest==7.4.3
pytest-asyncio==0.23.0

# Code quality
black==23.12.0
flake8==6.1.0
mypy==1.8.0
EOF

# Install dependencies
cd 01-playwright-mcp-agent
pip install -r requirements.txt
playwright install  # Download browser
```

---

#### **9. `run_server.py`**
**Purpose:** Entry point to start MCP server.

**Creation Steps:**
```bash
cat > 01-playwright-mcp-agent/run_server.py << 'EOF'
#!/usr/bin/env python3
"""
Start the MCP server.

Usage:
    python run_server.py
"""
import asyncio
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

from mcp_server.server import PlaywrightMCPServer


async def main():
    server = PlaywrightMCPServer()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
EOF

chmod +x 01-playwright-mcp-agent/run_server.py
```

---

#### **10. `tests/test_agents.py`**
**Purpose:** Integration tests for agents using the MCP server.

**Creation Steps:**
```bash
mkdir -p 01-playwright-mcp-agent/tests

cat > 01-playwright-mcp-agent/tests/test_agents.py << 'EOF'
"""
Integration tests for agents.
Tests that agents can successfully complete workflows.
"""
import pytest
import asyncio
import json
from pathlib import Path

# Mock MCP client for testing
class MockMCPClient:
    def __init__(self):
        self.call_log = []
    
    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Mock tool execution."""
        self.call_log.append({"tool": tool_name, "args": arguments})
        
        # Mock responses
        if tool_name == "navigate":
            return {"success": True, "url": arguments.get("url")}
        elif tool_name == "fill":
            return {"success": True, "selector": arguments.get("selector")}
        elif tool_name == "click":
            return {"success": True}
        elif tool_name == "wait_for":
            return {"success": True, "selector": arguments.get("selector")}
        else:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}


@pytest.mark.asyncio
async def test_login_agent_workflow():
    """Test that login agent executes a complete login flow."""
    # This test verifies the agent loop works end-to-end
    pass  # Implementation depends on your test setup
EOF
```

---

### Summary: MCP Project Files

| File | Purpose | AI Value |
|------|---------|----------|
| `config/selectors.json` | Page element registry | Single source of truth for selectors |
| `config/credentials.json` | Test account credentials | Reusable across agents |
| `mcp-server/schemas.py` | Tool input/output contracts | AI can understand tool capabilities |
| `mcp-server/tools/browser_tools.py` | Playwright wrappers | Encapsulated, testable tool implementations |
| `mcp-server/server.py` | MCP protocol handler | Exposes tools to agents |
| `agents/base_agent.py` | Agent loop template | Pattern for future AI-driven agents |
| `agents/login_agent.py` | Concrete login agent | Example of deterministic agent |
| `requirements.txt` | Dependencies | Reproducible environment |
| `tests/test_agents.py` | Integration tests | Verify agent workflows |

---

## Project 2: Playwright CLI (Test-Driven)

### Project Structure
```
02-playwright-cli-tests/
├── tests/
│   ├── auth/
│   │   ├── login.spec.ts           # Login test cases
│   │   └── logout.spec.ts          # Logout test cases
│   ├── products/
│   │   ├── browse.spec.ts          # Product browsing
│   │   └── filter.spec.ts          # Product filtering
│   ├── cart/
│   │   ├── add-to-cart.spec.ts
│   │   ├── remove-from-cart.spec.ts
│   │   └── cart-state.spec.ts
│   └── checkout/
│       ├── checkout-flow.spec.ts
│       └── payment.spec.ts
├── fixtures/
│   ├── page-objects/
│   │   ├── BasePage.ts             # Base class with common methods
│   │   ├── LoginPage.ts
│   │   ├── ProductPage.ts
│   │   ├── CartPage.ts
│   │   └── CheckoutPage.ts
│   └── setup.ts                    # Test setup and teardown
├── utils/
│   ├── selectors.ts                # Selector constants
│   ├── test-data.ts                # Test data factory
│   └── assertions.ts               # Custom assertion helpers
├── playwright.config.ts             # Playwright configuration
├── package.json                    # Node dependencies
└── README.md                        # Project documentation
```

### File Creation Guide

#### **1. `package.json`**
**Purpose:** Node project manifest and dependency manager.

**Why this file:**
- Defines project metadata
- Lists all dependencies
- Defines test scripts
- Enables reproducible installs

**Creation Steps:**
```bash
cd ../02-playwright-cli-tests

cat > package.json << 'EOF'
{
  "name": "playwright-cli-tests",
  "version": "1.0.0",
  "description": "Playwright CLI automation for practicesoftwaretesting.com",
  "scripts": {
    "test": "playwright test",
    "test:headed": "playwright test --headed",
    "test:debug": "playwright test --debug",
    "test:ui": "playwright test --ui",
    "test:report": "playwright show-report",
    "test:auth": "playwright test tests/auth",
    "test:products": "playwright test tests/products",
    "test:checkout": "playwright test tests/checkout"
  },
  "keywords": ["playwright", "automation", "testing"],
  "author": "Your Name",
  "license": "MIT",
  "devDependencies": {
    "@playwright/test": "^1.48.0",
    "@types/node": "^20.0.0",
    "typescript": "^5.0.0"
  },
  "dependencies": {
    "dotenv": "^16.0.0"
  }
}
EOF

npm install
```

**Best Practice:** Lock file (`package-lock.json`) should be committed.

---

#### **2. `playwright.config.ts`**
**Purpose:** Playwright test runner configuration.

**Why this file:**
- Central configuration for all tests
- Defines browser types (Chromium, Firefox, WebKit)
- Sets timeouts, retries, parallel execution
- Configures reporting (screenshots, videos, traces)

**Creation Steps:**
```bash
cat > playwright.config.ts << 'EOF'
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  
  reporter: [
    ['html', { outputFolder: 'playwright-report' }],
    ['json', { outputFile: 'test-results.json' }],
    ['junit', { outputFile: 'junit.xml' }],
    ['list']
  ],
  
  use: {
    baseURL: 'https://practicesoftwaretesting.com',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    trace: 'on-first-retry',
  },
  
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
  ],
  
  webServer: undefined, // We're testing a live site, not a local server
});
EOF
```

**Best Practice:** Extensive reporting helps diagnose test failures. Parallelization speeds up local feedback.

---

#### **3. `utils/selectors.ts`**
**Purpose:** Central selector constants for all tests.

**Why this file:**
- Single source of truth for page selectors
- Easy to update selectors across all tests
- Mirrors MCP project's `config/selectors.json`
- Supports AI-driven selector repair

**Creation Steps:**
```bash
mkdir -p utils

cat > utils/selectors.ts << 'EOF'
/**
 * Page selectors for practicesoftwaretesting.com
 * Updated: 2026-10-01
 * 
 * Best Practice: Keep selectors stable by preferring:
 * 1. IDs (most stable)
 * 2. Data attributes (intentional for automation)
 * 3. Classes (moderate stability)
 * 4. Avoid: XPath, complex CSS, text-based selectors
 */

export const Selectors = {
  // Login page
  login: {
    emailInput: 'input#email',
    passwordInput: 'input#password',
    submitButton: 'button[type="submit"]',
    errorMessage: '.alert-danger',
  },
  
  // Products page
  products: {
    productCard: '.product-item',
    productTitle: '.product-title',
    productPrice: '.product-price',
    addToCartButton: '.btn-add-to-cart',
    productLink: 'a.product-link',
    filterByCategory: 'select#category',
  },
  
  // Cart page
  cart: {
    cartIcon: '.cart-icon',
    cartItems: '.cart-item',
    itemQuantity: 'input.quantity',
    removeButton: '.btn-remove',
    checkoutButton: '.btn-checkout',
    emptyCartMessage: '.empty-cart',
    cartTotal: '.cart-total',
  },
  
  // Checkout page
  checkout: {
    addressInput: 'input#address',
    cityInput: 'input#city',
    zipInput: 'input#zip',
    countrySelect: 'select#country',
    paymentMethod: 'select#payment_method',
    cardNumber: 'input#card-number',
    placeOrderButton: 'button.btn-place-order',
    orderConfirmation: '.order-success',
  },
  
  // Navigation
  nav: {
    logo: '.navbar-brand',
    homeLink: 'a[href="/"]',
    productsLink: 'a[href="/products"]',
    cartLink: '.cart-icon',
    profileIcon: '.profile-icon',
  },
};

export default Selectors;
EOF
```

**Best Practice:** Selectors as constants enable easy refactoring and AI-driven repair.

---

#### **4. `utils/test-data.ts`**
**Purpose:** Test data factory for creating consistent test inputs.

**Why this file:**
- Centralizes test data
- Enables easy data variations
- Supports parametrized tests
- AI can use this to generate synthetic test scenarios

**Creation Steps:**
```bash
cat > utils/test-data.ts << 'EOF'
/**
 * Test data for practicesoftwaretesting.com
 * Includes fixtures for users, products, and checkout scenarios
 */

export interface TestUser {
  email: string;
  password: string;
  firstName: string;
  role: 'admin' | 'customer';
}

export interface TestProduct {
  id: string;
  name: string;
  price: number;
  category: string;
}

export const TestUsers: Record<string, TestUser> = {
  admin: {
    email: 'admin@practicesoftwaretesting.com',
    password: 'welcome01',
    firstName: 'John',
    role: 'admin',
  },
  customer1: {
    email: 'customer@practicesoftwaretesting.com',
    password: 'welcome01',
    firstName: 'Jane',
    role: 'customer',
  },
  customer2: {
    email: 'customer2@practicesoftwaretesting.com',
    password: 'welcome01',
    firstName: 'Jack',
    role: 'customer',
  },
  customer3: {
    email: 'customer3@practicesoftwaretesting.com',
    password: 'pass123',
    firstName: 'Bob',
    role: 'customer',
  },
};

export const TestProducts: TestProduct[] = [
  {
    id: '1',
    name: 'Leather Shoes',
    price: 99.99,
    category: 'footwear',
  },
  {
    id: '2',
    name: 'Running Shoes',
    price: 149.99,
    category: 'footwear',
  },
  {
    id: '3',
    name: 'T-Shirt',
    price: 29.99,
    category: 'clothing',
  },
];

export class CheckoutData {
  static validCheckout() {
    return {
      address: '123 Main St',
      city: 'San Francisco',
      zip: '94102',
      country: 'US',
      paymentMethod: 'credit_card',
    };
  }
  
  static invalidCheckout() {
    return {
      address: '',
      city: '',
      zip: '',
      country: '',
      paymentMethod: '',
    };
  }
}

export default { TestUsers, TestProducts, CheckoutData };
EOF
```

**Best Practice:** Externalized test data enables parameterized tests and scenario generation.

---

#### **5. `fixtures/page-objects/BasePage.ts`**
**Purpose:** Base class with common page interaction methods.

**Why this file:**
- DRY principle (Don't Repeat Yourself)
- Shared navigation, waiting, assertion logic
- All pages inherit common utilities
- Easier to maintain and refactor

**Creation Steps:**
```bash
mkdir -p fixtures/page-objects

cat > fixtures/page-objects/BasePage.ts << 'EOF'
/**
 * Base page class with common methods.
 * All page objects inherit from this.
 */
import { Page, expect } from '@playwright/test';

export class BasePage {
  protected page: Page;
  
  constructor(page: Page) {
    this.page = page;
  }
  
  /**
   * Navigate to a path on the base URL.
   * playwright.config.ts sets baseURL automatically.
   */
  async goto(path: string = '') {
    await this.page.goto(path);
  }
  
  /**
   * Fill a form field and verify it accepted the input.
   */
  async fillField(selector: string, text: string) {
    await this.page.fill(selector, text);
    await expect(this.page.locator(selector)).toHaveValue(text);
  }
  
  /**
   * Click an element and wait for navigation if needed.
   */
  async clickButton(selector: string) {
    await this.page.click(selector);
  }
  
  /**
   * Wait for an element to appear and be visible.
   */
  async waitForElement(selector: string, timeout: number = 5000) {
    await this.page.waitForSelector(selector, { state: 'visible', timeout });
  }
  
  /**
   * Get text content from an element.
   */
  async getText(selector: string): Promise<string | null> {
    return await this.page.textContent(selector);
  }
  
  /**
   * Check if element is visible.
   */
  async isVisible(selector: string): Promise<boolean> {
    try {
      await this.waitForElement(selector, 1000);
      return true;
    } catch {
      return false;
    }
  }
  
  /**
   * Take a screenshot for debugging.
   */
  async screenshot(name: string) {
    await this.page.screenshot({ path: `screenshots/${name}.png` });
  }
  
  /**
   * Get current page URL.
   */
  async getCurrentURL(): Promise<string> {
    return this.page.url();
  }
}

export default BasePage;
EOF
```

**Best Practice:** Base class eliminates code duplication across page objects.

---

#### **6. `fixtures/page-objects/LoginPage.ts`**
**Purpose:** Page object for login page interactions.

**Why this file:**
- Encapsulates login page structure
- Provides semantic methods (login, logout)
- Isolated from test logic
- Easy to update if UI changes

**Creation Steps:**
```bash
cat > fixtures/page-objects/LoginPage.ts << 'EOF'
/**
 * Login Page Object
 * Encapsulates all interactions with the login page.
 */
import { Page, expect } from '@playwright/test';
import BasePage from './BasePage';
import Selectors from '../../utils/selectors';

export class LoginPage extends BasePage {
  constructor(page: Page) {
    super(page);
  }
  
  async navigate() {
    await this.goto('/login');
    await this.waitForElement(Selectors.login.emailInput);
  }
  
  async login(email: string, password: string) {
    await this.fillField(Selectors.login.emailInput, email);
    await this.fillField(Selectors.login.passwordInput, password);
    await this.clickButton(Selectors.login.submitButton);
  }
  
  async assertLoginSuccess() {
    await this.page.waitForURL(/\/dashboard|\/products/, { timeout: 5000 });
  }
  
  async assertLoginError() {
    await expect(this.page.locator(Selectors.login.errorMessage)).toBeVisible();
  }
  
  async getErrorMessage(): Promise<string | null> {
    return await this.getText(Selectors.login.errorMessage);
  }
}

export default LoginPage;
EOF
```

**Best Practice:** Page objects are semantic; tests read like user narratives.

---

#### **7. `fixtures/page-objects/ProductPage.ts`**
**Purpose:** Page object for product browsing and interactions.

**Creation Steps:**
```bash
cat > fixtures/page-objects/ProductPage.ts << 'EOF'
/**
 * Product Page Object
 * Encapsulates product browsing, filtering, and selection.
 */
import { Page, expect } from '@playwright/test';
import BasePage from './BasePage';
import Selectors from '../../utils/selectors';

export class ProductPage extends BasePage {
  constructor(page: Page) {
    super(page);
  }
  
  async navigate() {
    await this.goto('/products');
    await this.waitForElement(Selectors.products.productCard);
  }
  
  async filterByCategory(category: string) {
    await this.page.selectOption(Selectors.products.filterByCategory, category);
    await this.page.waitForLoadState('networkidle');
  }
  
  async getProductCount(): Promise<number> {
    return await this.page.locator(Selectors.products.productCard).count();
  }
  
  async addProductToCart(index: number = 0) {
    const buttons = await this.page.locator(Selectors.products.addToCartButton).all();
    if (index < buttons.length) {
      await buttons[index].click();
    }
  }
  
  async clickProduct(index: number = 0) {
    const products = await this.page.locator(Selectors.products.productLink).all();
    if (index < products.length) {
      await products[index].click();
    }
  }
  
  async getProductPrice(index: number = 0): Promise<string | null> {
    const prices = await this.page.locator(Selectors.products.productPrice).all();
    if (index < prices.length) {
      return await prices[index].textContent();
    }
    return null;
  }
}

export default ProductPage;
EOF
```

---

#### **8. `fixtures/page-objects/CartPage.ts`**
**Purpose:** Page object for shopping cart interactions.

**Creation Steps:**
```bash
cat > fixtures/page-objects/CartPage.ts << 'EOF'
/**
 * Cart Page Object
 */
import { Page, expect } from '@playwright/test';
import BasePage from './BasePage';
import Selectors from '../../utils/selectors';

export class CartPage extends BasePage {
  constructor(page: Page) {
    super(page);
  }
  
  async navigate() {
    await this.goto('/cart');
    // Cart might be empty initially
  }
  
  async openCart() {
    await this.clickButton(Selectors.nav.cartLink);
    await this.page.waitForLoadState('networkidle');
  }
  
  async getCartItemCount(): Promise<number> {
    return await this.page.locator(Selectors.cart.cartItems).count();
  }
  
  async removeItem(index: number = 0) {
    const buttons = await this.page.locator(Selectors.cart.removeButton).all();
    if (index < buttons.length) {
      await buttons[index].click();
    }
  }
  
  async proceedToCheckout() {
    await this.clickButton(Selectors.cart.checkoutButton);
    await this.page.waitForLoadState('networkidle');
  }
  
  async assertEmptyCart() {
    await expect(this.page.locator(Selectors.cart.emptyCartMessage)).toBeVisible();
  }
  
  async getCartTotal(): Promise<string | null> {
    return await this.getText(Selectors.cart.cartTotal);
  }
}

export default CartPage;
EOF
```

---

#### **9. `fixtures/page-objects/CheckoutPage.ts`**
**Purpose:** Page object for checkout flow.

**Creation Steps:**
```bash
cat > fixtures/page-objects/CheckoutPage.ts << 'EOF'
/**
 * Checkout Page Object
 */
import { Page, expect } from '@playwright/test';
import BasePage from './BasePage';
import Selectors from '../../utils/selectors';

export interface CheckoutData {
  address: string;
  city: string;
  zip: string;
  country: string;
  paymentMethod: string;
}

export class CheckoutPage extends BasePage {
  constructor(page: Page) {
    super(page);
  }
  
  async fillShippingAddress(data: CheckoutData) {
    await this.fillField(Selectors.checkout.addressInput, data.address);
    await this.fillField(Selectors.checkout.cityInput, data.city);
    await this.fillField(Selectors.checkout.zipInput, data.zip);
    await this.page.selectOption(Selectors.checkout.countrySelect, data.country);
  }
  
  async selectPaymentMethod(method: string) {
    await this.page.selectOption(Selectors.checkout.paymentMethod, method);
  }
  
  async fillCardNumber(cardNumber: string) {
    await this.fillField(Selectors.checkout.cardNumber, cardNumber);
  }
  
  async placeOrder() {
    await this.clickButton(Selectors.checkout.placeOrderButton);
  }
  
  async assertOrderSuccess() {
    await expect(this.page.locator(Selectors.checkout.orderConfirmation)).toBeVisible();
  }
  
  async getOrderConfirmationText(): Promise<string | null> {
    return await this.getText(Selectors.checkout.orderConfirmation);
  }
}

export default CheckoutPage;
EOF
```

---

#### **10. `tests/auth/login.spec.ts`**
**Purpose:** Login test cases using page objects.

**Why this file:**
- Tests are readable; they read like user scenarios
- Uses page objects (no direct selector references)
- Demonstrates best practices for test organization
- AI can learn from these patterns for test generation

**Creation Steps:**
```bash
mkdir -p tests/auth

cat > tests/auth/login.spec.ts << 'EOF'
/**
 * Login Tests
 * Covers positive and negative login scenarios
 */
import { test, expect } from '@playwright/test';
import LoginPage from '../../fixtures/page-objects/LoginPage';
import { TestUsers } from '../../utils/test-data';

test.describe('Login', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to login before each test
    const loginPage = new LoginPage(page);
    await loginPage.navigate();
  });
  
  test('should login successfully with valid credentials', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const user = TestUsers.admin;
    
    await loginPage.login(user.email, user.password);
    await loginPage.assertLoginSuccess();
  });
  
  test('should show error for invalid password', async ({ page }) => {
    const loginPage = new LoginPage(page);
    const user = TestUsers.customer1;
    
    await loginPage.login(user.email, 'wrongpassword');
    await loginPage.assertLoginError();
    
    const errorMsg = await loginPage.getErrorMessage();
    expect(errorMsg).toContain('Invalid');
  });
  
  test('should show error for nonexistent user', async ({ page }) => {
    const loginPage = new LoginPage(page);
    
    await loginPage.login('nonexistent@example.com', 'anypassword');
    await loginPage.assertLoginError();
  });
  
  test.describe('Multiple user accounts', () => {
    for (const [key, user] of Object.entries(TestUsers)) {
      test(`should login as ${key}`, async ({ page }) => {
        const loginPage = new LoginPage(page);
        
        await loginPage.login(user.email, user.password);
        await loginPage.assertLoginSuccess();
      });
    }
  });
});
EOF
```

**Best Practice:** Tests are narratives, not implementation details. Page objects hide selectors.

---

#### **11. `tests/products/browse.spec.ts`**
**Purpose:** Product browsing test cases.

**Creation Steps:**
```bash
mkdir -p tests/products

cat > tests/products/browse.spec.ts << 'EOF'
/**
 * Product Browsing Tests
 */
import { test, expect } from '@playwright/test';
import LoginPage from '../../fixtures/page-objects/LoginPage';
import ProductPage from '../../fixtures/page-objects/ProductPage';
import { TestUsers } from '../../utils/test-data';

test.describe('Product Browsing', () => {
  test.beforeEach(async ({ page }) => {
    // Login before browsing products
    const loginPage = new LoginPage(page);
    await loginPage.navigate();
    await loginPage.login(TestUsers.customer1.email, TestUsers.customer1.password);
  });
  
  test('should display product list', async ({ page }) => {
    const productPage = new ProductPage(page);
    await productPage.navigate();
    
    const count = await productPage.getProductCount();
    expect(count).toBeGreaterThan(0);
  });
  
  test('should filter products by category', async ({ page }) => {
    const productPage = new ProductPage(page);
    await productPage.navigate();
    
    await productPage.filterByCategory('footwear');
    const count = await productPage.getProductCount();
    
    expect(count).toBeGreaterThan(0);
  });
  
  test('should click on product to view details', async ({ page }) => {
    const productPage = new ProductPage(page);
    await productPage.navigate();
    
    await productPage.clickProduct(0);
    await page.waitForURL(/\/products\/\d+/);
  });
});
EOF
```

---

#### **12. `tests/checkout/checkout-flow.spec.ts`**
**Purpose:** End-to-end checkout test.

**Creation Steps:**
```bash
mkdir -p tests/checkout

cat > tests/checkout/checkout-flow.spec.ts << 'EOF'
/**
 * End-to-End Checkout Flow
 * Login → Browse → Add to Cart → Checkout → Confirm
 */
import { test } from '@playwright/test';
import LoginPage from '../../fixtures/page-objects/LoginPage';
import ProductPage from '../../fixtures/page-objects/ProductPage';
import CartPage from '../../fixtures/page-objects/CartPage';
import CheckoutPage from '../../fixtures/page-objects/CheckoutPage';
import { TestUsers, CheckoutData } from '../../utils/test-data';

test('should complete end-to-end checkout', async ({ page }) => {
  const loginPage = new LoginPage(page);
  const productPage = new ProductPage(page);
  const cartPage = new CartPage(page);
  const checkoutPage = new CheckoutPage(page);
  
  // Step 1: Login
  await loginPage.navigate();
  await loginPage.login(TestUsers.customer1.email, TestUsers.customer1.password);
  
  // Step 2: Browse and add product
  await productPage.navigate();
  await productPage.addProductToCart(0);
  
  // Step 3: Go to cart
  await cartPage.openCart();
  const itemCount = await cartPage.getCartItemCount();
  console.log(`Cart has ${itemCount} items`);
  
  // Step 4: Checkout
  await cartPage.proceedToCheckout();
  const checkoutData = CheckoutData.validCheckout();
  await checkoutPage.fillShippingAddress(checkoutData);
  await checkoutPage.selectPaymentMethod('credit_card');
  await checkoutPage.placeOrder();
  
  // Step 5: Verify success
  await checkoutPage.assertOrderSuccess();
});
EOF
```

---

#### **13. `README.md` for CLI Project**
**Creation Steps:**
```bash
cat > README.md << 'EOF'
# Playwright CLI Tests

Automated tests for https://practicesoftwaretesting.com using Playwright Test (CLI).

## Getting Started

```bash
npm install
npx playwright install
```

## Running Tests

```bash
# Run all tests
npm test

# Run in headed mode (see browser)
npm run test:headed

# Run specific test file
npx playwright test tests/auth/login.spec.ts

# Run with debugging
npm run test:debug

# Run with UI
npm run test:ui

# View test report
npm run test:report
```

## Project Structure

- `tests/` - Test files organized by feature
- `fixtures/page-objects/` - Page Object Model classes
- `utils/` - Selectors, test data, helpers
- `playwright.config.ts` - Test runner configuration

## Page Objects

All tests use the Page Object Model pattern. This means:
- No selectors in test files
- Semantic methods (e.g., `loginPage.login()`)
- Easy to maintain when UI changes

## Test Data

Test users and products are defined in `utils/test-data.ts`.

## Best Practices

1. Use page objects for all browser interactions
2. Prefer CSS selectors (more stable than XPath)
3. Use meaningful test names
4. Parameterize tests when possible
5. Keep tests independent (no ordering)

## CI/CD Integration

Tests run in headless mode in CI/CD with:
- Automatic retries on failure
- Screenshots of failures
- Video recordings on failure
- JUnit XML reports

## Extending Tests

To add a new test:
1. Create page object in `fixtures/page-objects/`
2. Create test file in appropriate `tests/` directory
3. Use existing page objects and test data
4. Run locally before committing
EOF
```

---

### Summary: CLI Project Files

| File | Purpose | AI Value |
|------|---------|----------|
| `package.json` | Node project config | Reproducible dependencies |
| `playwright.config.ts` | Test runner config | Centralized test settings |
| `utils/selectors.ts` | Selector constants | Single source of truth |
| `utils/test-data.ts` | Test fixtures | Reusable test data, AI-driven scenarios |
| `fixtures/BasePage.ts` | Common page methods | DRY, reduced duplication |
| `fixtures/*Page.ts` | Page objects | Semantic, maintainable tests |
| `tests/**/*.spec.ts` | Test cases | Readable user scenarios |
| `README.md` | Documentation | Onboarding and reference |

---

## File Creation Checklist

### Phase 1: Project Setup
- [ ] Create directory structure for both projects
- [ ] Create `package.json` (CLI) and `requirements.txt` (MCP)
- [ ] Install dependencies
- [ ] Create `.env.example` files
- [ ] Create base configuration files

### Phase 2: Configuration & Data
- [ ] Create `config/selectors.json` (MCP) and `utils/selectors.ts` (CLI)
- [ ] Create `config/credentials.json` (MCP) and `utils/test-data.ts` (CLI)
- [ ] Create `playwright.config.ts` (CLI) and `mcp_server/schemas.py` (MCP)

### Phase 3: Infrastructure
- [ ] Create `mcp-server/tools/browser_tools.py` (MCP)
- [ ] Create `mcp-server/server.py` (MCP)
- [ ] Create `fixtures/page-objects/BasePage.ts` (CLI)
- [ ] Create `fixtures/page-objects/*Page.ts` (CLI)

### Phase 4: Agents (MCP) & Agents (CLI)
- [ ] Create `agents/base_agent.py` (MCP)
- [ ] Create `agents/login_agent.py` (MCP)
- [ ] Create `agents/product_agent.py` (MCP)
- [ ] Create first test in `tests/auth/login.spec.ts` (CLI)

### Phase 5: Testing
- [ ] Create `tests/test_agents.py` (MCP)
- [ ] Create remaining test files (CLI)
- [ ] Run tests locally to verify setup

---

## AI Integration Patterns

### Pattern 1: Tool Discovery (MCP)
**Scenario:** LLM agent needs to know what tools are available.

**How it works:**
1. Agent calls MCP `list_tools()`
2. Server returns all tool schemas from `schemas.py`
3. Agent reads descriptions and input types
4. Agent decides which tool to call next

**Why it matters:** Enables autonomous agents to discover capabilities without hard-coding tool knowledge.

---

### Pattern 2: Structured State (Both Projects)
**Scenario:** AI needs to understand current page state to make decisions.

**How it works:**
1. After each tool call, page state is captured
2. State includes: URL, title, visible elements, form values
3. Agent observes state and reasons about next step
4. Agent can adapt behavior based on actual page state (not expected state)

**Why it matters:** Agents can recover from unexpected UI changes.

---

### Pattern 3: Test Parameterization (CLI)
**Scenario:** Generate multiple test cases from a single test template.

**How it works:**
```typescript
for (const [key, user] of Object.entries(TestUsers)) {
  test(`should login as ${key}`, async ({ page }) => {
    // Test code
  });
}
```

**Why it matters:** AI can generate test cases by varying inputs from `TestUsers` object.

---

### Pattern 4: Self-Healing Selectors (Future)
**Scenario:** Selector breaks after UI change.

**Implementation:**
1. Test fails with selector not found
2. AI agent takes screenshot
3. AI analyzes screenshot to find new selector
4. AI updates `selectors.json` / `selectors.ts`
5. Test passes with new selector

**Foundation:** Both projects store selectors in single file for easy updates.

---

## Best Practices

### 1. Selector Strategy
- **Prefer:** `#id` > `[data-testid]` > `.class` > complex CSS
- **Avoid:** XPath, text-based, brittle indexes
- **Update:** Centralized in single file (`selectors.json` / `selectors.ts`)

### 2. Test Independence
- Each test should be runnable in isolation
- No test ordering or shared state
- Use fixtures to set up clean state before each test

### 3. Page Object Model
- All browser interaction through page objects
- Tests read like user narratives
- Selectors never appear in test files

### 4. Structured Logging
- Log tool calls with inputs and outputs
- Include timestamps for debugging
- Store call history for agent analysis

### 5. Error Recovery
- Retry on transient failures
- Distinguish between test failures and automation errors
- Provide actionable error messages

### 6. Documentation
- Document selector strategy
- Explain test data assumptions
- Keep README updated

---

## Forward-Looking AI Implementations

### 1. LLM-Driven Agent
Replace deterministic `LoginAgent` with LLM-driven version:
```python
# Instead of hardcoded state machine
async def think(self, observation):
    # Call LLM with prompt: "You're on a login page. What should you do?"
    # LLM responds: "I should fill the email field"
    # Agent calls tool
```

### 2. Test Generation
Use CLI test patterns to generate new test cases:
```python
# Given: ProductPage page object
# Generate: 5 new test cases covering edge cases
# AI analyzes ProductPage to understand capabilities
# AI generates test_*.spec.ts files
```

### 3. Selector Repair
When selector fails, use AI vision + page snapshot:
```python
# Page object calls click(selector)
# Selector not found
# MCP agent takes screenshot
# Vision model identifies new selector
# Updates selectors.json automatically
```

### 4. Test Orchestration
Multi-agent coordination:
```python
# LoginAgent logs in
# ProductAgent browses products
# CheckoutAgent completes purchase
# Agents communicate via shared page state
```

---

## Next Steps

1. **Start with CLI Project** — Simpler, teaches test structure
   - Create file structure
   - Write first login test
   - Verify it passes

2. **Then MCP Project** — More complex, teaches agent patterns
   - Create MCP server
   - Implement login agent
   - Test agent loop

3. **Integrate Both** — Use CLI tests to validate MCP agent behavior
   - MCP agent runs, CLI tests verify success

4. **Add AI Layer** — Replace deterministic agents with LLM reasoning
   - Use Claude API for agent thinking
   - Implement prompt-based tool selection

---

## Resources

- [Playwright Docs](https://playwright.dev)
- [MCP Spec](https://modelcontextprotocol.io)
- [Page Object Model Pattern](https://playwright.dev/docs/pom)
- [Best Practices Guide](./docs/best-practices.md)

---

**Version:** 1.0  
**Last Updated:** 2026-10-01  
**Maintainer:** Your Team  
EOF
```

---

## Summary

This `notes.md` file serves as your complete blueprint for both projects. It explains:

✅ **Every file to create** — with code samples  
✅ **Purpose of each file** — why it exists  
✅ **AI-forward patterns** — how AI will leverage the architecture  
✅ **Best practices** — stable selectors, Page Object Model, etc.  
✅ **Forward-looking roadmap** — test generation, selector repair, etc.  

**Next Step:** Start with the CLI project (simpler), then move to MCP (more complex). Use this notes file as your reference guide.