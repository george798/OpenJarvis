"""Manage external MCP servers in config.toml.

Unlike config_manage (scalar keys only), this tool understands the JSON
``[tools.mcp].servers`` array: list/add/remove HTTP or stdio servers, probe
connectivity, and wire Composio toolkit MCP endpoints (e.g. Reddit).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import requests

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

_RESTART_HINT = (
    "Restart required for new MCP tools to load. Run host_exec with: "
    "docker compose -f D:\\OpenJarvis\\compose\\docker-compose.yml restart jarvis "
    "(~1 minute downtime). Also ensure 'mcp:*' is in [agent].tools so discovered "
    "MCP tool names are not filtered out at startup."
)

_COMPOSIO_API_BASE = "https://backend.composio.dev/api/v3.1"


def _config_path() -> Path:
    from openjarvis.core.config import DEFAULT_CONFIG_PATH

    return Path(os.environ.get("OPENJARVIS_CONFIG", DEFAULT_CONFIG_PATH)).expanduser()


def _load_servers_doc() -> tuple[Any, list[dict[str, Any]]]:
    import tomlkit

    path = _config_path()
    if path.exists():
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    else:
        doc = tomlkit.document()
        path.parent.mkdir(parents=True, exist_ok=True)

    tools = doc.get("tools")
    if tools is None:
        tools = doc.add("tools", tomlkit.table())
    mcp = tools.get("mcp")
    if mcp is None:
        mcp = tools.add("mcp", tomlkit.table())
    if mcp.get("enabled") is None:
        mcp["enabled"] = True

    raw = mcp.get("servers", "[]")
    if isinstance(raw, list):
        servers = [s for s in raw if isinstance(s, dict)]
    else:
        try:
            parsed = json.loads(str(raw))
            servers = [s for s in parsed if isinstance(s, dict)] if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            servers = []
    return doc, servers


def _save_servers(doc: Any, servers: list[dict[str, Any]]) -> Path:
    path = _config_path()
    if path.exists():
        shutil.copy2(path, path.with_suffix(".toml.bak"))
    tools = doc["tools"]
    mcp = tools["mcp"]
    mcp["servers"] = json.dumps(servers, separators=(",", ":"))
    path.write_text(__import__("tomlkit").dumps(doc), encoding="utf-8")
    try:
        from openjarvis.core.config import load_config

        load_config.cache_clear()
    except Exception:
        pass
    return path


def _composio_api_key() -> str:
    key = (
        os.environ.get("COMPOSIO_API_KEY", "").strip()
        or os.environ.get("COMPOSIO_PLATFORM_API_KEY", "").strip()
    )
    if key:
        return key
    try:
        from openjarvis.core import secret_vault as vault

        data = vault.load_vault()
        for name in ("COMPOSIO_API_KEY", "composio_api_key", "COMPOSIO_PLATFORM_API_KEY"):
            if data.get(name, "").strip():
                return data[name].strip()
    except Exception:
        pass
    return ""


def _composio_user_id() -> str:
    return os.environ.get("COMPOSIO_USER_ID", "jarvis").strip() or "jarvis"


def _composio_headers() -> dict[str, str]:
    key = _composio_api_key()
    if not key:
        raise ValueError(
            "COMPOSIO_API_KEY is not set. Store it with credential_manage "
            "(name COMPOSIO_API_KEY) and add COMPOSIO_API_KEY=${COMPOSIO_API_KEY} "
            "to compose/.env, then restart jarvis."
        )
    return {"x-api-key": key, "Content-Type": "application/json"}


def _composio_list_servers(*, toolkits: str = "", name: str = "") -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": 50}
    if toolkits:
        params["toolkits"] = toolkits
    if name:
        params["name"] = name
    resp = requests.get(
        f"{_COMPOSIO_API_BASE}/mcp/servers",
        headers=_composio_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        items = data.get("items") or data.get("data") or data.get("servers") or []
        if isinstance(items, dict):
            items = items.get("items", [])
        return [i for i in items if isinstance(i, dict)]
    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]
    return []


def _composio_create_session(*, toolkits: list[str] | None = None) -> dict[str, Any]:
    """Create a Composio tool-router session and return the JSON body."""
    body: dict[str, Any] = {
        "user_id": _composio_user_id(),
        "manage_connections": {"enable": True, "enable_wait_for_connections": True},
    }
    if toolkits:
        body["toolkits"] = {"enable": toolkits}
    resp = requests.post(
        f"{_COMPOSIO_API_BASE}/tool_router/session",
        headers=_composio_headers(),
        json=body,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected session response: {data!r}")
    return data


def _composio_session_mcp_url(session: dict[str, Any]) -> str:
    mcp = session.get("mcp") or {}
    url = mcp.get("url")
    if isinstance(url, str) and url.startswith("http"):
        return url
    session_id = session.get("session_id") or session.get("id")
    if session_id:
        return f"https://backend.composio.dev/tool_router/{session_id}/mcp"
    raise ValueError(f"Could not resolve MCP URL from session keys: {sorted(session.keys())}")
def _composio_mcp_url(server: dict[str, Any]) -> str:
    """Resolve the Streamable HTTP MCP URL for a legacy Composio server record."""
    user_id = _composio_user_id()
    for key in ("mcp_url", "url", "server_url"):
        raw = server.get(key)
        if isinstance(raw, str) and raw.startswith("http"):
            if "user_id=" in raw:
                return raw
            sep = "&" if "?" in raw else "?"
            return f"{raw}{sep}user_id={user_id}"

    server_id = server.get("id") or server.get("server_id") or server.get("uuid")
    if server_id:
        return f"https://backend.composio.dev/v3/mcp/{server_id}?user_id={user_id}"

    raise ValueError(
        f"Could not resolve MCP URL from Composio server record keys: {sorted(server.keys())}"
    )


def refresh_composio_server_config(
    name: str = "composio-reddit",
    *,
    toolkit: str = "reddit",
) -> dict[str, Any] | None:
    """Mint a fresh Composio tool-router session and persist it to config.toml.

    Returns the updated server entry on success, else None.
    """
    name = (name or "composio-reddit").strip()
    toolkit = (toolkit or "reddit").strip()
    try:
        from openjarvis.core.secret_vault import inject_vault_into_environ

        inject_vault_into_environ()
        session = _composio_create_session(toolkits=[toolkit])
        mcp_url = _composio_session_mcp_url(session)
        entry: dict[str, Any] = {
            "name": name,
            "url": mcp_url,
            "headers": {"x-api-key": "${COMPOSIO_API_KEY}"},
        }
        doc, servers = _load_servers_doc()
        replaced = False
        for i, s in enumerate(servers):
            if s.get("name") == name:
                servers[i] = entry
                replaced = True
                break
        if not replaced:
            servers.append(entry)
        _save_servers(doc, servers)
        probe = _test_mcp_server(entry)
        if not probe.get("ok"):
            return None
        return entry
    except Exception:
        return None


def _test_mcp_server(cfg: dict[str, Any]) -> dict[str, Any]:
    from openjarvis.mcp.client import MCPClient
    from openjarvis.mcp.loader import _resolve_headers
    from openjarvis.mcp.transport import StdioTransport, StreamableHTTPTransport
    from openjarvis.tools.mcp_adapter import MCPToolProvider

    url = cfg.get("url")
    command = cfg.get("command", "")
    if url:
        transport = StreamableHTTPTransport(
            url=str(url),
            token=cfg.get("token"),
            headers=_resolve_headers(cfg.get("headers")) or None,
        )
    elif command:
        args = cfg.get("args") or []
        if not isinstance(args, list):
            args = [str(args)]
        transport = StdioTransport(command=[str(command)] + [str(a) for a in args])
    else:
        return {"ok": False, "error": "Server has neither url nor command"}

    client = MCPClient(transport)
    try:
        client.initialize()
        tools = MCPToolProvider(client).discover()
        names = [t.spec.name for t in tools]
        return {"ok": True, "tool_count": len(names), "tools": names[:40]}
    finally:
        transport.close()


@ToolRegistry.register("mcp_manage")
class MCPManageTool(BaseTool):
    """Add, remove, list, test, and connect Composio MCP servers."""

    tool_id = "mcp_manage"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="mcp_manage",
            description=(
                "Manage external MCP servers in config.toml ([tools.mcp].servers). "
                "Actions: list (configured servers), add_http (name+url+optional headers), "
                "add_stdio (name+command+args), remove (by name), test (probe one server), "
                "composio_list (list Composio MCP servers via API), composio_connect "
                "(add a Composio server — default toolkit reddit). After changes, restart "
                "jarvis. Ensure mcp:* is in [agent].tools."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list",
                            "add_http",
                            "add_stdio",
                            "remove",
                            "test",
                            "composio_list",
                            "composio_connect",
                        ],
                    },
                    "name": {"type": "string", "description": "Server name (unique id)."},
                    "url": {"type": "string", "description": "HTTP MCP endpoint URL."},
                    "command": {"type": "string", "description": "Stdio MCP command."},
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Stdio command arguments.",
                    },
                    "headers": {
                        "type": "object",
                        "description": "HTTP headers (values may use ${ENV_VAR}).",
                    },
                    "token": {"type": "string", "description": "Bearer token for HTTP MCP."},
                    "include_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Whitelist tool names from this server.",
                    },
                    "exclude_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Blacklist tool names from this server.",
                    },
                    "toolkit": {
                        "type": "string",
                        "description": "Composio toolkit slug for composio_* actions (default reddit).",
                    },
                    "server_name": {
                        "type": "string",
                        "description": "Filter Composio servers by name (partial match).",
                    },
                },
                "required": ["action"],
            },
            category="system",
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "list")
        try:
            if action == "list":
                return self._list()
            if action == "add_http":
                return self._add_http(params)
            if action == "add_stdio":
                return self._add_stdio(params)
            if action == "remove":
                return self._remove(params.get("name", ""))
            if action == "test":
                return self._test(params.get("name", ""))
            if action == "composio_list":
                return self._composio_list(params)
            if action == "composio_connect":
                return self._composio_connect(params)
        except Exception as exc:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content=f"mcp_manage {action} failed: {exc}",
            )
        return ToolResult(
            tool_name=self.spec.name,
            success=False,
            content=f"Unknown action: {action}",
        )

    def _list(self) -> ToolResult:
        _, servers = _load_servers_doc()
        lines = [f"Configured MCP servers ({len(servers)}):"]
        for s in servers:
            kind = "http" if s.get("url") else "stdio"
            extra = s.get("url") or s.get("command", "")
            inc = s.get("include_tools")
            lines.append(
                f"- {s.get('name', '?')} ({kind}): {extra}"
                + (f" include_tools={inc}" if inc else "")
            )
        lines.append(f"\n{_RESTART_HINT}")
        return ToolResult(tool_name=self.spec.name, success=True, content="\n".join(lines))

    def _upsert(self, entry: dict[str, Any]) -> ToolResult:
        name = entry.get("name")
        if not name:
            return ToolResult(
                tool_name=self.spec.name, success=False, content="'name' is required."
            )
        doc, servers = _load_servers_doc()
        replaced = False
        for i, s in enumerate(servers):
            if s.get("name") == name:
                servers[i] = entry
                replaced = True
                break
        if not replaced:
            servers.append(entry)
        path = _save_servers(doc, servers)
        verb = "Updated" if replaced else "Added"
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            content=f"{verb} MCP server {name!r}. Saved to {path.name}. {_RESTART_HINT}",
        )

    def _add_http(self, params: dict[str, Any]) -> ToolResult:
        name = (params.get("name") or "").strip()
        url = (params.get("url") or "").strip()
        if not name or not url:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content="add_http requires 'name' and 'url'.",
            )
        entry: dict[str, Any] = {"name": name, "url": url}
        headers = params.get("headers")
        if isinstance(headers, dict) and headers:
            entry["headers"] = headers
        token = params.get("token")
        if token:
            entry["token"] = str(token)
        for key in ("include_tools", "exclude_tools"):
            val = params.get(key)
            if isinstance(val, list) and val:
                entry[key] = [str(v) for v in val]
        return self._upsert(entry)

    def _add_stdio(self, params: dict[str, Any]) -> ToolResult:
        name = (params.get("name") or "").strip()
        command = (params.get("command") or "").strip()
        if not name or not command:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content="add_stdio requires 'name' and 'command'.",
            )
        args = params.get("args") or []
        if not isinstance(args, list):
            args = [str(args)]
        entry: dict[str, Any] = {"name": name, "command": command, "args": [str(a) for a in args]}
        for key in ("include_tools", "exclude_tools"):
            val = params.get(key)
            if isinstance(val, list) and val:
                entry[key] = [str(v) for v in val]
        return self._upsert(entry)

    def _remove(self, name: str) -> ToolResult:
        name = (name or "").strip()
        if not name:
            return ToolResult(
                tool_name=self.spec.name, success=False, content="'name' is required for remove."
            )
        doc, servers = _load_servers_doc()
        new_servers = [s for s in servers if s.get("name") != name]
        if len(new_servers) == len(servers):
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content=f"No MCP server named {name!r}.",
            )
        path = _save_servers(doc, new_servers)
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            content=f"Removed MCP server {name!r}. Saved to {path.name}. {_RESTART_HINT}",
        )

    def _test(self, name: str) -> ToolResult:
        name = (name or "").strip()
        if not name:
            return ToolResult(
                tool_name=self.spec.name, success=False, content="'name' is required for test."
            )
        _, servers = _load_servers_doc()
        cfg = next((s for s in servers if s.get("name") == name), None)
        if cfg is None:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content=f"No MCP server named {name!r}.",
            )
        result = _test_mcp_server(cfg)
        ok = result.get("ok", False)
        body = json.dumps(result, indent=2)
        return ToolResult(tool_name=self.spec.name, success=ok, content=body)

    def _composio_list(self, params: dict[str, Any]) -> ToolResult:
        toolkit = (params.get("toolkit") or "").strip()
        server_name = (params.get("server_name") or "").strip()
        servers = _composio_list_servers(toolkits=toolkit, name=server_name)
        if not servers:
            return ToolResult(
                tool_name=self.spec.name,
                success=True,
                content=(
                    "No Composio MCP servers found"
                    + (f" for toolkit={toolkit!r}" if toolkit else "")
                    + ". Create one at https://platform.composio.dev (MCP → add Reddit toolkit)."
                ),
            )
        lines = [f"Composio MCP servers ({len(servers)}):"]
        for s in servers:
            sid = s.get("id") or s.get("server_id") or "?"
            lines.append(
                f"- {s.get('name', '?')} id={sid} toolkits={s.get('toolkits') or s.get('toolkit')}"
            )
        return ToolResult(tool_name=self.spec.name, success=True, content="\n".join(lines))

    def _composio_connect(self, params: dict[str, Any]) -> ToolResult:
        toolkit = (params.get("toolkit") or "reddit").strip()
        server_name = (params.get("server_name") or "").strip()
        local_name = (params.get("name") or f"composio-{toolkit}").strip()

        # Legacy MCP server API (empty on new Composio projects — use sessions).
        servers = _composio_list_servers(toolkits=toolkit, name=server_name)
        mcp_url = ""
        picked_name = ""
        include_tools: list[str] | None = None

        if servers:
            picked = servers[0]
            if server_name:
                for s in servers:
                    if server_name.lower() in str(s.get("name", "")).lower():
                        picked = s
                        break
            picked_name = str(picked.get("name", ""))
            mcp_url = _composio_mcp_url(picked)
            allowed = picked.get("allowed_tools") or []
            if isinstance(allowed, list) and allowed:
                include_tools = [
                    str(t)
                    for t in allowed
                    if str(t).upper().startswith("REDDIT")
                    or toolkit.upper() in str(t).upper()
                ]
                if not include_tools:
                    include_tools = [str(t) for t in allowed[:30]]
        else:
            session = _composio_create_session(toolkits=[toolkit])
            mcp_url = _composio_session_mcp_url(session)
            picked_name = f"tool_router session {session.get('session_id', '?')}"

        entry: dict[str, Any] = {
            "name": local_name,
            "url": mcp_url,
            "headers": {"x-api-key": "${COMPOSIO_API_KEY}"},
        }
        if include_tools:
            entry["include_tools"] = include_tools

        result = self._upsert(entry)
        if not result.success:
            return result

        probe = _test_mcp_server(entry)
        probe_text = json.dumps(probe, indent=2)
        return ToolResult(
            tool_name=self.spec.name,
            success=probe.get("ok", False),
            content=(
                f"Connected Composio {picked_name!r} as {local_name!r}.\n"
                f"URL: {mcp_url}\n"
                f"Probe: {probe_text}\n\n{_RESTART_HINT}\n"
                "If Reddit auth is required, complete OAuth via Composio's connection "
                "manager tools in chat, or connect Reddit in the Composio dashboard for "
                f"user_id={_composio_user_id()}."
            ),
        )


__all__ = ["MCPManageTool", "refresh_composio_server_config"]
