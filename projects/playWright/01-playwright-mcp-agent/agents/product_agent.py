"""Product Agent: Browse and interact with products."""
import logging
from typing import Optional
from base_agent import BaseAgent


logger = logging.getLogger(__name__)


class ProductAgent(BaseAgent):
    """Agent that browses products and extracts information."""

    def __init__(self, mcp_client):
        super().__init__(mcp_client, "ProductAgent", max_iterations=10)
        self.state = "start"
        self.products_found = []

    async def think(self, observation: Optional[str] = None) -> str:
        """Browse products using state machine."""

        if self.state == "start":
            self.state = "navigating_to_products"
            return "navigate(url=https://practicesoftwaretesting.com/products)"

        elif self.state == "navigating_to_products":
            self.state = "extracting_products"
            return "extract_text(selector=.product-title, multiple=true)"

        elif self.state == "extracting_products":
            self.state = "adding_to_cart"
            return "click(selector=.btn-add-to-cart, force=true)"

        elif self.state == "adding_to_cart":
            return "done"

        else:
            return "done"

    async def browse_products(self):
        """Browse products and collect information."""
        return await self.run_until_done("Browse and extract product information")

    async def add_product_to_cart(self):
        """Add first product to cart."""
        return await self.run_until_done("Add product to cart")
