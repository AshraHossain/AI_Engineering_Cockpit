"""Checkout Agent: Complete checkout flow."""
import logging
from typing import Optional, Dict, Any
from base_agent import BaseAgent


logger = logging.getLogger(__name__)


class CheckoutAgent(BaseAgent):
    """Agent that completes the checkout flow."""

    def __init__(self, mcp_client, checkout_data: dict = None):
        super().__init__(mcp_client, "CheckoutAgent", max_iterations=15)
        self.state = "start"
        self.checkout_data = checkout_data or {}

    async def think(self, observation: Optional[str] = None) -> str:
        """Checkout flow using state machine."""

        if self.state == "start":
            self.state = "navigating_to_cart"
            return "navigate(url=https://practicesoftwaretesting.com/cart)"

        elif self.state == "navigating_to_cart":
            self.state = "proceeding_to_checkout"
            return "click(selector=.btn-checkout)"

        elif self.state == "proceeding_to_checkout":
            self.state = "filling_address"
            address = self.checkout_data.get("address", "123 Main St")
            return f"fill(selector=input#address, text={address})"

        elif self.state == "filling_address":
            self.state = "filling_city"
            city = self.checkout_data.get("city", "San Francisco")
            return f"fill(selector=input#city, text={city})"

        elif self.state == "filling_city":
            self.state = "filling_zip"
            zip_code = self.checkout_data.get("zip", "94102")
            return f"fill(selector=input#zip, text={zip_code})"

        elif self.state == "filling_zip":
            self.state = "selecting_country"
            return "click(selector=select#country)"

        elif self.state == "selecting_country":
            self.state = "selecting_payment"
            return "click(selector=select#payment_method)"

        elif self.state == "selecting_payment":
            self.state = "placing_order"
            return "click(selector=button.btn-place-order)"

        elif self.state == "placing_order":
            self.state = "verifying"
            return "wait_for(selector=.order-success, state=visible, timeout=10000)"

        elif self.state == "verifying":
            return "done"

        else:
            return "done"

    async def checkout(self, checkout_data: dict = None):
        """Run checkout process."""
        if checkout_data:
            self.checkout_data = checkout_data
        return await self.run_until_done("Complete checkout process")
