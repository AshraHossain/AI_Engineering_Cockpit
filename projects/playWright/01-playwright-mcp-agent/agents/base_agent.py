"""Base agent class for autonomous automation."""
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from abc import ABC, abstractmethod


logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Base class for browser automation agents."""

    def __init__(self, mcp_client, name: str, max_iterations: int = 10):
        self.mcp_client = mcp_client
        self.name = name
        self.max_iterations = max_iterations
        self.iteration_count = 0
        self.call_history: List[Dict[str, Any]] = []

    @abstractmethod
    async def think(self, observation: Optional[str] = None) -> str:
        """Think about current state and decide next action."""
        pass

    async def call_tool(self, tool_name: str, arguments: dict) -> Dict[str, Any]:
        """Call a tool via MCP."""
        logger.info(f"[{self.name}] Calling {tool_name} with {arguments}")
        result = await self.mcp_client.call_tool(tool_name, arguments)
        self._record_call(tool_name, arguments, result)
        return result

    async def run_until_done(self, goal: str) -> Dict[str, Any]:
        """Run agent loop until done or max iterations reached."""
        logger.info(f"[{self.name}] Starting with goal: {goal}")

        observation = None

        for i in range(self.max_iterations):
            self.iteration_count = i + 1

            # Step 1: Think
            decision = await self.think(observation)

            if decision == "done":
                logger.info(f"[{self.name}] Agent says we're done")
                return {"success": True, "iterations": self.iteration_count, "goal": goal}

            # Parse decision
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
            "goal": goal,
            "call_history": self.call_history
        }

    def _parse_decision(self, decision: str) -> tuple:
        """Parse decision string like 'click(selector=#button)'."""
        tool_name = decision.split("(")[0].strip()
        args_str = decision.split("(")[1].rstrip(")").strip()
        args = {}

        if args_str:
            for pair in args_str.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    args[k.strip()] = v.strip().strip("'\"")

        return tool_name, args

    def _record_call(self, tool_name: str, args: dict, result: dict):
        """Record tool call for debugging."""
        self.call_history.append({
            "tool": tool_name,
            "arguments": args,
            "result": result,
            "timestamp": datetime.now().isoformat()
        })
