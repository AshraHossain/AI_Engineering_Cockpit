"""Browser automation tools powered by Playwright."""
import base64
from datetime import datetime
from typing import Optional


class BrowserTools:
    """Wrapper around Playwright for tool-based access."""

    def __init__(self):
        self.browser = None
        self.page = None
        self.playwright_module = None

    async def initialize(self):
        """Start browser and create page."""
        try:
            from playwright.async_api import async_playwright
            p = await async_playwright().start()
            self.browser = await p.chromium.launch(headless=True)
            self.page = await self.browser.new_page()
            await self.page.set_viewport_size({"width": 1280, "height": 720})
            return {"success": True, "message": "Browser initialized"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def close(self):
        """Close browser."""
        try:
            if self.page:
                await self.page.close()
            if self.browser:
                await self.browser.close()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

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
            return {"success": False, "error": str(e), "url": url}

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
                "base64": base64_image[:500] + "...",  # truncate for logging
                "page_url": self.page.url,
                "page_title": await self.page.title(),
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_page_state(self):
        """Get current page state."""
        try:
            return {
                "url": self.page.url,
                "title": await self.page.title(),
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
