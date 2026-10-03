"""Tool schemas for MCP server."""
import json
from dataclasses import dataclass


@dataclass
class NavigateInput:
    url: str
    wait_until: str = "networkidle"
    timeout: int = 30000


@dataclass
class ClickInput:
    selector: str
    timeout: int = 30000
    force: bool = False


@dataclass
class FillInput:
    selector: str
    text: str
    timeout: int = 30000


@dataclass
class ExtractTextInput:
    selector: str
    multiple: bool = False


@dataclass
class WaitForInput:
    selector: str
    timeout: int = 30000
    state: str = "visible"


class ToolSchemas:
    """Registry of all tool schemas for MCP."""

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
                        "default": "networkidle"
                    },
                    "timeout": {"type": "integer", "description": "Timeout in ms", "default": 30000}
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
                    "timeout": {"type": "integer", "default": 30000},
                    "force": {"type": "boolean", "default": False}
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
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30000}
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
                    "selector": {"type": "string"},
                    "multiple": {"type": "boolean", "default": False}
                },
                "required": ["selector"]
            }
        },
        "wait_for": {
            "name": "wait_for",
            "description": "Wait for element to reach a state",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "state": {"type": "string", "enum": ["visible", "hidden", "attached"], "default": "visible"},
                    "timeout": {"type": "integer", "default": 30000}
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
                    "full_page": {"type": "boolean", "default": False}
                }
            }
        }
    }

    @classmethod
    def get_all_tools(cls):
        return list(cls.TOOLS.values())

    @classmethod
    def get_tool(cls, name: str):
        return cls.TOOLS.get(name)
