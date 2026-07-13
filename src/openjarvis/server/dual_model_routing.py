"""Dual-model chat routing — fast Cursor models + local Ollama tool agent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

_TOOL_INTENT_RE = re.compile(
    r"(?:"
    r"\b(?:search|look\s*up|lookup|browse|open|visit|navigate|reddit|google|"
    r"website|webpage|web\s+page|headless|screenshot|extract|scrape|fetch)\b"
    r"|"
    r"\b(?:find|check|read|summarize|what(?:'s|\s+is)\s+on)\b.{0,40}\b(?:online|web|internet|reddit|news)\b"
    r"|"
    r"\b(?:latest|current|live|today(?:'s)?)\b.{0,30}\b(?:news|headlines|weather|score)\b"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ChatRouting:
    """Resolved models and path for one chat completion request."""

    use_agent: bool
    stream_model: str
    agent_model: str
    polish_model: Optional[str] = None


def needs_tool_agent(user_text: str) -> bool:
    """Heuristic: does the user message likely need browse/search/tools?"""
    text = (user_text or "").strip()
    if not text:
        return False
    return bool(_TOOL_INTENT_RE.search(text))


def _model_engine_id(engine: Any, model: str) -> str:
    """Return engine_id for *model* (unwrap MultiEngine when present)."""
    try:
        from openjarvis.engine.multi import MultiEngine

        inner = getattr(engine, "_inner", engine)
        if isinstance(inner, MultiEngine):
            routed = inner._engine_for(model)
            if routed is not None:
                engine = routed
    except Exception:
        pass
    inner = getattr(engine, "_inner", None) or getattr(engine, "_engine", None)
    if inner is not None:
        return getattr(inner, "engine_id", "") or getattr(engine, "engine_id", "")
    return getattr(engine, "engine_id", "") or ""


def is_fast_proxy_model(engine: Any, model: str) -> bool:
    """True when *model* is served by the Cursor OpenAI proxy (lmstudio engine)."""
    return _model_engine_id(engine, model) == "lmstudio"


def resolve_chat_routing(
    config: Any,
    engine: Any,
    ui_model: str,
    user_text: str,
) -> ChatRouting:
    """Pick agent vs direct chat and which backend models to use.

    When dual routing is enabled and the UI selects a Cursor proxy model
    (cursor-small, composer-2, …):

    * plain chat → direct proxy stream (fast, no tool schema)
    * tool-like requests → Ollama ``tool_model`` agent loop
    * optional ``response_model`` polishes the final answer after tools
    """
    dual_enabled = bool(getattr(config.agent, "dual_model_routing", True))
    tool_model = (
        getattr(config.agent, "tool_model", "").strip()
        or getattr(config.intelligence, "fallback_model", "").strip()
        or ui_model
    )
    response_model = getattr(config.agent, "response_model", "").strip()

    if not dual_enabled or not is_fast_proxy_model(engine, ui_model):
        return ChatRouting(
            use_agent=True,
            stream_model=ui_model,
            agent_model=ui_model,
        )

    if needs_tool_agent(user_text):
        polish = response_model or ui_model
        if polish == tool_model:
            polish = None
        return ChatRouting(
            use_agent=True,
            stream_model=ui_model,
            agent_model=tool_model,
            polish_model=polish,
        )

    return ChatRouting(
        use_agent=False,
        stream_model=ui_model,
        agent_model=ui_model,
    )


__all__ = [
    "ChatRouting",
    "needs_tool_agent",
    "is_fast_proxy_model",
    "resolve_chat_routing",
]
