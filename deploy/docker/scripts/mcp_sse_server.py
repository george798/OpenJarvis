#!/usr/bin/env python3
"""MCP SSE bridge exposing OpenJarvis tools to Cursor (port 8888 /sse)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("openjarvis.mcp_sse")

# OpenJarvis internal MCP server (JSON-RPC tools)
from openjarvis.mcp.protocol import MCPRequest
from openjarvis.mcp.server import MCPServer

_internal = MCPServer()
_mcp = Server("openjarvis")
_sse = SseServerTransport("/messages/")


def _call_internal(method: str, params: dict[str, Any] | None, req_id: int | str) -> Any:
    request = MCPRequest(method=method, params=params or {}, id=req_id)
    response = _internal.handle(request)
    if response.error:
        raise RuntimeError(response.error.get("message", "MCP tool error"))
    return response.result or {}


@_mcp.list_tools()
async def list_tools() -> list[Tool]:
    result = _call_internal("tools/list", {}, 1)
    tools: list[Tool] = []
    for spec in result.get("tools", []):
        tools.append(
            Tool(
                name=spec["name"],
                description=spec.get("description", ""),
                inputSchema=spec.get("inputSchema", {"type": "object", "properties": {}}),
            )
        )
    return tools


@_mcp.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    result = _call_internal(
        "tools/call",
        {"name": name, "arguments": arguments},
        2,
    )
    content_blocks = result.get("content", [])
    if isinstance(content_blocks, list) and content_blocks:
        texts = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif isinstance(block, str):
                texts.append(block)
        if texts:
            return [TextContent(type="text", text="\n".join(texts))]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_sse(request: Request) -> Response:
    async with _sse.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await _mcp.run(
            streams[0],
            streams[1],
            _mcp.create_initialization_options(),
        )
    return Response()


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "openjarvis-mcp"})


app = Starlette(
    routes=[
        Route("/health", endpoint=health, methods=["GET"]),
        Route("/sse", endpoint=_handle_sse, methods=["GET"]),
        Mount("/messages", app=_sse.handle_post_message),
    ]
)


if __name__ == "__main__":
    logger.info("OpenJarvis MCP SSE listening on http://0.0.0.0:8888/sse")
    uvicorn.run(app, host="0.0.0.0", port=8888, log_level="info")
