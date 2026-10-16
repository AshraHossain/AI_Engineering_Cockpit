#!/usr/bin/env python3
"""Run autonomous agents demonstrating MCP automation."""
import asyncio
import json
import sys
import os
from pathlib import Path

# Add paths
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "mcp-server"))

from mcp_server.server import PlaywrightMCPServer
from agents.login_agent import LoginAgent
from agents.product_agent import ProductAgent
from agents.checkout_agent import CheckoutAgent


class MCPClient:
    """Mock MCP client for agents to call tools through."""

    def __init__(self, mcp_server: PlaywrightMCPServer):
        self.mcp_server = mcp_server

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call tool through MCP server."""
        result_json = await self.mcp_server.call_tool_handler(tool_name, arguments)
        return json.loads(result_json)


async def demo_login():
    """Demo: Autonomous login"""
    print("\n" + "="*60)
    print("DEMO 1: Autonomous Login Agent")
    print("="*60)

    # Create server
    mcp_server = PlaywrightMCPServer()
    mcp_client = MCPClient(mcp_server)

    # Load credentials
    credentials_path = Path(__file__).parent / "config" / "credentials.json"
    with open(credentials_path) as f:
        credentials = json.load(f)

    # Create and run login agent
    login_agent = LoginAgent(mcp_client, {})
    result = await login_agent.login("customer1", credentials["accounts"])

    print(f"\n✓ Login Agent Result:")
    print(f"  Success: {result.get('success')}")
    print(f"  Goal: {result.get('goal')}")
    print(f"  Iterations: {result.get('iterations')}")

    # Print call history
    if login_agent.call_history:
        print(f"\n  Call History ({len(login_agent.call_history)} calls):")
        for i, call in enumerate(login_agent.call_history, 1):
            print(f"    {i}. {call['tool']}: {call['result'].get('success', False)}")

    # Cleanup
    await mcp_server.browser_tools.close()

    return result


async def demo_full_flow():
    """Demo: Full checkout flow"""
    print("\n" + "="*60)
    print("DEMO 2: Full Checkout Flow")
    print("="*60)

    # Create server
    mcp_server = PlaywrightMCPServer()
    mcp_client = MCPClient(mcp_server)

    # Load credentials
    credentials_path = Path(__file__).parent / "config" / "credentials.json"
    with open(credentials_path) as f:
        credentials = json.load(f)

    account = credentials["accounts"]["customer1"]

    print(f"\nStep 1: Login as {account['email']}")
    login_agent = LoginAgent(mcp_client, {})
    login_result = await login_agent.login("customer1", credentials["accounts"])
    print(f"  ✓ Login: {login_result.get('success')}")

    print(f"\nStep 2: Browse Products")
    product_agent = ProductAgent(mcp_client)
    product_result = await product_agent.browse_products()
    print(f"  ✓ Product browse: {product_result.get('success')}")

    print(f"\nStep 3: Add to Cart")
    add_result = await product_agent.add_product_to_cart()
    print(f"  ✓ Add to cart: {add_result.get('success')}")

    print(f"\nStep 4: Checkout")
    checkout_data = {
        "address": "123 Main St",
        "city": "San Francisco",
        "zip": "94102",
        "country": "US",
        "payment_method": "credit_card"
    }
    checkout_agent = CheckoutAgent(mcp_client, checkout_data)
    checkout_result = await checkout_agent.checkout()
    print(f"  ✓ Checkout: {checkout_result.get('success')}")

    # Cleanup
    await mcp_server.browser_tools.close()

    return {
        "login": login_result,
        "browse": product_result,
        "add_cart": add_result,
        "checkout": checkout_result
    }


async def main():
    """Run demos."""
    print("\n🤖 Playwright MCP Agent Showcase")
    print("================================\n")

    try:
        # Demo 1: Just login
        login_result = await demo_login()

        print("\n" + "="*60)
        print("✓ Demo 1 Complete: Login Agent worked!")
        print("="*60)

        # Uncomment to run full flow
        # print("\nRunning full checkout flow...")
        # full_result = await demo_full_flow()
        # print("\n✓ Full flow completed!")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
