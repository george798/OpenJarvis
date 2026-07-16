"""remember — deterministic memory routing for persisted facts.

One tool, four destinations. The "what goes where" rules live here in code
(mirroring how ``project_context`` bakes in dual-layer routing) so the agent
never has to guess which memory layer to use:

- ``fact``        -> memory.db (FTS5, auto-injected into chats when relevant)
- ``preference``  -> USER.md (persona-resolved, always in the system prompt)
- ``rule``        -> MEMORY.md (persona-resolved, always in the system prompt)
- ``note``        -> Obsidian vault ``Notes/`` + inline knowledge.db indexing

When ``kind`` is omitted, conservative heuristics pick a destination and
ambiguous content defaults to ``fact``.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

VALID_KINDS = ("fact", "preference", "rule", "note")

# Single MEMORY.md / USER.md entries larger than this crowd out other always-
# injected bullets; long content belongs in the vault instead.
MAX_PROMPT_ENTRY_CHARS = 300

# Content longer than this without an explicit kind is routed to the vault.
NOTE_LENGTH_THRESHOLD = 400

_PREFERENCE_RE = re.compile(
    r"^(i prefer|i'd prefer|my name is|call me|i like|i dislike|i hate"
    r"|i am |i'm |my timezone|my email|my birthday|address me)",
    re.IGNORECASE,
)
_RULE_RE = re.compile(
    r"^(always|never|do not|don't|when \S.* (do|use|prefer)|from now on"
    r"|remember to always|make sure to)",
    re.IGNORECASE,
)
_CODE_MARKERS = (
    "```",
    "def ",
    "class ",
    "import ",
    "function ",
    "const ",
    "#include",
    "public static",
)


def _looks_like_code(content: str) -> bool:
    """Heuristic guard against dumping source code into memory stores."""
    if "```" in content:
        return True
    lines = content.splitlines()
    if len(lines) > 30:
        return True
    marker_hits = sum(1 for m in _CODE_MARKERS if m in content)
    brace_density = (content.count("{") + content.count("}")) / max(
        len(content), 1
    )
    return marker_hits >= 2 or brace_density > 0.02


def _infer_kind(content: str, title: str) -> str:
    stripped = content.strip()
    if _PREFERENCE_RE.match(stripped):
        return "preference"
    if _RULE_RE.match(stripped):
        return "rule"
    if title or len(stripped) > NOTE_LENGTH_THRESHOLD:
        return "note"
    return "fact"


def _slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "note"


@ToolRegistry.register("remember")
class RememberTool(BaseTool):
    """Persist a fact/preference/rule/note to the correct memory layer."""

    tool_id = "remember"

    def __init__(self, backend: Any = None) -> None:
        self._backend = backend  # memory.db backend, injected by SystemBuilder
        self._mf_config: Any = None  # persona-resolved MemoryFilesConfig (lazy)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="remember",
            description=(
                "Persist something worth remembering. Routes to the correct"
                " memory layer automatically: kind='fact' for small discrete"
                " facts/events (searchable memory, surfaces in future chats"
                " when relevant); kind='preference' for user identity and"
                " preferences (always-visible profile); kind='rule' for"
                " standing instructions to always follow (always-visible"
                " memory); kind='note' for longer content like summaries or"
                " meeting notes (Obsidian vault, searchable via"
                " knowledge_search). Omit kind to let the router decide."
                " Never pass source code — index code projects instead."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The information to remember.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": list(VALID_KINDS),
                        "description": (
                            "fact = small discrete fact/event;"
                            " preference = user identity/preference;"
                            " rule = standing instruction;"
                            " note = long-form content."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": (
                            "Optional title (used for the vault note filename"
                            " and heading)."
                        ),
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags stored as metadata.",
                    },
                },
                "required": ["content"],
            },
            category="memory",
        )

    # ------------------------------------------------------------------
    # Lazy config / backend resolution
    # ------------------------------------------------------------------

    def _memory_files(self) -> Any:
        """Persona-resolved MemoryFilesConfig (MEMORY.md/USER.md/vault paths)."""
        if self._mf_config is None:
            from openjarvis.core.config import load_config
            from openjarvis.prompt.builder import SystemPromptBuilder

            cfg = load_config()
            self._mf_config = SystemPromptBuilder._resolve_persona(
                cfg.memory_files
            )
        return self._mf_config

    def _get_backend(self) -> Any:
        if self._backend is None:
            try:
                import openjarvis.tools.storage  # noqa: F401
                from openjarvis.core.config import load_config
                from openjarvis.core.registry import MemoryRegistry

                cfg = load_config()
                key = cfg.memory.default_backend
                if MemoryRegistry.contains(key):
                    self._backend = MemoryRegistry.create(
                        key, db_path=cfg.memory.db_path
                    )
            except Exception:
                return None
        return self._backend

    # ------------------------------------------------------------------
    # Destination writers
    # ------------------------------------------------------------------

    def _store_fact(
        self, content: str, tags: List[str]
    ) -> ToolResult:
        backend = self._get_backend()
        if backend is None:
            return self._fail("Memory backend (memory.db) is unavailable.")
        metadata = {"kind": "fact", "via": "remember"}
        if tags:
            metadata["tags"] = ",".join(tags)
        doc_id = backend.store(content, source="remember", metadata=metadata)
        return self._ok(
            "fact",
            "memory.db",
            f"Stored as fact in memory.db (doc {doc_id}). It will surface"
            " automatically in future chats when the topic matches, and via"
            " memory_search.",
        )

    def _append_prompt_file(
        self, content: str, kind: str
    ) -> ToolResult:
        mf = self._memory_files()
        if kind == "preference":
            raw_path, cap_attr, label = mf.user_path, "user_max_chars", "USER.md"
        else:
            raw_path, cap_attr, label = (
                mf.memory_path,
                "memory_max_chars",
                "MEMORY.md",
            )
        if not raw_path:
            return self._fail(
                f"No {label} path configured (persona disabled); store as"
                " kind='fact' instead."
            )
        if len(content) > MAX_PROMPT_ENTRY_CHARS:
            return self._fail(
                f"Entry too long for {label} ({len(content)} chars,"
                f" max {MAX_PROMPT_ENTRY_CHARS}) — the file is injected into"
                " every system prompt so it must stay small. Use kind='note'"
                " for long content."
            )
        path = Path(raw_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if content in existing:
            return self._ok(
                kind,
                label,
                f"Already present in {label}; nothing added.",
            )
        path.write_text(
            existing.rstrip() + f"\n- {content}\n", encoding="utf-8"
        )

        warning = ""
        try:
            from openjarvis.core.config import load_config

            cap = getattr(load_config().system_prompt, cap_attr)
            new_size = len(path.read_text(encoding="utf-8"))
            if new_size > cap:
                warning = (
                    f" WARNING: {label} is now {new_size} chars, over its"
                    f" {cap}-char injection budget — older entries will be"
                    " truncated from the system prompt. Consider pruning."
                )
        except Exception:
            pass

        surface = (
            "the system prompt of every chat"
            if kind == "rule"
            else "the always-visible user profile"
        )
        return self._ok(
            kind,
            label,
            f"Stored as {kind} in {label} — injected into {surface}.{warning}",
        )

    def _store_note(
        self, content: str, title: str, tags: List[str]
    ) -> ToolResult:
        # Read vault_path from the raw config: _resolve_persona() returns a
        # rebuilt MemoryFilesConfig that does not carry vault_path over.
        from openjarvis.core.config import load_config

        raw_mf = load_config().memory_files
        vault_raw = (getattr(raw_mf, "vault_path", "") or "").strip()
        if not vault_raw:
            return self._fail(
                "No Obsidian vault configured (memory_files.vault_path);"
                " store as kind='fact' instead."
            )
        vault = Path(vault_raw).expanduser()
        notes_dir = vault / "Notes"
        notes_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now().strftime("%Y-%m-%d")
        heading = title.strip() or " ".join(content.split()[:8])
        base = f"{today}-{_slugify(heading)}"
        note_path = notes_dir / f"{base}.md"
        counter = 2
        while note_path.exists():
            note_path = notes_dir / f"{base}-{counter}.md"
            counter += 1

        front = ["---", f"title: {heading}", f"date: {today}"]
        if tags:
            front.append("tags: [" + ", ".join(tags) + "]")
        front.append("---")
        note_text = "\n".join(front) + f"\n\n# {heading}\n\n{content}\n"
        note_path.write_text(note_text, encoding="utf-8")

        rel = note_path.relative_to(vault)
        indexed = self._index_note(note_text, heading, rel)
        if indexed:
            search_hint = (
                "indexed into knowledge.db — findable now via"
                " knowledge_search(source='obsidian')"
            )
        else:
            search_hint = (
                "will be searchable after the next obsidian sync"
                " (connector_manage action=sync connector_id=obsidian)"
            )
        return self._ok(
            "note",
            str(note_path),
            f"Saved as note {rel} in the Obsidian vault; {search_hint}.",
        )

    def _index_note(self, text: str, title: str, rel: Path) -> bool:
        """Best-effort inline indexing so the note is searchable immediately.

        Uses the same doc_id scheme as the Obsidian connector
        (``obsidian:{rel_path}``) so a later full vault sync dedups instead
        of double-indexing.
        """
        try:
            from openjarvis.connectors._stubs import Document
            from openjarvis.connectors.pipeline import IngestionPipeline
            from openjarvis.connectors.store import KnowledgeStore

            doc = Document(
                doc_id=f"obsidian:{rel.as_posix()}",
                source="obsidian",
                doc_type="note",
                content=text,
                title=title,
                timestamp=datetime.now(),
            )
            pipeline = IngestionPipeline(KnowledgeStore())
            return pipeline.ingest([doc]) > 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Result helpers
    # ------------------------------------------------------------------

    def _ok(self, kind: str, destination: str, message: str) -> ToolResult:
        return ToolResult(
            tool_name="remember",
            content=message,
            success=True,
            metadata={"kind": kind, "destination": destination},
        )

    def _fail(self, message: str) -> ToolResult:
        return ToolResult(
            tool_name="remember", content=message, success=False
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def execute(self, **params: Any) -> ToolResult:
        content = str(params.get("content", "")).strip()
        if not content:
            return self._fail("`content` is required and cannot be empty.")

        kind: Optional[str] = params.get("kind")
        if kind is not None:
            kind = str(kind).strip().lower()
            if kind not in VALID_KINDS:
                return self._fail(
                    f"Invalid kind '{kind}'."
                    f" Valid kinds: {', '.join(VALID_KINDS)}."
                )

        title = str(params.get("title", "") or "").strip()
        raw_tags = params.get("tags") or []
        tags = [str(t).strip() for t in raw_tags if str(t).strip()]

        if _looks_like_code(content):
            return self._fail(
                "This looks like source code or a file dump. Never store code"
                " in memory — index the project instead"
                " (update-project-knowledge.ps1) and query it via"
                " knowledge_search/project_context."
            )

        if kind is None:
            kind = _infer_kind(content, title)

        if kind == "fact":
            return self._store_fact(content, tags)
        if kind in ("preference", "rule"):
            return self._append_prompt_file(content, kind)
        return self._store_note(content, title, tags)


__all__ = ["RememberTool"]
