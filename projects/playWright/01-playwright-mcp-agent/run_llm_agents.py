#!/usr/bin/env python3
"""Run LLM-driven agents demonstrating Claude integration."""
import asyncio
import json
import sys
import os
from pathlib import Path

# Add paths
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "mcp-server"))

from mcp_server.server import PlaywrightMCPServer
from agents.llm_agent import LLMLoginAgent, LLMProductAgent, LLMCheckoutAgent
from agents.login_agent import LoginAgent  # For comparison


class MCPClient:
    """MCP client for agents to call tools through."""

    def __init__(self, mcp_server: PlaywrightMCPServer):
        self.mcp_server = mcp_server

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call tool through MCP server."""
        result_json = await self.mcp_server.call_tool_handler(tool_name, arguments)
        return json.loads(result_json)


async def demo_deterministic_login():
    """Demo: Deterministic login (hardcoded state machine)"""
    print("\n" + "="*70)
    print("DEMO 1: Deterministic Agent (State Machine)")
    print("="*70)

    mcp_server = PlaywrightMCPServer()
    mcp_client = MCPClient(mcp_server)

    credentials_path = Path(__file__).parent / "config" / "credentials.json"
    with open(credentials_path) as f:
        credentials = json.load(f)

    agent = LoginAgent(mcp_client, {})
    result = await agent.login("customer1", credentials["accounts"])

    print(f"\n✓ Deterministic Agent Result:")
    print(f"  Success: {result.get('success')}")
    print(f"  Iterations: {result.get('iterations')}")
    print(f"  Tool calls: {len(agent.call_history)}")

    await mcp_server.browser_tools.close()
    return result


async def demo_llm_login():
    """Demo: LLM-driven login (Claude reasoning)"""
    print("\n" + "="*70)
    print("DEMO 2: LLM-Driven Agent (Claude Reasoning)")
    print("="*70)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("\n❌ ANTHROPIC_API_KEY not set!")
        print("   Set it in .env file or as environment variable")
        print("   Then run: python run_llm_agents.py")
        return {"success": False, "error": "API key not set"}

    mcp_server = PlaywrightMCPServer()
    mcp_client = MCPClient(mcp_server)

    credentials_path = Path(__file__).parent / "config" / "credentials.json"
    with open(credentials_path) as f:
        credentials = json.load(f)

    agent = LLMLoginAgent(mcp_client, {}, api_key=api_key)
    result = await agent.login("customer1", credentials["accounts"])

    print(f"\n✓ LLM Agent Result:")
    print(f"  Success: {result.get('success')}")
    print(f"  Iterations: {result.get('iterations')}")
    print(f"  Tool calls: {len(agent.call_history)}")
    print(f"  Claude conversations: {len(agent.conversation_history)}")

    if agent.call_history:
        print(f"\n  Tool Calls Made:")
        for i, call in enumerate(agent.call_history[:5], 1):
            print(f"    {i}. {call['tool']}: {call['result'].get('success', False)}")

    await mcp_server.browser_tools.close()
    return result


async def compare_approaches():
    """Compare both approaches side-by-side"""
    print("\n" + "="*70)
    print("COMPARISON: Deterministic vs LLM-Driven")
    print("="*70)

    print("\n📊 Deterministic Agent (hardcoded):")
    print("  ✓ Predictable behavior")
    print("  ✓ No API calls needed")
    print("  ✓ Fast execution")
    print("  ✗ Cannot adapt to UI changes")
    print("  ✗ Requires code changes for new flows")

    print("\n🤖 LLM-Driven Agent (Claude):")
    print("  ✓ Adapts to UI changes")
    print("  ✓ Can handle unexpected scenarios")
    print("  ✓ Reads like natural language")
    print("  ✓ Easier to extend")
    print("  ✗ Requires API key and internet")
    print("  ✗ Slower due to API calls")
    print("  ✗ May make mistakes on complex tasks")


async def main():
    """Run demos."""
    print("\n🤖 Playwright MCP - AI Integration Demo")
    print("====================================\n")

    try:
        # Demo 1: Deterministic (always works)
        det_result = await demo_deterministic_login()

        # Demo 2: LLM-Driven (requires API key)
        llm_result = await demo_llm_login()

        # Comparison
        await compare_approaches()

        print("\n" + "="*70)
        print("✓ Demo Complete!")
        print("="*70)

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
