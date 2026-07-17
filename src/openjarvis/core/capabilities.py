"""CapabilityIndex — live map of what OpenJarvis can do and what it knows.

Single source of truth for the brain (``self_inspect`` tool), the API
(``GET /v1/capabilities``), the system-prompt status block, and the web
System Map. Aggregates tools, MCP servers, connectors, knowledge corpus,
managed agents, memory layers, channels, vault, and engine/model.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        logger.debug("CapabilityIndex probe failed", exc_info=True)
        return default


def _config_path() -> Path:
    from openjarvis.core.config import DEFAULT_CONFIG_PATH

    return Path(os.environ.get("OPENJARVIS_CONFIG", DEFAULT_CONFIG_PATH)).expanduser()


def _parse_mcp_servers() -> List[Dict[str, Any]]:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

    path = _config_path()
    if not path.exists():
        return []
    with open(path, "rb") as fh:
        raw = tomllib.load(fh).get("tools", {}).get("mcp", {})
    servers = raw.get("servers", "[]")
    if isinstance(servers, str):
        try:
            servers = json.loads(servers) if servers.strip() else []
        except json.JSONDecodeError:
            return []
    if not isinstance(servers, list):
        return []
    out: List[Dict[str, Any]] = []
    for s in servers:
        if not isinstance(s, dict):
            continue
        out.append(
            {
                "name": s.get("name", "?"),
                "kind": "http" if s.get("url") else "stdio",
                "url": s.get("url") or None,
                "command": s.get("command") or None,
            }
        )
    return out


def _tool_catalog() -> Dict[str, Any]:
    from openjarvis.core.registry import ToolRegistry

    try:
        import openjarvis.tools  # noqa: F401
    except Exception:
        pass

    names = sorted(ToolRegistry.keys()) if hasattr(ToolRegistry, "keys") else []
    if not names:
        try:
            names = sorted(n for n, _ in ToolRegistry.items())
        except Exception:
            names = []

    enabled_raw = ""
    try:
        from openjarvis.core.config import load_config

        cfg = load_config()
        enabled_raw = getattr(cfg.agent, "tools", "") or getattr(
            cfg.tools, "enabled", ""
        )
    except Exception:
        pass
    enabled = {t.strip() for t in enabled_raw.split(",") if t.strip()}
    want_all_mcp = "mcp:*" in enabled or "*" in enabled

    return {
        "registry_count": len(names),
        "registry": names,
        "enabled": sorted(enabled),
        "enabled_count": len(enabled),
        "mcp_wildcard": want_all_mcp,
    }


def _connector_status() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    try:
        from openjarvis.core.registry import ConnectorRegistry
        import openjarvis.connectors  # noqa: F401

        instances: Dict[str, Any] = {}
        try:
            from openjarvis.server import connectors_router

            instances = dict(getattr(connectors_router, "_instances", {}) or {})
        except Exception:
            pass

        for cid, _cls in ConnectorRegistry.items():
            inst = instances.get(cid)
            connected = bool(inst and getattr(inst, "is_connected", lambda: False)())
            status: Dict[str, Any] = {
                "id": cid,
                "connected": connected,
            }
            if inst is not None and hasattr(inst, "sync_status"):
                try:
                    ss = inst.sync_status()
                    status["items_synced"] = getattr(ss, "items_synced", None)
                    status["items_total"] = getattr(ss, "items_total", None)
                    status["state"] = getattr(ss, "state", None)
                except Exception:
                    pass
            items.append(status)
    except Exception:
        logger.debug("Connector status probe failed", exc_info=True)
    return sorted(items, key=lambda x: x["id"])


def _knowledge_stats() -> Dict[str, Any]:
    try:
        from openjarvis.connectors.store import KnowledgeStore

        store = KnowledgeStore()
        try:
            total = store.count()
            sources = store.distinct_sources()
            by_source: Dict[str, int] = {}
            for src in sources:
                row = store._conn.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks "
                    "WHERE source = ? AND deleted_at IS NULL",
                    (src,),
                ).fetchone()
                by_source[src] = int(row[0]) if row else 0
            return {
                "chunk_count": total,
                "sources": by_source,
                "source_count": len(sources),
            }
        finally:
            store.close()
    except Exception:
        logger.debug("Knowledge stats probe failed", exc_info=True)
        return {"chunk_count": 0, "sources": {}, "source_count": 0}


def _memory_stats() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "layers": {
            "fact": {"store": "memory.db", "injection": "auto_rag"},
            "preference": {"store": "USER.md", "injection": "system_prompt"},
            "rule": {"store": "MEMORY.md", "injection": "system_prompt"},
            "note": {
                "store": "vault Notes/ + knowledge.db",
                "injection": "tool_or_unified_rag",
            },
        }
    }
    try:
        from openjarvis.core.config import load_config

        cfg = load_config()
        mf = cfg.memory_files
        out["persona"] = getattr(mf, "persona_name", "") or ""
        out["soul_path"] = getattr(mf, "soul_path", "")
        out["memory_path"] = getattr(mf, "memory_path", "")
        out["user_path"] = getattr(mf, "user_path", "")
        out["context_from_memory"] = bool(
            getattr(cfg.agent, "context_from_memory", True)
        )
        out["context_from_knowledge"] = bool(
            getattr(cfg.agent, "context_from_knowledge", False)
        )
        db = Path(cfg.memory.db_path).expanduser()
        out["memory_db"] = {
            "path": str(db),
            "exists": db.exists(),
            "size_bytes": db.stat().st_size if db.exists() else 0,
        }
    except Exception:
        logger.debug("Memory stats probe failed", exc_info=True)
    return out


def _vault_status() -> Dict[str, Any]:
    try:
        from openjarvis.core.config import load_config

        cfg = load_config()
        mf = cfg.memory_files
        vault_path = (getattr(mf, "vault_path", "") or "").strip()
        if not vault_path:
            return {"configured": False}
        vault = Path(vault_path).expanduser()
        note_count = 0
        journal_count = 0
        if vault.is_dir():
            note_count = sum(1 for _ in vault.rglob("*.md") if ".obsidian" not in _.parts)
            journal_dir = vault / "Journal"
            if journal_dir.is_dir():
                journal_count = len(list(journal_dir.glob("*.md")))
        checkpoint = {}
        cp_path = vault / ".jarvis-writeback.json"
        if cp_path.exists():
            try:
                checkpoint = json.loads(cp_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "configured": True,
            "path": str(vault),
            "exists": vault.is_dir(),
            "writeback": bool(getattr(mf, "vault_writeback", True)),
            "writeback_interval": int(
                getattr(mf, "vault_writeback_interval", 3600) or 3600
            ),
            "note_count": note_count,
            "journal_count": journal_count,
            "writeback_checkpoint": {
                "last_trace_rowid": checkpoint.get("last_trace_rowid", 0),
                "known_skills": len(checkpoint.get("known_skills") or []),
            },
        }
    except Exception:
        logger.debug("Vault status probe failed", exc_info=True)
        return {"configured": False}


def _managed_agents(app_state: Any = None) -> List[Dict[str, Any]]:
    manager = None
    if app_state is not None:
        manager = getattr(app_state, "agent_manager", None)
    if manager is None:
        try:
            from openjarvis.core.config import load_config

            cfg = load_config()
            db = getattr(
                getattr(cfg, "agent_manager", None), "db_path", ""
            ) or "~/.openjarvis/agents.db"
            from openjarvis.agents.manager import AgentManager

            manager = AgentManager(str(Path(db).expanduser()))
        except Exception:
            return []
    try:
        agents = manager.list_agents()
        out = []
        for a in agents:
            out.append(
                {
                    "id": a.get("id"),
                    "name": a.get("name"),
                    "agent_type": a.get("agent_type"),
                    "status": a.get("status"),
                    "schedule_type": a.get("schedule_type"),
                    "schedule_value": a.get("schedule_value"),
                    "last_run_at": a.get("last_run_at"),
                    "total_runs": a.get("total_runs"),
                    "current_activity": a.get("current_activity"),
                }
            )
        return out
    except Exception:
        logger.debug("Managed agents probe failed", exc_info=True)
        return []


def _channels() -> List[Dict[str, Any]]:
    try:
        from openjarvis.core.registry import ChannelRegistry

        return [
            {"id": name, "registered": True}
            for name, _ in ChannelRegistry.items()
        ]
    except Exception:
        return []


def _engine_info(app_state: Any = None) -> Dict[str, Any]:
    info: Dict[str, Any] = {}
    if app_state is not None:
        info["model"] = getattr(app_state, "model", "") or ""
        info["engine"] = getattr(app_state, "engine_name", "") or ""
        agent = getattr(app_state, "agent", None)
        info["agent"] = getattr(agent, "agent_id", None) or getattr(
            app_state, "agent_name", ""
        )
    if not info.get("model"):
        try:
            from openjarvis.core.config import load_config

            cfg = load_config()
            info.setdefault("model", getattr(cfg.intelligence, "model", "") or "")
            info.setdefault(
                "engine", getattr(cfg.engine, "default_engine", "") or ""
            )
        except Exception:
            pass
    return info


def build_capability_index(app_state: Any = None) -> Dict[str, Any]:
    """Assemble the full capability manifest."""
    tools = _safe(_tool_catalog, {})
    mcp = _safe(_parse_mcp_servers, [])
    connectors = _safe(_connector_status, [])
    knowledge = _safe(_knowledge_stats, {})
    memory = _safe(_memory_stats, {})
    vault = _safe(_vault_status, {})
    agents = _safe(lambda: _managed_agents(app_state), [])
    channels = _safe(_channels, [])
    engine = _safe(lambda: _engine_info(app_state), {})

    connected = [c for c in (connectors or []) if c.get("connected")]
    summary = {
        "tools_enabled": (tools or {}).get("enabled_count", 0),
        "tools_registry": (tools or {}).get("registry_count", 0),
        "mcp_servers": len(mcp or []),
        "connectors_connected": len(connected),
        "connectors_total": len(connectors or []),
        "knowledge_chunks": (knowledge or {}).get("chunk_count", 0),
        "knowledge_sources": (knowledge or {}).get("source_count", 0),
        "managed_agents": len(agents or []),
        "vault_configured": bool((vault or {}).get("configured")),
        "model": (engine or {}).get("model", ""),
    }

    return {
        "summary": summary,
        "engine": engine,
        "tools": tools,
        "mcp_servers": mcp,
        "connectors": connectors,
        "knowledge": knowledge,
        "memory": memory,
        "vault": vault,
        "managed_agents": agents,
        "channels": channels,
        "self_modification": {
            "tools": [
                "self_inspect",
                "config_manage",
                "mcp_manage",
                "connector_manage",
                "credential_manage",
                "skill_manage",
                "managed_agent_manage",
                "memory_manage",
                "user_profile_manage",
            ],
            "hint": (
                "Use self_inspect for the full map; use the listed tools to "
                "modify your own configuration, connectors, MCP servers, "
                "skills, and long-running agents."
            ),
        },
    }


def capability_prompt_block(app_state: Any = None, *, max_sources: int = 8) -> str:
    """Compact system-prompt suffix so the brain always sees its body."""
    idx = build_capability_index(app_state)
    s = idx.get("summary") or {}
    lines = [
        "## Capability map (live — use self_inspect for details)",
        (
            f"Tools enabled: {s.get('tools_enabled', 0)}/"
            f"{s.get('tools_registry', 0)} registry"
            + (" + mcp:*" if (idx.get("tools") or {}).get("mcp_wildcard") else "")
        ),
        f"MCP servers: {s.get('mcp_servers', 0)}",
        (
            f"Connectors: {s.get('connectors_connected', 0)}/"
            f"{s.get('connectors_total', 0)} connected"
        ),
        (
            f"Knowledge corpus: {s.get('knowledge_chunks', 0)} chunks across "
            f"{s.get('knowledge_sources', 0)} sources"
        ),
        f"Managed agents: {s.get('managed_agents', 0)}",
    ]
    sources = (idx.get("knowledge") or {}).get("sources") or {}
    if sources:
        top = sorted(sources.items(), key=lambda kv: -kv[1])[:max_sources]
        lines.append(
            "Indexed sources: "
            + ", ".join(f"{name}({n})" for name, n in top)
        )
    vault = idx.get("vault") or {}
    if vault.get("configured"):
        lines.append(
            f"Obsidian vault: {vault.get('path')} "
            f"({vault.get('note_count', 0)} notes, "
            f"{vault.get('journal_count', 0)} journals, "
            f"writeback={'on' if vault.get('writeback') else 'off'})"
        )
    else:
        lines.append("Obsidian vault: not configured (set memory_files.vault_path).")
    lines.append(
        "Use self_inspect to query your full capability map. "
        "Use config_manage / mcp_manage / connector_manage / "
        "managed_agent_manage to modify yourself."
    )
    return "\n".join(lines)


__all__ = [
    "build_capability_index",
    "capability_prompt_block",
]
