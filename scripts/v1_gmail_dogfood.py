"""Dogfood the v1 document schema against real Gmail data.

Run after authenticating Gmail (e.g. ``jarvis connect gdrive``). This script
ingests a bounded sample of your Gmail into a fresh KnowledgeStore on disk,
then writes a markdown report at ``--out`` covering ingestion, schema-field
coverage, chunking quality, dedup signal, channel distribution, the top
contacts leaderboard, and six canned retrieval queries.

What it exercises:
  - GmailConnector → IngestionPipeline → KnowledgeStore end-to-end
  - Every v1 field: source_id, participants_raw, channel, content_hash,
    namespaced thread_id, last_synced, embedding, embedding_model_version
  - BM25 lexical retrieval with source/channel/timestamp filters
  - Dense-embedding sanity: coverage, fixed dimensionality, and intra-thread
    cosine similarity above the random-pair baseline

What it does NOT exercise (deferred):
  - Vector retrieval at query time (embeddings are populated but the
    retrieve() path is still BM25-only)
  - Lexical search over participants_raw (FTS5 hasn't been rebuilt to
    include it)

Usage:
    uv run python scripts/v1_gmail_dogfood.py
    uv run python scripts/v1_gmail_dogfood.py --limit 1000 --since-days 365
    uv run python scripts/v1_gmail_dogfood.py --db /tmp/dogfood.db --out report.md

Read scripts/v1_gmail_dogfood_checklist.md alongside the report — it spells
out which signals matter and when a yellow flag means "stop and fix" vs.
"defer to the retrieval PR."
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Allow running from the repo root without pip-installing.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from openjarvis.connectors._stubs import Document  # noqa: E402
from openjarvis.connectors.embeddings import (  # noqa: E402
    DEFAULT_EMBED_MODEL,
    OllamaEmbedder,
    decode_embedding,
)
from openjarvis.connectors.gmail import GmailConnector  # noqa: E402
from openjarvis.connectors.pipeline import IngestionPipeline  # noqa: E402
from openjarvis.connectors.store import KnowledgeStore  # noqa: E402

import numpy as np  # noqa: E402

# ---------------------------------------------------------------------------
# Detectors for chunk-quality signals
# ---------------------------------------------------------------------------

# Common HTML tags found when text/plain is unavailable and we fall through
# to raw markup. Marketing emails are the worst offenders.
_HTML_MARKERS = re.compile(
    r"<\s*(html|body|div|span|table|td|tr|p|br|a|img|style|head|meta|li|ul|ol)\b",
    re.IGNORECASE,
)

# Lines starting with one or more '>' (the universal email quote convention),
# or the "On <date>, <person> wrote:" prefix Gmail and most clients add.
_QUOTE_LINE = re.compile(r"^\s*>+\s", re.MULTILINE)
_ON_WROTE = re.compile(
    r"^\s*On\b.+\bwrote:\s*$|^-{2,}\s*Original Message\s*-{2,}",
    re.MULTILINE,
)


def _looks_html(text: str) -> bool:
    return bool(_HTML_MARKERS.search(text))


def _has_quotes(text: str) -> bool:
    return bool(_QUOTE_LINE.search(text) or _ON_WROTE.search(text))


# ---------------------------------------------------------------------------
# Tiny markdown helpers — no third-party deps to keep this script portable
# ---------------------------------------------------------------------------


def _h(level: int, text: str) -> str:
    return f"{'#' * level} {text}\n"


def _table(headers: List[str], rows: List[List[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |"]
    out.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out) + "\n"


def _pct(numer: int, denom: int) -> str:
    if denom == 0:
        return "n/a"
    return f"{100.0 * numer / denom:.1f}%"


def _trim(text: str, n: int = 160) -> str:
    """Single-line, length-bounded preview of chunk content."""
    flat = " ".join(text.split())
    return flat if len(flat) <= n else flat[:n] + "…"


# ---------------------------------------------------------------------------
# Sync + ingest
# ---------------------------------------------------------------------------


def collect_docs(connector: GmailConnector, since_days: int, limit: int) -> List[Document]:
    """Pull up to ``limit`` recent Gmail messages as Documents."""
    since = datetime.now(timezone.utc) - timedelta(days=since_days)
    docs: List[Document] = []
    for i, doc in enumerate(connector.sync(since=since), start=1):
        docs.append(doc)
        if i % 50 == 0:
            print(f"  fetched {i} messages...", flush=True)
        if len(docs) >= limit:
            break
    return docs


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------


def section_counts_and_timing(
    docs: List[Document],
    chunks_stored: int,
    sync_seconds: float,
    ingest_seconds: float,
    chunk_lengths: List[int],
    chunks_per_doc: List[int],
) -> str:
    def _percentile(data: List[int], p: float) -> int:
        if not data:
            return 0
        s = sorted(data)
        idx = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
        return s[idx]

    rows = [
        ["docs synced", len(docs)],
        ["chunks stored", chunks_stored],
        ["sync seconds", f"{sync_seconds:.1f}"],
        ["ingest seconds", f"{ingest_seconds:.1f}"],
        ["ingest ms / doc", f"{(ingest_seconds * 1000.0 / max(1, len(docs))):.1f}"],
        ["chunks/doc median", _percentile(chunks_per_doc, 50)],
        ["chunks/doc p90", _percentile(chunks_per_doc, 90)],
        ["chunks/doc max", max(chunks_per_doc) if chunks_per_doc else 0],
        ["chunk length median (chars)", _percentile(chunk_lengths, 50)],
        ["chunk length p90 (chars)", _percentile(chunk_lengths, 90)],
        ["chunk length max (chars)", max(chunk_lengths) if chunk_lengths else 0],
    ]
    return _h(2, "1. Counts & timing") + _table(["metric", "value"], rows) + "\n"


def section_field_coverage(conn: sqlite3.Connection) -> str:
    total = conn.execute(
        "SELECT COUNT(*) FROM knowledge_chunks WHERE deleted_at IS NULL"
    ).fetchone()[0]

    def _coverage(predicate: str) -> Tuple[int, str]:
        n = conn.execute(
            f"SELECT COUNT(*) FROM knowledge_chunks "
            f"WHERE deleted_at IS NULL AND ({predicate})"
        ).fetchone()[0]
        return n, _pct(n, total)

    s_id_n, s_id_pct = _coverage("source_id != ''")
    tid_n, tid_pct = _coverage("thread_id LIKE 'gmail:%'")
    ch_n, ch_pct = _coverage("content_hash != ''")
    pr_n, pr_pct = _coverage("participants_raw != '[]' AND participants_raw != ''")
    chan_n, chan_pct = _coverage("channel != ''")
    ls_n, ls_pct = _coverage("last_synced > 0")

    rows = [
        ["source_id non-empty", s_id_n, s_id_pct, "want 100%"],
        ["thread_id namespaced (gmail:*)", tid_n, tid_pct, "want ~100%"],
        ["content_hash non-empty", ch_n, ch_pct, "want 100%"],
        ["participants_raw populated", pr_n, pr_pct, "want >95%"],
        ["channel populated", chan_n, chan_pct, "expect 80–95%"],
        ["last_synced > 0", ls_n, ls_pct, "want 100%"],
    ]
    return _h(2, "2. v1 field coverage") + _table(
        ["field", "rows", "% of live rows", "expected"], rows
    ) + "\n"


def section_chunking_quality(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT id, content FROM knowledge_chunks WHERE deleted_at IS NULL"
    ).fetchall()
    total = len(rows)
    html_rows = [r for r in rows if _looks_html(r[1])]
    quote_rows = [r for r in rows if _has_quotes(r[1])]
    short_rows = [r for r in rows if len(r[1]) < 50]
    long_rows = [r for r in rows if len(r[1]) > 4000]

    summary = _table(
        ["signal", "rows", "%", "interpretation"],
        [
            [
                "HTML markers in chunk",
                len(html_rows),
                _pct(len(html_rows), total),
                "<5% ok · 5–20% defer · >20% fix before retrieval PR",
            ],
            [
                "quote markers in chunk",
                len(quote_rows),
                _pct(len(quote_rows), total),
                "<10% ok · 10–30% tolerable · >30% needs strip pass",
            ],
            [
                "chunks <50 chars",
                len(short_rows),
                _pct(len(short_rows), total),
                "fragments / signatures",
            ],
            [
                "chunks >4000 chars",
                len(long_rows),
                _pct(len(long_rows), total),
                "splitter not aggressive enough",
            ],
        ],
    )

    longest = sorted(rows, key=lambda r: -len(r[1]))[:5]
    shortest = sorted(rows, key=lambda r: len(r[1]))[:5]

    def _list(items: List[Tuple[str, str]], label: str) -> str:
        out = [f"### {label}\n"]
        for i, (cid, content) in enumerate(items, start=1):
            out.append(f"{i}. `{len(content)}` chars · `{cid[:8]}` · {_trim(content)}\n")
        return "\n".join(out) + "\n"

    return (
        _h(2, "3. Chunking quality")
        + summary
        + "\n"
        + _list(longest, "top 5 longest chunks")
        + _list(shortest, "top 5 shortest chunks")
    )


def section_dedup(conn: sqlite3.Connection) -> str:
    total_rows = conn.execute(
        "SELECT COUNT(*) FROM knowledge_chunks WHERE deleted_at IS NULL"
    ).fetchone()[0]
    unique_hashes = conn.execute(
        "SELECT COUNT(DISTINCT content_hash) FROM knowledge_chunks "
        "WHERE deleted_at IS NULL AND content_hash != ''"
    ).fetchone()[0]
    ratio = total_rows / max(1, unique_hashes)

    repeat_rows = conn.execute(
        """
        SELECT content_hash, COUNT(*) AS n, MIN(content) AS sample
        FROM knowledge_chunks
        WHERE deleted_at IS NULL AND content_hash != ''
        GROUP BY content_hash
        HAVING n > 1
        ORDER BY n DESC
        LIMIT 5
        """
    ).fetchall()

    summary = _table(
        ["metric", "value"],
        [
            ["unique content_hash", unique_hashes],
            ["total live rows", total_rows],
            ["duplicate ratio", f"{ratio:.2f}"],
        ],
    )
    out = _h(2, "4. Dedup signal") + summary + "\n"
    if not repeat_rows:
        out += "_No duplicates._\n"
    else:
        out += "### top 5 most-repeated content_hashes\n"
        for h, n, sample in repeat_rows:
            out += f"- `{n}` rows · `{h[:8]}` · {_trim(sample)}\n"
        out += "\n_Most repeats are signatures, footers, and 'Sent from my iPhone'-style boilerplate. Real-content repeats >5× warrant a content-strip pass._\n"
    return out + "\n"


def section_channels(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(channel, ''), '(none)') AS chan, COUNT(*) AS n
        FROM knowledge_chunks
        WHERE deleted_at IS NULL
        GROUP BY chan
        ORDER BY n DESC
        """
    ).fetchall()
    total = sum(n for _, n in rows)
    table = _table(
        ["channel", "rows", "%"],
        [[c, n, _pct(n, total)] for c, n in rows],
    )
    return _h(2, "5. Channel distribution") + table + "\n"


