"""managed_agent_manage — create/pause/resume/reschedule long-running agents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


def _manager():
    """Open the persistent AgentManager from the configured agents DB."""
    from openjarvis.agents.manager import AgentManager
    from openjarvis.core.config import load_config

    cfg = load_config()
    db = getattr(getattr(cfg, "agent_manager", None), "db_path", "") or (
        "~/.openjarvis/agents.db"
    )
    return AgentManager(str(Path(db).expanduser()))


def _public_agent(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": a.get("id"),
        "name": a.get("name"),
        "agent_type": a.get("agent_type"),
        "status": a.get("status"),
        "schedule_type": a.get("schedule_type"),
        "schedule_value": a.get("schedule_value"),
        "last_run_at": a.get("last_run_at"),
        "total_runs": a.get("total_runs"),
        "current_activity": a.get("current_activity"),
        "tools": (a.get("config") or {}).get("tools"),
        "instruction": (a.get("config") or {}).get("instruction"),
    }


@ToolRegistry.register("managed_agent_manage")
class ManagedAgentManageTool(BaseTool):
    """List / create / pause / resume / reschedule persistent managed agents."""

    tool_id = "managed_agent_manage"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="managed_agent_manage",
            description=(
                "Manage long-running Agents (the ones on the Agents page with "
                "cron/interval schedules). Actions: 'list', 'get', 'create', "
                "'pause', 'resume', 'update' (rename / change schedule / tools / "
                "instruction), 'archive'. Prefer this over agent_list/agent_spawn "
                "when the user means persistent scheduled agents."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list",
                            "get",
                            "create",
                            "pause",
                            "resume",
                            "update",
                            "archive",
                        ],
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Required for get/pause/resume/update/archive.",
                    },
                    "name": {"type": "string"},
                    "agent_type": {
                        "type": "string",
                        "description": "e.g. orchestrator, monitor_operative, deep_research",
                    },
                    "schedule_type": {
                        "type": "string",
                        "enum": ["manual", "cron", "interval"],
                    },
                    "schedule_value": {
                        "type": "string",
                        "description": "Cron expr or interval seconds as string.",
                    },
                    "tools": {
                        "type": "string",
                        "description": "Comma-separated tool names for the agent.",
                    },
                    "instruction": {"type": "string"},
                    "system_prompt": {"type": "string"},
                    "model": {"type": "string"},
                    "max_turns": {"type": "integer"},
                },
                "required": ["action"],
            },
            category="agents",
            required_capabilities=["system:admin"],
        )

    def execute(self, **params: Any) -> ToolResult:
        action = (params.get("action") or "").strip().lower()
        try:
            mgr = _manager()
        except Exception as exc:
            return ToolResult(
                tool_name="managed_agent_manage",
                content=f"Cannot open AgentManager: {exc}",
                success=False,
            )

        try:
            if action == "list":
                agents = [_public_agent(a) for a in mgr.list_agents()]
                return ToolResult(
                    tool_name="managed_agent_manage",
                    content=json.dumps({"agents": agents}, indent=2, default=str),
                    success=True,
                )

            if action == "get":
                agent_id = params.get("agent_id")
                if not agent_id:
                    return self._fail("agent_id required")
                a = mgr.get_agent(agent_id)
                if not a:
                    return self._fail(f"Agent not found: {agent_id}")
                return ToolResult(
                    tool_name="managed_agent_manage",
                    content=json.dumps(_public_agent(a), indent=2, default=str),
                    success=True,
                )

            if action == "create":
                name = (params.get("name") or "").strip()
                if not name:
                    return self._fail("name required for create")
                config: Dict[str, Any] = {}
                if params.get("schedule_type"):
                    config["schedule_type"] = params["schedule_type"]
                if params.get("schedule_value") is not None:
                    config["schedule_value"] = str(params["schedule_value"])
                if params.get("tools"):
                    tools = params["tools"]
                    if isinstance(tools, str):
                        tools = [t.strip() for t in tools.split(",") if t.strip()]
                    config["tools"] = tools
                for key in ("instruction", "system_prompt", "model"):
                    if params.get(key):
                        config[key] = params[key]
                if params.get("max_turns") is not None:
                    config["max_turns"] = int(params["max_turns"])
                created = mgr.create_agent(
                    name=name,
                    agent_type=params.get("agent_type") or "orchestrator",
                    config=config,
                )
                return ToolResult(
                    tool_name="managed_agent_manage",
                    content=json.dumps(
                        {"created": _public_agent(created)}, indent=2, default=str
                    ),
                    success=True,
                )

            if action in ("pause", "resume", "archive", "update"):
                agent_id = params.get("agent_id")
                if not agent_id:
                    return self._fail("agent_id required")
                a = mgr.get_agent(agent_id)
                if not a:
                    return self._fail(f"Agent not found: {agent_id}")

                if action == "pause":
                    mgr.pause_agent(agent_id)
                elif action == "resume":
                    mgr.resume_agent(agent_id)
                elif action == "archive":
                    mgr.delete_agent(agent_id)
                elif action == "update":
                    config = dict(a.get("config") or {})
                    if params.get("schedule_type"):
                        config["schedule_type"] = params["schedule_type"]
                    if params.get("schedule_value") is not None:
                        config["schedule_value"] = str(params["schedule_value"])
                    if params.get("tools") is not None:
                        tools = params["tools"]
                        if isinstance(tools, str):
                            tools = [
                                t.strip() for t in tools.split(",") if t.strip()
                            ]
                        config["tools"] = tools
                    for key in ("instruction", "system_prompt", "model"):
                        if params.get(key) is not None:
                            config[key] = params[key]
                    if params.get("max_turns") is not None:
                        config["max_turns"] = int(params["max_turns"])
                    kwargs: Dict[str, Any] = {"config": config}
                    if params.get("name"):
                        kwargs["name"] = params["name"]
                    if params.get("agent_type"):
                        kwargs["agent_type"] = params["agent_type"]
                    mgr.update_agent(agent_id, **kwargs)

                updated = mgr.get_agent(agent_id)
                return ToolResult(
                    tool_name="managed_agent_manage",
                    content=json.dumps(
                        {
                            "action": action,
                            "agent": _public_agent(updated) if updated else None,
                        },
                        indent=2,
                        default=str,
                    ),
                    success=True,
                )

            return self._fail(f"Unknown action: {action}")
        except Exception as exc:
            return ToolResult(
                tool_name="managed_agent_manage",
                content=f"managed_agent_manage failed: {exc}",
                success=False,
            )

    def _fail(self, msg: str) -> ToolResult:
        return ToolResult(
            tool_name="managed_agent_manage",
            content=msg,
            success=False,
        )


__all__ = ["ManagedAgentManageTool"]
