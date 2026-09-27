"""本机小程序桥接客户端（默认 SSE：http://127.0.0.1:4554/sse）。

封装 evaluate / navigate 等调用，供业务在微信小程序 appservice 内执行脚本。
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "http://127.0.0.1:4554/sse"
APPSERVICE_CONTEXT_ID = 3


class MiniappMCP:
    def __init__(self, url: str = DEFAULT_MCP_URL):
        self.url = url

    @asynccontextmanager
    async def session(self):
        async with sse_client(self.url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        async with self.session() as session:
            result = await session.call_tool(name, arguments or {})
            return _parse_tool_result(result)

    async def get_info(self) -> Any:
        return await self.call_tool("miniapp_get_info")

    async def get_current_route(self) -> Any:
        return await self.call_tool("miniapp_get_current_route")

    async def navigate(self, route: str, method: str = "navigateTo") -> Any:
        return await self.call_tool(
            "miniapp_navigate",
            {"route": route, "method": method},
        )

    async def evaluate(
        self,
        expression: str,
        *,
        await_promise: bool = True,
        context_id: int = APPSERVICE_CONTEXT_ID,
    ) -> Any:
        return await self.call_tool(
            "miniapp_evaluate",
            {
                "expression": expression,
                "await_promise": await_promise,
                "context_id": context_id,
            },
        )

    async def ping(self) -> dict[str, Any]:
        try:
            info = await self.get_info()
            route = await self.get_current_route()
            return {
                "ok": True,
                "mcp": True,
                "appid": (info or {}).get("appid"),
                "name": (info or {}).get("name"),
                "route": (route or {}).get("route") if isinstance(route, dict) else route,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("MCP ping failed")
            return {"ok": False, "mcp": False, "error": str(exc)}


def _parse_tool_result(result: Any) -> Any:
    """Normalize MCP CallToolResult into Python data."""
    if result is None:
        return None

    if getattr(result, "isError", False):
        texts = []
        for block in getattr(result, "content", []) or []:
            texts.append(getattr(block, "text", str(block)))
        raise RuntimeError("; ".join(texts) or "MCP tool error")

    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured

    contents = getattr(result, "content", None) or []
    texts: list[str] = []
    for block in contents:
        text = getattr(block, "text", None)
        if text is not None:
            texts.append(text)

    if not texts:
        return None
    if len(texts) == 1:
        return _maybe_json(texts[0])
    return [_maybe_json(t) for t in texts]


def _maybe_json(text: str) -> Any:
    text = text.strip()
    if not text:
        return text
    if text[0] in "{[":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


def unwrap_evaluate(result: Any) -> Any:
    """miniapp_evaluate often returns {value, type}."""
    if isinstance(result, dict) and "value" in result and set(result.keys()) <= {
        "value",
        "type",
        "error",
        "exception",
    }:
        return result["value"]
    return result
