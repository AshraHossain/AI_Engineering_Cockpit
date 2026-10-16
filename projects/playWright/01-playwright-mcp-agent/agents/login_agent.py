"""Login Agent: Autonomously logs in."""
import logging
from typing import Optional, Dict, Any
from base_agent import BaseAgent


logger = logging.getLogger(__name__)


class LoginAgent(BaseAgent):
    """Agent that logs in autonomously using a state machine."""

    def __init__(self, mcp_client, credentials: dict):
        super().__init__(mcp_client, "LoginAgent", max_iterations=10)
        self.credentials = credentials
        self.state = "start"

    async def think(self, observation: Optional[str] = None) -> str:
        """Deterministic login flow using state machine."""

        if self.state == "start":
            self.state = "navigating"
            return "navigate(url=https://practicesoftwaretesting.com)"

        elif self.state == "navigating":
            self.state = "filling_email"
            email = self.credentials.get("email", "")
            return f"fill(selector=input#email, text={email})"

        elif self.state == "filling_email":
            self.state = "filling_password"
            password = self.credentials.get("password", "")
            return f"fill(selector=input#password, text={password})"

        elif self.state == "filling_password":
            self.state = "submitting"
            return "click(selector=button[type='submit'])"

        elif self.state == "submitting":
            self.state = "waiting_for_dashboard"
            return "wait_for(selector=.dashboard-container, state=visible, timeout=10000)"

        elif self.state == "waiting_for_dashboard":
            return "done"

        else:
            return "done"

    async def login(self, account_key: str, accounts: dict) -> dict:
        """Run login for given account."""
        if account_key not in accounts:
            return {"success": False, "error": f"Account {account_key} not found"}

        account = accounts[account_key]
        self.credentials = account
        return await self.run_until_done(f"Login as {account_key}")
