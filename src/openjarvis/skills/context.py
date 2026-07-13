"""Helpers for loading skill manifests and building chat context."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from openjarvis.agents._stubs import AgentContext
from openjarvis.skills.loader import discover_skills, load_skill_directory
from openjarvis.skills.types import SkillManifest


def skills_dirs(config: object | None = None) -> List[Path]:
    """Return skill search paths (config dir first, then default)."""
    dirs: List[Path] = []
    if config is not None:
        skills_cfg = getattr(config, "skills", None)
        skills_dir = getattr(skills_cfg, "skills_dir", None) if skills_cfg else None
        if skills_dir:
            dirs.append(Path(str(skills_dir)).expanduser())
    default = Path("~/.openjarvis/skills").expanduser()
    if default not in dirs:
        dirs.append(default)
    return dirs


def discover_installed_skills(
    config: object | None = None,
) -> Dict[str, SkillManifest]:
    """Discover skills from configured directories (first-seen name wins)."""
    found: Dict[str, SkillManifest] = {}
    for directory in skills_dirs(config):
        for manifest in discover_skills(directory):
            if manifest.name not in found:
                found[manifest.name] = manifest
    return found


def resolve_skill_manifest(
    name: str,
    config: object | None = None,
) -> Optional[SkillManifest]:
    """Return a skill manifest by name, or None if not installed."""
    return discover_installed_skills(config).get(name)


def build_skill_prompt(manifest: SkillManifest) -> str:
    """Format a skill manifest as system-context instructions."""
    parts = [
        f"# Active skill: {manifest.name}",
        "",
        manifest.description or "",
    ]
    if manifest.markdown_content:
        parts.extend(["", manifest.markdown_content])
    if manifest.steps:
        parts.append("\n## Required tool steps (execute in order)")
        for i, step in enumerate(manifest.steps, 1):
            target = step.tool_name or step.skill_name or "step"
            parts.append(f"{i}. Call `{target}` with arguments matching:")
            parts.append(f"   {step.arguments_template}")
    parts.append(
        "\n**MANDATORY:** This skill is active for this turn. Execute the steps above "
        "using your tools — do not give generic advice or skip tool calls. "
        "If a step needs user input (e.g. an API key), ask once, then continue the sequence."
    )
    return "\n".join(p for p in parts if p is not None).strip()


def apply_active_skill_to_context(
    ctx: AgentContext,
    skill_name: str,
    config: object | None = None,
) -> bool:
    """Load an active skill prompt into *ctx* metadata for agent system prompts."""
    if not skill_name:
        return False
    prompt = load_skill_by_name(skill_name, config)
    if not prompt:
        return False
    ctx.metadata["active_skill"] = skill_name
    ctx.metadata["active_skill_prompt"] = prompt
    return True


def load_skill_by_name(
    name: str,
    config: object | None = None,
) -> Optional[str]:
    """Load full skill prompt text for *name*, or None if missing."""
    manifest = resolve_skill_manifest(name, config)
    if manifest is None:
        for directory in skills_dirs(config):
            dir_path = directory / name
            if dir_path.is_dir():
                try:
                    manifest = load_skill_directory(dir_path)
                    break
                except Exception:
                    continue
    if manifest is None:
        return None
    return build_skill_prompt(manifest)
