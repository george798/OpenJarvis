"""Shared helper for loading MCP server tools from a TOML config blob.

Used by ``cli/ask.py``, ``cli/serve.py``, ``system/builder.py`` and
``server/agent_manager_routes.py`` so each call site doesn't reimplement
the server-config → transport → client → discovered-tools pipeline.

The returned tuple of ``(tools, clients)`` is load-bearing: the caller
MUST hold a reference to ``clients`` for as long as the tools are used,
otherwise the MCP transport sessions get garbage-collected and the
underlying HTTP connections close mid-execution (see #461 adversarial
review). The recommended pattern is to stash the client list on the
agent so they share its lifetime:

    tools, mcp_clients = load_mcp_tools_from_config(config.tools.mcp)
    agent = AgentCls(tools=tools, ...)
    agent._mcp_clients = mcp_clients   # keep transports alive
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any, Optional

_ENV_REF = re.compile(r"\$\{([^}]+)\}")

if TYPE_CHECKING:
    from openjarvis.core.types import ToolSpec  # noqa: F401
    from openjarvis.mcp.client import MCPClient
    from openjarvis.tools._stubs import BaseTool

logger = logging.getLogger(__name__)


def _resolve_env(value: str) -> str:
    """Expand ``${VAR}`` placeholders from the process environment."""
    return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), value)


def _resolve_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    resolved: dict[str, str] = {}
    for key, value in headers.items():
        if isinstance(value, str):
            resolved[str(key)] = _resolve_env(value)
    return resolved


def load_mcp_tools_from_config(
    mcp_cfg: Any,
    *,
    allowed_names: Optional[set[str]] = None,
) -> tuple[list["BaseTool"], list["MCPClient"]]:
    """Load tools from every server in ``mcp_cfg.servers``.

    Returns ``(tools, clients)``. ``clients`` is the list of live
    ``MCPClient`` instances — keep a reference or the transports get
    GC'd. Failures in any single server are logged and that server is
    skipped; the rest are returned as a best-effort batch.

    ``allowed_names`` is an outer filter applied after each server's
    own include/exclude filter. Pass the caller's `--tools`/`enabled`
    list to honour CLI scoping; pass ``None`` to take every tool.

    Returns ``([], [])`` when mcp is disabled or no servers are
    configured — no exception, no warning.
    """
    # MCP headers often reference ${COMPOSIO_API_KEY} etc. Ensure vault
    # secrets are in os.environ before resolving (jarvis ask skips serve's
    # startup injection).
    try:
        from openjarvis.core.secret_vault import inject_vault_into_environ

        inject_vault_into_environ()
    except Exception:
        pass

    # ``enabled`` and ``servers`` come from openjarvis.core.config's
    # MCPConfig dataclass; accept duck-typed equivalents for tests.
    enabled = getattr(mcp_cfg, "enabled", False)
    servers_blob = getattr(mcp_cfg, "servers", None)
    if not enabled or not servers_blob:
        return [], []

    try:
        server_list = (
            json.loads(servers_blob) if isinstance(servers_blob, str) else servers_blob
        )
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Failed to parse MCP servers config: %s", exc)
        return [], []
    if not isinstance(server_list, list):
        logger.warning(
            "MCP servers config is not a list (got %r) — skipping MCP discovery",
            type(server_list).__name__,
        )
        return [], []

    # Imported lazily so that `openjarvis.mcp.loader` can be imported
    # cheaply from CLI startup paths without dragging in the heavy MCP
    # client stack until something actually wants to discover tools.
    from openjarvis.mcp.client import MCPClient
    from openjarvis.mcp.transport import StdioTransport, StreamableHTTPTransport
    from openjarvis.tools.mcp_adapter import MCPToolProvider

    tools: list["BaseTool"] = []
    clients: list["MCPClient"] = []

    for server_cfg in server_list:
        cfg: dict[str, Any] | None = None
        try:
            cfg = (
                json.loads(server_cfg) if isinstance(server_cfg, str) else server_cfg
            )
            name = cfg.get("name", "<unnamed>")
            url = cfg.get("url")
            token = cfg.get("token")
            headers = _resolve_headers(cfg.get("headers"))
            command = cfg.get("command", "")
            args = cfg.get("args", [])

            def _connect_and_discover(server: dict[str, Any]) -> list["BaseTool"]:
                server_url = server.get("url")
                server_token = server.get("token")
                server_headers = _resolve_headers(server.get("headers"))
                server_command = server.get("command", "")
                server_args = server.get("args", [])

                if server_url:
                    transport = StreamableHTTPTransport(
                        url=server_url,
                        token=server_token,
                        headers=server_headers or None,
                    )
                elif server_command:
                    transport = StdioTransport(
                        command=[server_command] + server_args
                    )
                else:
                    raise ValueError(
                        f"MCP server '{server.get('name', '<unnamed>')}' has neither "
                        "'url' nor 'command'"
                    )

                client = MCPClient(transport)
                client.initialize()
                clients.append(client)

                provider = MCPToolProvider(client)
                discovered = provider.discover()

                include_tools = set(server.get("include_tools", []))
                exclude_tools = set(server.get("exclude_tools", []))
                if include_tools:
                    discovered = [
                        t for t in discovered if t.spec.name in include_tools
                    ]
                if exclude_tools:
                    discovered = [
                        t for t in discovered if t.spec.name not in exclude_tools
                    ]
                if allowed_names:
                    discovered = [
                        t for t in discovered if t.spec.name in allowed_names
                    ]
                return discovered

            try:
                discovered = _connect_and_discover(cfg)
            except Exception as first_exc:
                err = str(first_exc)
                is_composio = (
                    "composio" in name.lower()
                    or "tool_router" in str(url or "")
                )
                if is_composio and "401" in err:
                    logger.warning(
                        "Composio MCP server '%s' returned 401 — refreshing session",
                        name,
                    )
                    from openjarvis.tools.mcp_manage import refresh_composio_server_config

                    refreshed = refresh_composio_server_config(name)
                    if refreshed:
                        cfg = refreshed
                        discovered = _connect_and_discover(cfg)
                    else:
                        raise first_exc
                else:
                    raise first_exc

            tools.extend(discovered)
            logger.info(
                "Discovered %d MCP tools from server '%s'", len(discovered), name
            )
        except Exception as exc:  # per-server isolation
            logger.warning(
                "Failed to discover MCP tools from '%s': %s",
                cfg.get("name", "<unnamed>") if cfg else "<unparsed>",
                exc,
            )
            continue

    return tools, clients
