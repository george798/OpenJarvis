"""Server startup bootstrap: persona files, skill sync, vault memory loop."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_JARVIS_SOUL = """\
# Jarvis — Soul

You are Jarvis, a local-first personal AI assistant. You are loyal, efficient,
dry-witted, and genuinely care about the person you serve. You anticipate needs,
stay calm under pressure, and explain things clearly without fluff.
"""

_JARVIS_MEMORY = """\
# Jarvis — Memory

Long-term notes the assistant curates about the user and recurring workflows.
Add durable facts here after complex tasks (procedural memory).
"""

_JARVIS_USER = """\
# Jarvis — User

Preferences, timezone, honorific, and standing instructions for this user.
"""


def _bootstrap_memory_files(config: Any) -> list[str]:
    """Create SOUL/MEMORY/USER files if missing."""
    created: list[str] = []
    mf = config.memory_files
    persona = (mf.persona_name or config.digest.persona or "jarvis").strip()
    if persona and persona != "none":
        base = Path.home() / ".openjarvis" / "personas" / persona
        paths = {
            base / "SOUL.md": _JARVIS_SOUL,
            base / "MEMORY.md": _JARVIS_MEMORY,
            base / "USER.md": _JARVIS_USER,
        }
    else:
        paths = {
            Path(mf.soul_path).expanduser(): _JARVIS_SOUL,
            Path(mf.memory_path).expanduser(): _JARVIS_MEMORY,
            Path(mf.user_path).expanduser(): _JARVIS_USER,
        }

    for path, template in paths.items():
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template, encoding="utf-8")
        created.append(str(path))
    return created


# Keep a module-level reference so the daemon thread and its SQLite handles
# survive for the process lifetime and start() is idempotent across calls.
_connector_sync_scheduler: Any = None


def start_connector_sync(config: Any) -> bool:
    """Start the background connector re-sync loop. Returns True if running.

    Periodically re-syncs every *connected* connector (Gmail, Notion, the
    Obsidian vault, ...) into knowledge.db. Pulls live instances from the
    connectors router on each cycle, so sources connected at runtime via
    the web app join the loop without a restart.
    """
    global _connector_sync_scheduler
    cc = getattr(config, "connectors", None)
    if cc is not None and not getattr(cc, "sync_enabled", True):
        return False
    if _connector_sync_scheduler is not None:
        return True

    from openjarvis.connectors.pipeline import IngestionPipeline
    from openjarvis.connectors.scheduler import SyncScheduler
    from openjarvis.connectors.store import KnowledgeStore
    from openjarvis.connectors.sync_engine import SyncEngine

    def _live_connectors():
        from openjarvis.server import connectors_router

        return list(connectors_router._instances.values())

    interval = int(getattr(cc, "sync_interval", 1800) or 1800)
    scheduler = SyncScheduler(
        SyncEngine(pipeline=IngestionPipeline(store=KnowledgeStore())),
        interval_seconds=max(60, interval),
        connector_provider=_live_connectors,
    )
    scheduler.start()
    _connector_sync_scheduler = scheduler
    return True


def run_startup_bootstrap(config: Any) -> Dict[str, Any]:
    """Run server bootstrap steps. Safe to call on every server start."""
    result: Dict[str, Any] = {"memory_files": [], "skills": {}, "vault": {}}

    try:
        result["memory_files"] = _bootstrap_memory_files(config)
    except Exception:
        logger.exception("Startup bootstrap: memory bootstrap failed")

    if config.skills.auto_sync or config.skills.sources:
        try:
            from openjarvis.skills.sync import sync_skill_sources

            result["skills"] = sync_skill_sources(config)
        except Exception:
            logger.exception("Startup bootstrap: skill sync failed")

    try:
        from openjarvis.connectors.vault import connect_vault

        result["vault"] = connect_vault(config)
    except Exception:
        logger.exception("Startup bootstrap: vault connect failed")

    try:
        from openjarvis.connectors.vault_writeback import start_vault_writeback

        start_vault_writeback(config)
    except Exception:
        logger.exception("Startup bootstrap: vault writeback failed")

    try:
        result["connector_sync"] = start_connector_sync(config)
    except Exception:
        logger.exception("Startup bootstrap: connector sync scheduler failed")

    return result


__all__ = ["run_startup_bootstrap", "start_connector_sync"]
