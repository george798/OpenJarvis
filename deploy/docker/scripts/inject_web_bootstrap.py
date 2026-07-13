#!/usr/bin/env python3
"""Inject OpenJarvis web UI bootstrap so /v1/models auth works on first load."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Allow import when run from /app/deploy/docker/scripts/
_REPO_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from openjarvis.server.web_ui_bootstrap import MARKER, inject_bootstrap  # noqa: E402

INDEX_CANDIDATES = (
    Path("/app/src/openjarvis/server/static/index.html"),
    Path(__file__).resolve().parents[3] / "src/openjarvis/server/static/index.html",
)


def main() -> None:
    api_key = os.environ.get("OPENJARVIS_API_KEY", "").strip()
    if not api_key:
        return

    index_path = next((p for p in INDEX_CANDIDATES if p.is_file()), None)
    if index_path is None:
        print("[openjarvis] Web bootstrap skipped: index.html not found")
        return

    html = index_path.read_text(encoding="utf-8")
    updated = inject_bootstrap(html, api_key)
    if updated != html:
        index_path.write_text(updated, encoding="utf-8")
        print("[openjarvis] Web UI bootstrap injected (API key for model list)")
    elif MARKER in html:
        print("[openjarvis] Web bootstrap already present")
    else:
        print("[openjarvis] Web bootstrap skipped: <head> missing")


if __name__ == "__main__":
    main()
