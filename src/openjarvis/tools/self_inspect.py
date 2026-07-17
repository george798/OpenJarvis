"""self_inspect — let the brain query its own CapabilityIndex."""

from __future__ import annotations

import json
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


@ToolRegistry.register("self_inspect")
class SelfInspectTool(BaseTool):
    """Query the live map of OpenJarvis capabilities and state."""

    tool_id = "self_inspect"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="self_inspect",
            description=(
                "Inspect your own OpenJarvis body: tools (registry vs enabled), "
                "MCP servers, connected data sources and knowledge.db corpus "
                "counts, managed long-running agents, memory layers, Obsidian "
                "vault status, channels, and engine/model. Actions: 'summary' "
                "(compact overview), 'full' (complete manifest), or a section "
                "name: tools, mcp, connectors, knowledge, memory, vault, "
                "agents, channels, engine, self_modification."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "summary",
                            "full",
                            "tools",
                            "mcp",
                            "connectors",
                            "knowledge",
                            "memory",
                            "vault",
                            "agents",
                            "channels",
                            "engine",
                            "self_modification",
                        ],
                        "description": "What to inspect (default: summary).",
                    },
                },
            },
            category="self",
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.core.capabilities import build_capability_index

        action = (params.get("action") or "summary").strip().lower()
        index = build_capability_index(
            getattr(self, "_app_state", None)
        )

        section_map = {
            "tools": "tools",
            "mcp": "mcp_servers",
            "connectors": "connectors",
            "knowledge": "knowledge",
            "memory": "memory",
            "vault": "vault",
            "agents": "managed_agents",
            "channels": "channels",
            "engine": "engine",
            "self_modification": "self_modification",
        }

        if action == "full":
            payload = index
        elif action == "summary":
            payload = {
                "summary": index.get("summary"),
                "engine": index.get("engine"),
                "self_modification": index.get("self_modification"),
                "hint": (
                    "Call again with action=full or a section name "
                    "(tools, mcp, connectors, knowledge, memory, vault, "
                    "agents) for details."
                ),
            }
        elif action in section_map:
            key = section_map[action]
            payload = {key: index.get(key)}
        else:
            return ToolResult(
                tool_name="self_inspect",
                content=f"Unknown action: {action}",
                success=False,
            )

        return ToolResult(
            tool_name="self_inspect",
            content=json.dumps(payload, indent=2, default=str),
            success=True,
        )


__all__ = ["SelfInspectTool"]