def section_people_leaderboard(conn: sqlite3.Connection, top_n: int = 20) -> str:
    counts: Counter[str] = Counter()
    for (raw,) in conn.execute(
        "SELECT participants FROM knowledge_chunks WHERE deleted_at IS NULL"
    ):
        try:
            for addr in json.loads(raw):
                if addr:
                    counts[addr] += 1
        except json.JSONDecodeError:
            continue
    rows = [[i + 1, addr, n] for i, (addr, n) in enumerate(counts.most_common(top_n))]
    return _h(2, f"6. People leaderboard (top {top_n})") + _table(
        ["rank", "address", "row count"], rows
    ) + "\n"


def section_embedding_health(
    conn: sqlite3.Connection, embed_model: str, sample_pairs: int = 200
) -> str:
    """Coverage + dimensionality + cosine sanity check on stored embeddings.

    Cosine sanity: chunks sharing a ``thread_id`` are, by construction, from
    the same email conversation and almost always semantically related. The
    mean cosine of intra-thread pairs should sit comfortably above the mean
    cosine of cross-thread pairs sampled at random. If it doesn't, something
    is wrong with how embeddings are being generated or stored.
    """
    total = conn.execute(
        "SELECT COUNT(*) FROM knowledge_chunks WHERE deleted_at IS NULL"
    ).fetchone()[0]
    rows = conn.execute(
        """
        SELECT id, thread_id, embedding, embedding_model_version
        FROM knowledge_chunks
        WHERE deleted_at IS NULL
        """
    ).fetchall()

    populated = [r for r in rows if r[2] is not None]
    versions: Counter[str] = Counter(r[3] for r in populated)
    dims: Counter[int] = Counter()
    vectors: List[Tuple[str, str, np.ndarray]] = []
    for cid, tid, blob, _ver in populated:
        vec = decode_embedding(blob)
        if vec is None or vec.size == 0:
            continue
        dims[int(vec.shape[0])] += 1
        # L2-normalise once so cosine reduces to a dot product.
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            continue
        vectors.append((cid, tid or "", vec / norm))

    coverage_pct = _pct(len(populated), total)
    unique_dims = sorted(dims.keys())
    dim_str = ", ".join(f"{d} ({dims[d]} rows)" for d in unique_dims) or "n/a"
    version_str = ", ".join(f"{v} ({n})" for v, n in versions.most_common()) or "n/a"

    summary_rows = [
        ["chunks with embedding", len(populated), coverage_pct, "want 100%"],
        ["distinct dimensionality", len(unique_dims), dim_str, "want exactly 1"],
        ["embedding_model_version values", len(versions), version_str, "want exactly 1"],
    ]
    summary = _table(["signal", "count", "detail", "expected"], summary_rows)

    # Cosine sanity check — same-thread vs cross-thread pairs.
    import random

    rng = random.Random(0xDA61FF00D)
    by_thread: Dict[str, List[int]] = {}
    for idx, (_cid, tid, _vec) in enumerate(vectors):
        if tid:
            by_thread.setdefault(tid, []).append(idx)
    multi = [idxs for idxs in by_thread.values() if len(idxs) >= 2]

    cosine_block = "\n### Cosine similarity sanity (same-thread vs cross-thread)\n"
    if not multi or len(vectors) < 2:
        cosine_block += (
            "  _not enough multi-chunk threads to evaluate (need ≥1 thread with ≥2 chunks)._\n"
        )
        same_mean: Optional[float] = None
        diff_mean: Optional[float] = None
    else:
        same_sims: List[float] = []
        for _ in range(sample_pairs):
            idxs = rng.choice(multi)
            i, j = rng.sample(idxs, 2)
            same_sims.append(float(vectors[i][2] @ vectors[j][2]))

        diff_sims: List[float] = []
        n = len(vectors)
        attempts = 0
        while len(diff_sims) < sample_pairs and attempts < sample_pairs * 4:
            attempts += 1
            i, j = rng.sample(range(n), 2)
            if vectors[i][1] and vectors[i][1] == vectors[j][1]:
                continue
            diff_sims.append(float(vectors[i][2] @ vectors[j][2]))

        same_mean = statistics.fmean(same_sims)
        diff_mean = statistics.fmean(diff_sims) if diff_sims else None
        gap = (same_mean - diff_mean) if diff_mean is not None else None
        verdict = (
            "ok — intra-thread cosine > cross-thread"
            if (gap is not None and gap > 0.05)
            else "FAIL — embeddings don't separate threads from random pairs"
        )
        cosine_block += _table(
            ["metric", "value"],
            [
                ["pairs sampled (each side)", sample_pairs],
                ["mean cosine, same thread", f"{same_mean:.4f}"],
                [
                    "mean cosine, cross thread",
                    f"{diff_mean:.4f}" if diff_mean is not None else "n/a",
                ],
                ["gap (same − cross)", f"{gap:.4f}" if gap is not None else "n/a"],
                ["verdict", verdict],
            ],
        )

    return (
        _h(2, "8. Embedding health")
        + f"_model: `{embed_model}`_\n\n"
        + summary
        + cosine_block
        + "\n"
    )


