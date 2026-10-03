"""MCP Server: Exposes Playwright tools for agents to call."""
import json
import asyncio
import logging
import sys
from typing import Any

from tools.browser_tools import BrowserTools
from tools.schemas import ToolSchemas


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class PlaywrightMCPServer:
    """MCP server wrapping Playwright tools."""

    def __init__(self):
        self.browser_tools = BrowserTools()
        self.is_initialized = False

    async def initialize_handler(self):
        """Initialize browser."""
        if not self.is_initialized:
            await self.browser_tools.initialize()
            self.is_initialized = True

    async def list_tools_handler(self):
        """Return available tools."""
        tools = ToolSchemas.get_all_tools()
        return {
            "tools": tools
        }

    async def call_tool_handler(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool call from agent."""
        logger.info(f"Tool call: {tool_name} with args {arguments}")

        try:
            await self.initialize_handler()
            result = await self._execute_tool(tool_name, arguments)
            return json.dumps(result)
        except Exception as e:
            logger.error(f"Tool error: {e}")
            return json.dumps({"error": str(e)})

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

    async def run_interactive(self):
        """Run in interactive mode (stdin/stdout for MCP protocol)."""
        logger.info("Playwright MCP Server started (interactive mode)")

        # Initialize browser
        await self.initialize_handler()

        # Simple REPL for testing
        print("Playwright MCP Server ready. Enter commands or 'quit' to exit.")
        print("Available tools:", ", ".join([t["name"] for t in ToolSchemas.get_all_tools()]))

        while True:
            try:
                user_input = input("\n> ").strip()

                if user_input.lower() in ["quit", "exit"]:
                    await self.browser_tools.close()
                    break

                if not user_input:
                    continue

                # Parse simple command format: tool_name(arg1=val1, arg2=val2)
                if "(" in user_input and ")" in user_input:
                    tool_name = user_input.split("(")[0]
                    args_str = user_input.split("(")[1].rstrip(")")
                    args = {}
                    for pair in args_str.split(","):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            args[k.strip()] = v.strip().strip("'\"")

                    result = await self.call_tool_handler(tool_name, args)
                    print(f"Result: {result}")
                else:
                    print("Format: tool_name(arg1=val1, arg2=val2)")

            except KeyboardInterrupt:
                await self.browser_tools.close()
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                print(f"Error: {e}")


async def main():
    """Entry point."""
    server = PlaywrightMCPServer()

    # Detect if running in interactive mode or stdio mode
    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        await server.run_interactive()
    else:
        # For actual MCP usage, would implement stdio protocol
        # For now, just show it's ready
        logger.info("Playwright MCP Server initialized")
        logger.info("Use --interactive flag for interactive mode")
        await server.initialize_handler()


if __name__ == "__main__":
    asyncio.run(main())
