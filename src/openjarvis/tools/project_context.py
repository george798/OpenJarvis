"""Project context tools — slim MCP surface for external coding agents.

Exposes two tools intended for Cursor / OpenCode connecting over the MCP
SSE bridge:

- ``project_list``     — registered code projects from ``projects.toml`` with
  indexed-layer status (code chunks + Graphify graph chunks).
- ``project_context``  — dual-layer knowledge retrieval for one project. The
  routing rules from the ``dual-layer-project-knowledge`` skill are baked in
  server-side: graph layer (``{source}-graph``) first for change/architecture
  intents, code layer (``{source}``) first for lookups. External callers get
  both layers in a single structured response without needing to know the
  two-layer scheme.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.connectors.store import KnowledgeStore
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

GRAPH_TOP_K = 12
CODE_TOP_K = 10


def _projects_toml_path() -> Path:
    override = os.environ.get("OPENJARVIS_PROJECTS_TOML", "")
    if override:
        return Path(override)
    from openjarvis.core.config import DEFAULT_CONFIG_DIR

    return DEFAULT_CONFIG_DIR / "projects.toml"


def _load_projects() -> List[Dict[str, Any]]:
    """Return enabled projects from projects.toml (empty list if missing)."""
    path = _projects_toml_path()
    if not path.is_file():
        return []
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    projects = data.get("project", [])
    if not isinstance(projects, list):
        return []
    return [p for p in projects if isinstance(p, dict) and p.get("enabled", True)]


def _chunk_counts(store: KnowledgeStore, sources: List[str]) -> Dict[str, int]:
    """Return chunk counts per source for the given source names."""
    if not sources:
        return {}
    placeholders = ",".join("?" for _ in sources)
    rows = store._conn.execute(
        "SELECT source, COUNT(*) FROM knowledge_chunks "
        f"WHERE source IN ({placeholders}) GROUP BY source",
        sources,
    ).fetchall()
    counts = {src: 0 for src in sources}
    counts.update({row[0]: row[1] for row in rows})
    return counts


@ToolRegistry.register("project_list")
class ProjectListTool(BaseTool):
    """List code projects registered in projects.toml with index status."""

    tool_id = "project_list"

    def __init__(self, store: Optional[KnowledgeStore] = None) -> None:
        self._store = store

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="project_list",
            description=(
                "List the code projects OpenJarvis has deep knowledge about"
                " (indexed code chunks + architecture graphs). Call this FIRST"
                " to discover valid project sources, then call"
                " `project_context` with the returned `source` value to"
                " retrieve project knowledge."
            ),
            parameters={"type": "object", "properties": {}},
            category="knowledge",
        )

    def _get_store(self) -> Optional[KnowledgeStore]:
        if self._store is None:
            try:
                self._store = KnowledgeStore()
            except Exception:
                return None
        return self._store

    def execute(self, **params: Any) -> ToolResult:
        projects = _load_projects()
        if not projects:
            return ToolResult(
                tool_name="project_list",
                content=(
                    "No projects registered. Expected projects.toml at "
                    f"{_projects_toml_path()}."
                ),
                success=False,
            )

        store = self._get_store()
        all_sources: List[str] = []
        for proj in projects:
            src = str(proj.get("source", ""))
            if src:
                all_sources.extend([src, f"{src}-graph"])
        counts = _chunk_counts(store, all_sources) if store else {}

        lines: List[str] = [
            "Registered code projects (use `source` with `project_context`):",
            "",
        ]
        for proj in projects:
            src = str(proj.get("source", ""))
            name = proj.get("name", src)
            code_chunks = counts.get(src, 0)
            graph_chunks = counts.get(f"{src}-graph", 0)
            lines.append(f"## {name}")
            lines.append(f"- source: `{src}`")
            if proj.get("container_path"):
                lines.append(f"- repo path (container): {proj['container_path']}")
            if proj.get("host_path"):
                lines.append(f"- repo path (host): {proj['host_path']}")
            lines.append(
                f"- indexed layers: code={code_chunks} chunks,"
                f" graph={graph_chunks} chunks"
            )
            if code_chunks == 0 and graph_chunks == 0:
                lines.append(
                    "  (not yet indexed — run update-project-knowledge.ps1)"
                )
            lines.append("")

        return ToolResult(
            tool_name="project_list",
            content="\n".join(lines).rstrip(),
            success=True,
            metadata={"num_projects": len(projects)},
        )


@ToolRegistry.register("project_context")
class ProjectContextTool(BaseTool):
    """Dual-layer (graph + code) knowledge retrieval for a registered project."""

    tool_id = "project_context"

    def __init__(self, store: Optional[KnowledgeStore] = None) -> None:
        self._store = store

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="project_context",
            description=(
                "Get deep context about a registered code project. Searches"
                " TWO knowledge layers automatically: the architecture graph"
                " (modules, dependencies, blast radius, from Graphify) and the"
                " indexed code chunks (file paths, symbols, snippets). Use"
                " `project_list` first to find valid `source` values. Set"
                " `intent` to match the task: 'change' (impact analysis before"
                " editing), 'architecture' (how things connect), or 'locate'"
                " (find where something is implemented)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": (
                            "Project source id from `project_list`"
                            " (e.g. 'astrosecrets-api')."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "What to look up, e.g. 'authentication middleware"
                            " and token validation'."
                        ),
                    },
                    "intent": {
                        "type": "string",
                        "enum": ["change", "architecture", "locate"],
                        "description": (
                            "Task type: 'change' = planning a code change"
                            " (default), 'architecture' = understand structure,"
                            " 'locate' = find files/symbols."
                        ),
                    },
                    "top_k": {
                        "type": "integer",
                        "description": (
                            "Max results per layer (default: 12 graph, 10 code)."
                        ),
                    },
                },
                "required": ["source", "query"],
            },
            category="knowledge",
        )

    def _get_store(self) -> Optional[KnowledgeStore]:
        if self._store is None:
            try:
                self._store = KnowledgeStore()
            except Exception:
                return None
        return self._store

    def _search_layer(
        self, store: KnowledgeStore, query: str, source: str, top_k: int
    ) -> List[Any]:
        try:
            hits = store.retrieve(query, top_k=top_k, source=source)
        except Exception:
            hits = []
        if hits:
            return hits
        # FTS5 MATCH treats multiple words as implicit AND, so natural-language
        # queries ("authentication token validation") often match nothing.
        # Fall back to OR-joined quoted terms — BM25 still ranks chunks that
        # contain more of the terms higher.
        terms = [t for t in re.findall(r"[A-Za-z0-9_]+", query) if len(t) > 1]
        if len(terms) < 2:
            return hits
        or_query = " OR ".join(f'"{t}"' for t in terms)
        try:
            return store.retrieve(or_query, top_k=top_k, source=source)
        except Exception:
            return []

    @staticmethod
    def _format_hits(hits: List[Any]) -> List[str]:
        lines: List[str] = []
        for i, hit in enumerate(hits, start=1):
            meta = hit.metadata or {}
            title = meta.get("title", "") or meta.get("path", "")
            header = f"**{i}. {title}**" if title else f"**{i}.**"
            lines.append(header)
            lines.append(hit.content)
            lines.append("")
        return lines

    def execute(self, **params: Any) -> ToolResult:
        source = str(params.get("source", "")).strip()
        query = str(params.get("query", "")).strip()
        intent = str(params.get("intent", "change")).strip().lower()
        top_k = params.get("top_k")

        if not source or not query:
            return ToolResult(
                tool_name="project_context",
                content="Both `source` and `query` are required.",
                success=False,
            )

        projects = _load_projects()
        known = {str(p.get("source", "")): p for p in projects}
        if source not in known:
            valid = ", ".join(sorted(known)) or "(none registered)"
            return ToolResult(
                tool_name="project_context",
                content=(
                    f"Unknown project source '{source}'."
                    f" Valid sources: {valid}."
                    " Call `project_list` for details."
                ),
                success=False,
            )

        store = self._get_store()
        if store is None:
            return ToolResult(
                tool_name="project_context",
                content="Knowledge store is not available.",
                success=False,
            )

        graph_source = f"{source}-graph"
        graph_k = int(top_k) if top_k else GRAPH_TOP_K
        code_k = int(top_k) if top_k else CODE_TOP_K

        # Dual-layer routing (mirrors the dual-layer-project-knowledge skill):
        # change/architecture -> graph layer first; locate -> code layer first.
        graph_hits = self._search_layer(store, query, graph_source, graph_k)
        code_hits = self._search_layer(store, query, source, code_k)

        proj = known[source]
        repo_path = proj.get("container_path", "") or proj.get("host_path", "")

        graph_section: List[str] = [
            f"# ARCHITECTURE / IMPACT (graph layer: {graph_source})",
            "",
        ]
        if graph_hits:
            graph_section.extend(self._format_hits(graph_hits))
        else:
            graph_section.append(
                "No graph-layer results. The architecture graph may not cover"
                " this topic (or has not been indexed yet) — rely on the code"
                " layer below and consider rephrasing the query with module or"
                " feature names."
            )
            graph_section.append("")

        code_section: List[str] = [
            f"# CODE (file chunks: {source})",
            "",
        ]
        if code_hits:
            code_section.extend(self._format_hits(code_hits))
        else:
            code_section.append(
                "No code-layer results. Try rephrasing with concrete symbol or"
                " file names."
            )
            code_section.append("")

        if intent == "locate":
            sections = code_section + graph_section
        else:
            sections = graph_section + code_section

        header = [
            f"Project: {proj.get('name', source)} (source: {source},"
            f" intent: {intent})",
        ]
        if repo_path:
            header.append(f"Repository path: {repo_path}")
        header.append(
            "Guidance: use the graph layer for modules, dependencies and blast"
            " radius; use the code layer for exact files and snippets. Only"
            " cite file paths that appear in the results below."
        )
        header.append("")

        content = "\n".join(header + sections).rstrip()
        if not graph_hits and not code_hits:
            content += (
                "\n\nNo results in either layer. The topic may be outside this"
                " project or the index may be stale."
            )

        return ToolResult(
            tool_name="project_context",
            content=content,
            success=True,
            metadata={
                "source": source,
                "intent": intent,
                "graph_results": len(graph_hits),
                "code_results": len(code_hits),
            },
        )


__all__ = ["ProjectListTool", "ProjectContextTool"]