def section_canned_queries(store: KnowledgeStore) -> str:
    """Six canned queries that exercise the v1 schema end-to-end."""
    out = [_h(2, "7. Canned retrieval queries")]

    def _fmt_results(results: List[Any]) -> str:
        if not results:
            return "  _no results_\n"
        lines = []
        for r in results[:3]:
            meta = r.metadata or {}
            ts = meta.get("timestamp", "?")
            chan = meta.get("channel") or "(none)"
            sid = meta.get("source_id", "?")
            parts = meta.get("participants") or []
            sender = parts[0] if parts else "?"
            lines.append(
                f"  - score=`{r.score:.2f}` source_id=`{sid[:24]}` channel=`{chan}` "
                f"@`{ts[:19]}` from=`{sender}`\n"
                f"    {_trim(r.content)}"
            )
        return "\n".join(lines) + "\n"

    # q1: bare lexical query
    q1 = "meeting"
    out.append(f"### q1 · `\"{q1}\"` — no filter\n")
    out.append(_fmt_results(store.retrieve(q1, top_k=3)))

    # q2: source filter
    q2 = "review"
    out.append(f"\n### q2 · `\"{q2}\"` — source=gmail\n")
    out.append(_fmt_results(store.retrieve(q2, top_k=3, source="gmail")))

    # q3: time-range filter
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    q3 = "update"
    out.append(f"\n### q3 · `\"{q3}\"` — since={cutoff.date()}\n")
    out.append(_fmt_results(store.retrieve(q3, top_k=3, since=cutoff)))

    # q4: longest thread retrieval
    out.append("\n### q4 · longest thread\n")
    cur = store._conn.execute(
        """
        SELECT thread_id, COUNT(*) AS n
        FROM knowledge_chunks
        WHERE deleted_at IS NULL AND thread_id != ''
        GROUP BY thread_id
        ORDER BY n DESC
        LIMIT 1
        """
    ).fetchone()
    if cur:
        thread_id, n = cur
        rows = store._conn.execute(
            "SELECT timestamp, source_id, content "
            "FROM knowledge_chunks WHERE thread_id = ? AND deleted_at IS NULL "
            "ORDER BY timestamp",
            (thread_id,),
        ).fetchall()
        out.append(f"thread `{thread_id}` · `{n}` rows\n\n")
        for ts, sid, content in rows[:5]:
            out.append(f"  - `{(ts or '')[:19]}` · `{sid[:24]}` · {_trim(content, 120)}\n")
        if len(rows) > 5:
            out.append(f"  - _… {len(rows) - 5} more_\n")
    else:
        out.append("  _no thread data_\n")

    # q5: channel=SENT smoke test (raw SQL since retrieve() doesn't take channel)
    out.append("\n### q5 · channel=SENT (raw SQL, retrieve() doesn't filter on channel yet)\n")
    rows = store._conn.execute(
        "SELECT timestamp, source_id, content "
        "FROM knowledge_chunks WHERE channel = 'SENT' AND deleted_at IS NULL "
        "ORDER BY timestamp DESC LIMIT 3"
    ).fetchall()
    if rows:
        for ts, sid, content in rows:
            out.append(f"  - `{(ts or '')[:19]}` · `{sid[:24]}` · {_trim(content, 120)}\n")
    else:
        out.append("  _no SENT mail in sample_\n")

    # q6: dedup verification
    out.append("\n### q6 · dedup verification (most-repeated content_hash)\n")
    repeat = store._conn.execute(
        """
        SELECT content_hash, COUNT(*) AS n FROM knowledge_chunks
        WHERE deleted_at IS NULL AND content_hash != ''
        GROUP BY content_hash HAVING n > 1
        ORDER BY n DESC LIMIT 1
        """
    ).fetchone()
    if repeat:
        h, n = repeat
        siblings = store._conn.execute(
            "SELECT timestamp, source_id, channel "
            "FROM knowledge_chunks WHERE content_hash = ? AND deleted_at IS NULL "
            "ORDER BY timestamp DESC LIMIT 5",
            (h,),
        ).fetchall()
        out.append(f"hash=`{h[:16]}…` shared by `{n}` rows\n\n")
        for ts, sid, chan in siblings:
            out.append(f"  - `{(ts or '')[:19]}` · `{sid[:24]}` · channel=`{chan or '(none)'}`\n")
    else:
        out.append("  _no duplicates in sample_\n")

    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=500,
        help="max number of Gmail messages to ingest (default 500)",
    )
    parser.add_argument(
        "--since-days", type=int, default=180,
        help="only sync mail newer than N days (default 180)",
    )
    parser.add_argument(
        "--db", type=str, default="~/.openjarvis/dogfood_v1.db",
        help=(
            "path to KnowledgeStore. Existing rows are preserved by default; "
            "pipeline dedup (by doc_id) skips messages already ingested. Pass "
            "--clobber to wipe and rebuild from scratch."
        ),
    )
    parser.add_argument(
        "--clobber", action="store_true",
        help="Delete the target db (and its WAL sidecars) before ingest.",
    )
    parser.add_argument(
        "--out", type=str, default="dogfood_report.md",
        help="path to write markdown report (default dogfood_report.md)",
    )
    parser.add_argument(
        "--credentials", type=str, default="",
        help="override Gmail OAuth credentials path (default: ~/.openjarvis/connectors/gmail.json)",
    )
    parser.add_argument(
        "--embed-model", type=str, default=DEFAULT_EMBED_MODEL,
        help=f"Ollama embedding model tag (default: {DEFAULT_EMBED_MODEL})",
    )
    parser.add_argument(
        "--no-embed", action="store_true",
        help="skip embedding generation (useful when iterating on non-embedding code)",
    )
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if args.clobber:
        if db_path.exists():
            db_path.unlink()
        for sidecar in (
            db_path.with_suffix(db_path.suffix + "-wal"),
            db_path.with_suffix(db_path.suffix + "-shm"),
        ):
            if sidecar.exists():
                sidecar.unlink()
    elif db_path.exists():
        print(
            f"Reusing existing KnowledgeStore at {db_path} — pipeline dedup "
            f"will skip already-ingested doc_ids. Pass --clobber to wipe.",
            file=sys.stderr,
        )

    connector = GmailConnector(credentials_path=args.credentials)
    if not connector.is_connected():
        print(
            "Gmail not connected. Run `jarvis connect gdrive` (or your equivalent\n"
            "OAuth flow) to drop credentials at ~/.openjarvis/connectors/gmail.json,\n"
            "then re-run this script.",
            file=sys.stderr,
        )
        return 2

    store = KnowledgeStore(db_path=db_path)

    embedder: Optional[OllamaEmbedder] = None
    if not args.no_embed:
        embedder = OllamaEmbedder(model=args.embed_model)
        if not embedder.is_available():
            print(
                f"Ollama daemon or model '{args.embed_model}' not available — "
                f"chunks will be stored without embeddings. Pass --no-embed to suppress.",
                file=sys.stderr,
            )
            embedder = None
        else:
            print(f"Using embedder: {embedder.model_version}")

    pipeline = IngestionPipeline(store, embedder=embedder)

    print(f"Syncing Gmail (since={args.since_days}d, limit={args.limit})...")
    t0 = time.time()
    docs = collect_docs(connector, since_days=args.since_days, limit=args.limit)
    sync_seconds = time.time() - t0
    print(f"  fetched {len(docs)} messages in {sync_seconds:.1f}s")

    if not docs:
        print("No messages fetched — nothing to dogfood. Exiting.", file=sys.stderr)
        return 1

    print("Ingesting into KnowledgeStore...")
    t0 = time.time()
    chunks_stored = pipeline.ingest(docs)
    ingest_seconds = time.time() - t0
    print(f"  ingested {chunks_stored} chunks in {ingest_seconds:.1f}s")

    chunk_lengths: List[int] = [
        row[0] for row in store._conn.execute(
            "SELECT length(content) FROM knowledge_chunks WHERE deleted_at IS NULL"
        )
    ]
    per_doc = Counter(
        row[0] for row in store._conn.execute(
            "SELECT doc_id FROM knowledge_chunks WHERE deleted_at IS NULL"
        )
    )
    chunks_per_doc = list(per_doc.values())

    print(f"Writing report to {args.out}...")
    report = [
        _h(1, "v1 Schema Dogfood Report"),
        f"_generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
        f"db={db_path} · sample={len(docs)} messages_\n\n",
        section_counts_and_timing(
            docs, chunks_stored, sync_seconds, ingest_seconds,
            chunk_lengths, chunks_per_doc,
        ),
        section_field_coverage(store._conn),
        section_chunking_quality(store._conn),
        section_dedup(store._conn),
        section_channels(store._conn),
        section_people_leaderboard(store._conn),
        section_canned_queries(store),
        section_embedding_health(
            store._conn,
            embed_model=(embedder.model_version if embedder else "(skipped)"),
        ),
        "---\n_See `scripts/v1_gmail_dogfood_checklist.md` for triage guidance._\n",
    ]
    Path(args.out).write_text("".join(report), encoding="utf-8")
    print(f"Report written. Inspect with: less {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
