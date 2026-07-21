#!/usr/bin/env python3
"""Verify the hybrid Docker runtime has every package agent tools need.

Catches the 'ModuleNotFoundError mid-chat' class of bugs (e.g. missing orjson)
at **image build** time instead of during a user turn.

Usage:
  python deploy/docker/scripts/verify_hybrid_deps.py          # hard-fail
  python deploy/docker/scripts/verify_hybrid_deps.py --soft  # warn only
  python deploy/docker/scripts/verify_hybrid_deps.py --json  # machine-readable

Exit codes:
  0 — all required imports ok
  1 — one or more required imports missing (unless --soft)
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Probe:
    """One import check tied to the feature that needs it."""

    module: str
    feature: str
    required: bool = True
    # Optional attribute to touch after import (forces lazy C-ext load).
    attr: Optional[str] = None


# Required = hybrid image must ship these or configured tools die at runtime.
# Optional = nice-to-have / unused by default config; warn only.
PROBES: Tuple[Probe, ...] = (
    # --- serve / API ---
    Probe("fastapi", "server API"),
    Probe("uvicorn", "server API"),
    Probe("pydantic", "server API"),
    Probe("httpx", "connectors / MCP HTTP / tools"),
    Probe("tomlkit", "config_manage"),
    Probe("orjson", "litellm/rdflib JSON fast-path (historically crashed tools)"),
    # --- inference ---
    Probe("openai", "cloud inference / image_generate"),
    Probe("anthropic", "cloud inference"),
    Probe("litellm", "cloud routing"),
    # --- memory / retrieval ---
    Probe("faiss", "memory-faiss / dense retrieval"),
    Probe("sentence_transformers", "embeddings"),
    Probe("numpy", "faiss / speech / embeddings"),
    # --- speech ---
    Probe("faster_whisper", "audio_transcribe"),
    Probe("kokoro", "text_to_speech fallback"),
    Probe("soundfile", "text_to_speech fallback"),
    # --- browser / search / schedule ---
    Probe("playwright", "browser_* tools"),
    Probe("tavily", "web_search (Tavily)"),
    Probe("ddgs", "web_search (DDGS fallback)"),
    Probe("croniter", "schedule_task cron parsing"),
    # --- documents / vault ---
    Probe("pdfplumber", "pdf_extract / Drive PDF ingest"),
    Probe("cryptography", "credential_manage encrypted vault", attr="fernet"),
    # --- MCP surface ---
    Probe("mcp", "MCP SSE bridge (:8888) + markitdown-mcp"),
    Probe("markitdown", "markitdown MCP server"),
    Probe("markitdown_mcp", "markitdown MCP server entrypoint"),
    # --- learning (config optimizer=dspy) ---
    Probe("dspy", "learning.skills DSPy optimizer"),
    # --- optional / not required for current hybrid tool list ---
    Probe("googleapiclient", "channels/gmail (connectors use httpx)", required=False),
    Probe("rank_bm25", "legacy BM25 extra (Rust BM25 used instead)", required=False),
    Probe("docker", "sandbox-docker / code_interpreter_docker", required=False),
    Probe("deepgram", "speech-deepgram extra", required=False),
)


def _probe_one(p: Probe) -> Tuple[bool, str]:
    try:
        mod = importlib.import_module(p.module)
        if p.attr:
            # cryptography.fernet is a submodule; others may be attributes.
            if p.module == "cryptography" and p.attr == "fernet":
                importlib.import_module("cryptography.fernet")
            else:
                getattr(mod, p.attr)
        return True, "ok"
    except Exception as exc:  # noqa: BLE001 — report any import failure
        return False, f"{type(exc).__name__}: {exc}"


def _check_playwright_browsers() -> Tuple[bool, str]:
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/playwright"))
    if not root.is_dir():
        return False, f"PLAYWRIGHT_BROWSERS_PATH missing: {root}"
    chromium = list(root.glob("chromium-*"))
    if not chromium:
        return False, f"no chromium-* under {root}"
    return True, f"found {chromium[0].name}"


def run_probes(
    probes: Sequence[Probe] = PROBES,
    *,
    check_browsers: bool = False,
) -> List[dict]:
    results: List[dict] = []
    for p in probes:
        ok, detail = _probe_one(p)
        results.append(
            {
                **asdict(p),
                "ok": ok,
                "detail": detail,
            }
        )
    if check_browsers:
        ok, detail = _check_playwright_browsers()
        results.append(
            {
                "module": "playwright.chromium",
                "feature": "browser_* Chromium binary",
                "required": True,
                "attr": None,
                "ok": ok,
                "detail": detail,
            }
        )
    return results


def _print_human(results: Iterable[dict], *, soft: bool) -> int:
    missing_required = []
    missing_optional = []
    for r in results:
        status = "OK  " if r["ok"] else "MISS"
        req = "required" if r["required"] else "optional"
        line = f"  [{status}] {r['module']:<28} ({req}) — {r['feature']}"
        if not r["ok"]:
            line += f"\n           {r['detail']}"
            (missing_required if r["required"] else missing_optional).append(r)
        print(line)

    if missing_optional:
        print(
            f"\n[openjarvis] {len(missing_optional)} optional dep(s) missing "
            "(safe to ignore unless you use that feature)."
        )
    if missing_required:
        print(
            f"\n[openjarvis] {len(missing_required)} REQUIRED dep(s) missing. "
            "Install with: uv pip install --system '.[hybrid]'"
        )
        for r in missing_required:
            print(f"  - {r['module']}  needed by: {r['feature']}")
        return 0 if soft else 1

    print("\n[openjarvis] hybrid dependency check passed.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--soft",
        action="store_true",
        help="Warn on missing required deps but always exit 0 (entrypoint use).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON results on stdout.",
    )
    parser.add_argument(
        "--check-browsers",
        action="store_true",
        help="Also verify Playwright Chromium is present under PLAYWRIGHT_BROWSERS_PATH.",
    )
    args = parser.parse_args(argv)

    results = run_probes(check_browsers=args.check_browsers)
    if args.json:
        missing = [r for r in results if r["required"] and not r["ok"]]
        print(json.dumps({"ok": not missing, "results": results}, indent=2))
        return 0 if (args.soft or not missing) else 1
    return _print_human(results, soft=args.soft)


if __name__ == "__main__":
    sys.exit(main())
