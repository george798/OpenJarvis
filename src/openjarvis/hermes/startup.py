"""Hermes-parity startup hooks (skill sync, memory bootstrap)."""

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
Add durable facts here after complex tasks (Hermes-style procedural memory).
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


def _sync_skill_sources(config: Any) -> dict[str, int]:
    """Sync configured Hermes/OpenClaw/GitHub skill sources."""
    from openjarvis.core.config import SkillSourceConfig
    from openjarvis.skills.importer import SkillImporter
    from openjarvis.skills.parser import SkillParser
    from openjarvis.skills.tool_translator import ToolTranslator

    sources = config.skills.sources
    if not sources and not config.skills.auto_sync:
        return {}

    # Normalise dict entries from raw TOML if needed
    normalised: list[SkillSourceConfig] = []
    for src in sources:
        if isinstance(src, SkillSourceConfig):
            normalised.append(src)
        elif isinstance(src, dict):
            normalised.append(
                SkillSourceConfig(
                    source=str(src.get("source", "")),
                    url=str(src.get("url", "")),
                    filter=dict(src.get("filter") or {}),
                    auto_update=bool(src.get("auto_update", False)),
                )
            )

    if not normalised:
        return {}

    from openjarvis.skills.sources.github import GitHubResolver
    from openjarvis.skills.sources.hermes import HermesResolver
    from openjarvis.skills.sources.openclaw import OpenClawResolver

    def _get_resolver(source: str, url: str = ""):
        if source == "hermes":
            return HermesResolver()
        if source == "openclaw":
            return OpenClawResolver()
        if source == "github":
            return GitHubResolver(repo_url=url)
        raise ValueError(f"Unknown skill source: {source}")

    target_root = Path(config.skills.skills_dir).expanduser()
    importer = SkillImporter(
        parser=SkillParser(),
        tool_translator=ToolTranslator(),
        target_root=target_root,
    )
    counts: dict[str, int] = {}

    for src in normalised:
        if not src.source:
            continue
        try:
            resolver = _get_resolver(src.source, url=src.url)
            resolver.sync()
            skills = resolver.list_skills()
            categories = src.filter.get("category") or []
            if categories:
                skills = [s for s in skills if s.category in categories]
            installed = 0
            for skill in skills:
                try:
                    importer.import_skill(skill)
                    installed += 1
                except Exception as exc:
                    logger.debug("Skill import skipped %s: %s", skill.name, exc)
            counts[src.source] = installed
            logger.info("Hermes startup: synced %d skills from %s", installed, src.source)
        except Exception:
            logger.exception("Hermes startup: failed to sync source %s", src.source)
            counts[src.source] = 0

    return counts


def _connect_vault(config: Any) -> dict[str, Any]:
    """Auto-connect the Obsidian vault and index it into memory.

    Reads ``memory_files.vault_path``; seeds the connectors-router instance
    cache (so the web UI shows it connected) and kicks off a background
    ingestion sync so the vault contents are searchable via knowledge tools.
    """
    mf = getattr(config, "memory_files", None)
    vault_path = (getattr(mf, "vault_path", "") or "").strip()
    if not vault_path:
        return {}
    path = Path(vault_path).expanduser()
    if not path.is_dir():
        logger.warning("Hermes startup: vault path %s does not exist", path)
        return {"vault": "missing"}

    from openjarvis.connectors.obsidian import ObsidianConnector

    connector = ObsidianConnector(vault_path=str(path))

    # Seed the API router's cache so GET /v1/connectors shows it connected.
    try:
        from openjarvis.server import connectors_router

        connectors_router._instances["obsidian"] = connector
    except Exception:
        pass

    def _index() -> None:
        try:
            from openjarvis.connectors.pipeline import IngestionPipeline
            from openjarvis.connectors.store import KnowledgeStore
            from openjarvis.connectors.sync_engine import SyncEngine

            engine = SyncEngine(pipeline=IngestionPipeline(store=KnowledgeStore()))
            engine.sync(connector)
            logger.info("Hermes startup: vault indexed from %s", path)
        except Exception:
            logger.exception("Hermes startup: vault indexing failed")

    import threading

    threading.Thread(target=_index, daemon=True, name="vault-index").start()
    return {"vault": str(path)}


def run_hermes_startup(config: Any) -> Dict[str, Any]:
    """Run Hermes-parity bootstrap steps. Safe to call on every server start."""
    result: Dict[str, Any] = {"memory_files": [], "skills": {}, "vault": {}}

    try:
        result["memory_files"] = _bootstrap_memory_files(config)
    except Exception:
        logger.exception("Hermes startup: memory bootstrap failed")

    if config.skills.auto_sync or config.skills.sources:
        try:
            result["skills"] = _sync_skill_sources(config)
        except Exception:
            logger.exception("Hermes startup: skill sync failed")

    try:
        result["vault"] = _connect_vault(config)
    except Exception:
        logger.exception("Hermes startup: vault connect failed")

    try:
        from openjarvis.hermes.vault_writeback import start_vault_writeback

        start_vault_writeback(config)
    except Exception:
        logger.exception("Hermes startup: vault writeback failed")

    return result
