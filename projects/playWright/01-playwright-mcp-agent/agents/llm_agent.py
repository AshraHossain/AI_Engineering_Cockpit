"""LLM-Driven Agent: Uses Claude for autonomous reasoning."""
import json
import logging
from typing import Optional, Dict, Any
import os
from anthropic import Anthropic

from base_agent import BaseAgent


logger = logging.getLogger(__name__)


class LLMAgent(BaseAgent):
    """Agent that uses Claude for autonomous reasoning."""

    def __init__(self, mcp_client, name: str, api_key: Optional[str] = None):
        super().__init__(mcp_client, name, max_iterations=10)

        api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")

        self.client = Anthropic(api_key=api_key)
        self.conversation_history = []

    async def think(self, observation: Optional[str] = None) -> str:
        """Use Claude to decide next action."""

        # Build the prompt
        prompt = self._build_prompt(observation)

        # Add to conversation history
        self.conversation_history.append({
            "role": "user",
            "content": prompt
        })

        # Call Claude
        logger.info(f"[{self.name}] Asking Claude: {prompt[:100]}...")

        response = self.client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=256,
            messages=self.conversation_history
        )

        decision = response.content[0].text

        # Add response to history
        self.conversation_history.append({
            "role": "assistant",
            "content": decision
        })

        logger.info(f"[{self.name}] Claude decided: {decision[:100]}...")

        # Parse Claude's decision
        return self._parse_claude_decision(decision)

    def _build_prompt(self, observation: Optional[str] = None) -> str:
        """Build prompt for Claude."""

        tools_desc = self._get_available_tools_desc()

        prompt = f"""You are an autonomous browser automation agent. Your job is to automate web tasks.

Available Tools:
{tools_desc}

Current Goal: {self.goal}

Tool Format: Use tool_name(arg1=value1, arg2=value2)
Response Format: Either return "done" when finished, or the next tool call.

"""

        if observation:
            obs_obj = json.loads(observation)
            prompt += f"Last Tool Result: {json.dumps(obs_obj, indent=2)}\n\n"

        prompt += f"Iteration: {self.iteration_count}/{self.max_iterations}\n"
        prompt += "What is your next action?"

        return prompt

    def _get_available_tools_desc(self) -> str:
        """Get description of available tools."""
        tools = [
            "navigate(url=...) - Go to a URL",
            "fill(selector=..., text=...) - Fill a form field",
            "click(selector=...) - Click an element",
            "extract_text(selector=...) - Get text from element",
            "wait_for(selector=..., state=visible) - Wait for element",
            "screenshot() - Take a screenshot",
        ]
        return "\n".join(f"- {t}" for t in tools)

    def _parse_claude_decision(self, decision: str) -> str:
        """Parse Claude's decision into tool call or done."""
        decision = decision.strip()

        # Check if Claude said we're done
        if "done" in decision.lower():
            return "done"

        # Look for tool call pattern: tool_name(args)
        if "(" in decision and ")" in decision:
            # Extract the tool call
            start = decision.find("navigate(")
            if start == -1:
                start = decision.find("fill(")
            if start == -1:
                start = decision.find("click(")
            if start == -1:
                start = decision.find("extract_text(")
            if start == -1:
                start = decision.find("wait_for(")

            if start != -1:
                end = decision.find(")", start) + 1
                return decision[start:end]

        # If we can't parse, ask Claude to clarify
        logger.warning(f"Could not parse Claude's decision: {decision}")
        return "done"


class LLMLoginAgent(LLMAgent):
    """LLM-driven login agent using Claude."""

    def __init__(self, mcp_client, credentials: dict, api_key: Optional[str] = None):
        super().__init__(mcp_client, "LLMLoginAgent", api_key)
        self.credentials = credentials
        self.goal = f"Log in with email: {credentials.get('email', 'unknown')}"

    async def login(self, account_key: str, accounts: dict) -> dict:
        """Run login using Claude reasoning."""
        if account_key not in accounts:
            return {"success": False, "error": f"Account {account_key} not found"}

        account = accounts[account_key]
        self.credentials = account
        self.goal = f"Log in as {account_key} with email {account['email']}"

        return await self.run_until_done(self.goal)


class LLMProductAgent(LLMAgent):
    """LLM-driven product browsing agent using Claude."""

    def __init__(self, mcp_client, api_key: Optional[str] = None):
        super().__init__(mcp_client, "LLMProductAgent", api_key)
        self.goal = "Browse products and find interesting items"

    async def browse_products(self) -> dict:
        """Browse products using Claude reasoning."""
        return await self.run_until_done(self.goal)


class LLMCheckoutAgent(LLMAgent):
    """LLM-driven checkout agent using Claude."""

    def __init__(self, mcp_client, checkout_data: dict = None, api_key: Optional[str] = None):
        super().__init__(mcp_client, "LLMCheckoutAgent", api_key)
        self.checkout_data = checkout_data or {}
        self.goal = "Complete checkout with shipping and payment"

    async def checkout(self, checkout_data: dict = None) -> dict:
        """Run checkout using Claude reasoning."""
        if checkout_data:
            self.checkout_data = checkout_data
        return await self.run_until_done(self.goal)
