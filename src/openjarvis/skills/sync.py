"""Startup sync of external skill sources (Hermes Agent, OpenClaw, GitHub)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def sync_skill_sources(config: Any) -> dict[str, int]:
    """Sync configured skill sources into the local skills directory.

    Returns a mapping of ``source -> installed skill count``.
    """
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
            logger.info("Skill sync: synced %d skills from %s", installed, src.source)
        except Exception:
            logger.exception("Skill sync: failed to sync source %s", src.source)
            counts[src.source] = 0

    return counts


__all__ = ["sync_skill_sources"]
