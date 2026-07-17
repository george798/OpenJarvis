"""Context injection — retrieve relevant memory and inject into prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from openjarvis.core.events import EventType, get_event_bus
from openjarvis.core.types import Message, Role
from openjarvis.tools.storage._stubs import MemoryBackend, RetrievalResult


@dataclass(slots=True)
class ContextConfig:
    """Controls how retrieved context is injected into prompts."""

    enabled: bool = True
    top_k: int = 5
    min_score: float = 0.0
    max_context_tokens: int = 2048
    # Optional second store (knowledge.db) merged into the same context block.
    knowledge_enabled: bool = False
    knowledge_top_k: int = 3
    knowledge_max_tokens: int = 1024


def _count_tokens(text: str) -> int:
    """Approximate token count via whitespace split."""
    return len(text.split())


def format_context(results: List[RetrievalResult]) -> str:
    """Format retrieval results into a context block.

    Each result is prefixed with its source attribution.
    """
    if not results:
        return ""

    lines = []
    for r in results:
        source_tag = f"[Source: {r.source}]" if r.source else ""
        if source_tag:
            lines.append(f"{source_tag} {r.content}")
        else:
            lines.append(r.content)

    return "\n\n".join(lines)


def build_context_message(
    results: List[RetrievalResult],
) -> Message:
    """Create a system message with formatted context."""
    context_text = format_context(results)
    content = (
        "The following context was retrieved from the knowledge"
        " base. Use it to inform your response, citing sources"
        " where applicable:\n\n" + context_text
    )
    return Message(role=Role.SYSTEM, content=content)


def _truncate(
    results: List[RetrievalResult], max_tokens: int
) -> List[RetrievalResult]:
    truncated: List[RetrievalResult] = []
    total_tokens = 0
    for r in results:
        tokens = _count_tokens(r.content)
        if total_tokens + tokens > max_tokens:
            break
        truncated.append(r)
        total_tokens += tokens
    return truncated


def _knowledge_retrieve(query: str, top_k: int) -> List[RetrievalResult]:
    """Search knowledge.db; label each hit with its connector source."""
    try:
        from openjarvis.connectors.store import KnowledgeStore

        store = KnowledgeStore()
        try:
            hits = store.retrieve(query, top_k=top_k)
        finally:
            store.close()
        out: List[RetrievalResult] = []
        for h in hits:
            src = h.source or "knowledge"
            meta = getattr(h, "metadata", None) or {}
            title = meta.get("title") or ""
            content = h.content
            if title and title not in content[:80]:
                content = f"{title}: {content}"
            out.append(
                RetrievalResult(
                    content=content,
                    score=h.score,
                    source=f"knowledge:{src}",
                    metadata=meta,
                )
            )
        return out
    except Exception:
        return []


def inject_context(
    query: str,
    messages: List[Message],
    backend: Optional[MemoryBackend],
    *,
    config: Optional[ContextConfig] = None,
    jarvis_config: Any = None,
) -> List[Message]:
    """Retrieve relevant context and prepend it to *messages*.

    Returns a **new** list — the original list is not mutated.
    If no results pass the score threshold, returns the original
    messages unchanged.

    When ``config.knowledge_enabled`` (or ``jarvis_config.agent.context_from_knowledge``)
    is true, also merges BM25 hits from ``knowledge.db`` (Gmail, Obsidian, …)
    into the same injected system message.
    """
    cfg = config or ContextConfig()
    if jarvis_config is not None:
        agent = getattr(jarvis_config, "agent", None)
        if agent is not None:
            if getattr(agent, "context_from_knowledge", False):
                cfg.knowledge_enabled = True
            if getattr(agent, "context_knowledge_top_k", None):
                cfg.knowledge_top_k = int(agent.context_knowledge_top_k)
            if getattr(agent, "context_knowledge_max_tokens", None):
                cfg.knowledge_max_tokens = int(agent.context_knowledge_max_tokens)

    if not cfg.enabled and not cfg.knowledge_enabled:
        return messages

    results: List[RetrievalResult] = []
    if cfg.enabled and backend is not None:
        try:
            mem_hits = backend.retrieve(query, top_k=cfg.top_k)
            mem_hits = [r for r in mem_hits if r.score >= cfg.min_score]
            results.extend(_truncate(mem_hits, cfg.max_context_tokens))
        except Exception:
            pass

    if cfg.knowledge_enabled:
        kn_hits = _knowledge_retrieve(query, cfg.knowledge_top_k)
        results.extend(_truncate(kn_hits, cfg.knowledge_max_tokens))

    if not results:
        return messages

    total_tokens = sum(_count_tokens(r.content) for r in results)

    bus = get_event_bus()
    bus.publish(
        EventType.MEMORY_RETRIEVE,
        {
            "context_injection": True,
            "query": query,
            "num_results": len(results),
            "total_tokens": total_tokens,
            "knowledge_enabled": cfg.knowledge_enabled,
        },
    )

    ctx_msg = build_context_message(results)
    return [ctx_msg] + list(messages)


__all__ = [
    "ContextConfig",
    "build_context_message",
    "format_context",
    "inject_context",
]
