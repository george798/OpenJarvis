"""Per-platform tool restrictions.

Full tools on CLI/web, restricted sets on messaging channels and cron —
overridable via the ``[toolsets]`` config section.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from openjarvis.core.config import JarvisConfig

_DEFAULT_RESTRICTED = (
    "retrieval,calculator,think,knowledge_search,knowledge_sql,"
    "memory_search,memory_retrieve,file_read,web_search,skill_manage"
)
_DEFAULT_WEB = (
    "retrieval,calculator,think,knowledge_search,knowledge_sql,"
    "memory_search,memory_retrieve,file_read,web_search,text_to_speech,"
    "skill_manage,digest_collect"
)
_DEFAULT_CRON = (
    "retrieval,calculator,think,knowledge_search,memory_search,"
    "memory_retrieve,web_search,skill_manage"
)

_BUILTIN_DEFAULTS: dict[str, Optional[str]] = {
    "cli": None,
    "web": _DEFAULT_WEB,
    "telegram": _DEFAULT_RESTRICTED,
    "discord": _DEFAULT_RESTRICTED,
    "slack": _DEFAULT_RESTRICTED,
    "whatsapp": _DEFAULT_RESTRICTED,
    "matrix": _DEFAULT_RESTRICTED,
    "sms": _DEFAULT_RESTRICTED,
    "imessage": _DEFAULT_RESTRICTED,
    "sendblue": _DEFAULT_RESTRICTED,
    "cron": _DEFAULT_CRON,
    "scheduler": _DEFAULT_CRON,
    "channel": _DEFAULT_RESTRICTED,
}


def normalize_platform(channel_type: str) -> str:
    """Map a channel adapter id to a toolset platform key."""
    key = (channel_type or "cli").strip().lower()
    if key in _BUILTIN_DEFAULTS:
        return key
    for prefix in ("telegram", "discord", "slack", "whatsapp", "matrix", "sms"):
        if key.startswith(prefix):
            return prefix
    return "channel"


def _base_tool_names(config: "JarvisConfig") -> List[str]:
    raw = config.tools.enabled or config.agent.tools
    if not raw:
        return []
    if isinstance(raw, list):
        return [n.strip() for n in raw if isinstance(n, str) and n.strip()]
    return [n.strip() for n in raw.split(",") if n.strip()]


def resolve_tool_names(
    platform: str,
    config: "JarvisConfig",
    *,
    base_tools: Optional[List[str]] = None,
) -> Optional[List[str]]:
    """Return allowed tool names for *platform*, or ``None`` for all base tools.

    When ``config.toolsets.enabled`` is false, returns ``None`` (no filtering).
    """
    if not getattr(config, "toolsets", None) or not config.toolsets.enabled:
        return None

    platform_key = normalize_platform(platform)
    toolsets = config.toolsets

    # Config override: toolsets.<platform> = "a,b,c" or "*" for all
    override = getattr(toolsets, platform_key, "")
    if override in ("*", "all"):
        return None
    if override:
        allowed = [t.strip() for t in override.split(",") if t.strip()]
        base = base_tools if base_tools is not None else _base_tool_names(config)
        if not base:
            return allowed
        base_set = set(base)
        filtered = [t for t in allowed if t in base_set]
        return filtered or allowed

    default = _BUILTIN_DEFAULTS.get(platform_key)
    if default is None:
        return None

    allowed = [t.strip() for t in default.split(",") if t.strip()]
    base = base_tools if base_tools is not None else _base_tool_names(config)
    if not base:
        return allowed
    base_set = set(base)
    filtered = [t for t in allowed if t in base_set]
    return filtered or allowed


__all__ = ["normalize_platform", "resolve_tool_names"]
