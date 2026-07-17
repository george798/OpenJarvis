"""Obsidian vault write-back — the compounding memory loop.

Periodically appends completed agent interactions (from the trace store) and
newly learned skills to daily journal notes in the configured markdown vault.
The vault is also indexed into memory by the Obsidian connector, so everything
written here is retrievable in future conversations: the loop that makes the
system smarter on day 100 than on day 1.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CHECKPOINT_NAME = ".jarvis-writeback.json"
_thread: Optional[threading.Thread] = None


def _load_checkpoint(vault: Path) -> dict:
    path = vault / _CHECKPOINT_NAME
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_trace_rowid": 0, "known_skills": []}


def _save_checkpoint(vault: Path, cp: dict) -> None:
    try:
        (vault / _CHECKPOINT_NAME).write_text(
            json.dumps(cp, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        logger.debug("Vault writeback: checkpoint save failed: %s", exc)


def _new_traces(db_path: Path, after_rowid: int) -> list[dict]:
    if not db_path.exists():
        return []
    try:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        # NB: explicit alias — on tables with an INTEGER PRIMARY KEY, sqlite
        # reports the rowid column under the PK's name, not "rowid".
        rows = con.execute(
            "SELECT rowid AS rid, query, agent, model, result, started_at "
            "FROM traces WHERE rowid > ? ORDER BY rowid ASC LIMIT 200",
            (after_rowid,),
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("Vault writeback: trace query failed: %s", exc)
        return []


def _summarize(text: str, limit: int = 400) -> str:
    text = (text or "").strip().replace("\r", "")
    if len(text) > limit:
        text = text[:limit].rstrip() + " …"
    return text


def _append_journal(
    vault: Path, traces: list[dict], new_skills: list[str]
) -> list[Path]:
    """Append entries to daily journal notes; return the touched note paths."""
    journal_dir = vault / "Journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    touched: list[Path] = []

    by_day: dict[str, list[dict]] = {}
    for tr in traces:
        day = datetime.fromtimestamp(tr.get("started_at") or time.time()).strftime(
            "%Y-%m-%d"
        )
        by_day.setdefault(day, []).append(tr)

    for day, day_traces in by_day.items():
        note = journal_dir / f"{day}.md"
        lines: list[str] = []
        if not note.exists():
            lines.append(f"# Jarvis Journal — {day}\n")
        for tr in day_traces:
            ts = datetime.fromtimestamp(
                tr.get("started_at") or time.time()
            ).strftime("%H:%M")
            query = _summarize(tr.get("query", ""), 200)
            result = _summarize(tr.get("result", ""))
            if not query:
                continue
            lines.append(f"## {ts} — {query}")
            lines.append(f"*agent: {tr.get('agent', '?')} · model: {tr.get('model', '?')}*")
            lines.append("")
            lines.append(result or "(no result recorded)")
            lines.append("")
        if lines:
            with note.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            touched.append(note)

    if new_skills:
        today = datetime.now().strftime("%Y-%m-%d")
        note = journal_dir / f"{today}.md"
        with note.open("a", encoding="utf-8") as fh:
            fh.write("\n## Skills learned\n")
            for name in new_skills:
                fh.write(f"- `{name}`\n")
            fh.write("\n")
        if note not in touched:
            touched.append(note)

    return touched


def _reindex_notes(vault: Path, notes: list[Path]) -> None:
    """Re-ingest freshly written journal notes into knowledge.db.

    Without this, journal entries only become searchable after the next
    full vault sync — historically the next server restart — leaving the
    "compounding memory" loop half-open for the rest of the day.
    """
    if not notes:
        return
    try:
        from openjarvis.connectors._stubs import Document
        from openjarvis.connectors.pipeline import IngestionPipeline
        from openjarvis.connectors.store import KnowledgeStore

        docs = []
        for note in notes:
            try:
                text = note.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = note.relative_to(vault)
            docs.append(
                Document(
                    doc_id=f"obsidian:{rel.as_posix()}",
                    source="obsidian",
                    doc_type="note",
                    content=text,
                    title=note.stem,
                    timestamp=datetime.now(),
                )
            )
        if docs:
            pipeline = IngestionPipeline(store=KnowledgeStore())
            count = pipeline.ingest(docs, replace=True)
            logger.debug(
                "Vault writeback: re-indexed %d journal notes (%d chunks)",
                len(docs),
                count,
            )
    except Exception:
        logger.exception("Vault writeback: journal re-index failed")


def _current_skills(skills_dir: str) -> list[str]:
    d = Path(skills_dir).expanduser()
    if not d.exists():
        return []
    return sorted(f.stem for f in d.glob("*.toml"))


def _writeback_once(config: Any, vault: Path) -> None:
    cp = _load_checkpoint(vault)

    traces_db = Path(
        getattr(getattr(config, "traces", None), "db_path", "")
        or "~/.openjarvis/traces.db"
    ).expanduser()
    traces = _new_traces(traces_db, int(cp.get("last_trace_rowid", 0)))

    skills_dir = getattr(getattr(config, "skills", None), "skills_dir", "") or (
        "~/.openjarvis/skills/"
    )
    skills_now = _current_skills(skills_dir)
    known = set(cp.get("known_skills", []))
    new_skills = [s for s in skills_now if s not in known]

    if not traces and not new_skills:
        return

    touched = _append_journal(vault, traces, new_skills)
    _reindex_notes(vault, touched)

    if traces:
        cp["last_trace_rowid"] = max(int(t["rid"]) for t in traces)
    cp["known_skills"] = skills_now
    _save_checkpoint(vault, cp)
    logger.info(
        "Vault writeback: %d interactions, %d new skills -> %s",
        len(traces),
        len(new_skills),
        vault / "Journal",
    )


def start_vault_writeback(config: Any) -> bool:
    """Start the periodic vault write-back daemon. Returns True if started."""
    global _thread
    mf = getattr(config, "memory_files", None)
    vault_path = (getattr(mf, "vault_path", "") or "").strip()
    if not vault_path or not getattr(mf, "vault_writeback", True):
        return False
    vault = Path(vault_path).expanduser()
    if not vault.is_dir():
        logger.warning("Vault writeback: vault path %s does not exist", vault)
        return False
    if _thread is not None and _thread.is_alive():
        return True

    interval = max(60, int(getattr(mf, "vault_writeback_interval", 3600)))

    def _loop() -> None:
        while True:
            try:
                _writeback_once(config, vault)
            except Exception:
                logger.exception("Vault writeback cycle failed")
            time.sleep(interval)

    _thread = threading.Thread(target=_loop, daemon=True, name="vault-writeback")
    _thread.start()
    logger.info(
        "Vault writeback started (vault=%s, interval=%ds)", vault, interval
    )
    return True


__all__ = ["start_vault_writeback"]
