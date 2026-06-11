#!/usr/bin/env python3
"""Inject OpenJarvis web UI bootstrap so /v1/models auth works on first load."""

from __future__ import annotations

import json
import os
from pathlib import Path

MARKER = 'id="openjarvis-docker-bootstrap"'
INDEX_CANDIDATES = (
    Path("/app/src/openjarvis/server/static/index.html"),
    Path("/app/src/openjarvis/server/static/index.html").resolve(),
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
    if MARKER in html:
        print("[openjarvis] Web bootstrap already present")
        return

    payload = json.dumps(api_key)
    script = f"""<script {MARKER}>
(function() {{
  try {{
    var key = {payload};
    var raw = localStorage.getItem("openjarvis-settings") || "{{}}";
    var settings = JSON.parse(raw);
    if (!settings.apiKey) settings.apiKey = key;
    if (!settings.apiUrl) settings.apiUrl = window.location.origin;
    localStorage.setItem("openjarvis-settings", JSON.stringify(settings));
  }} catch (e) {{}}
}})();
</script>"""

    if "</head>" not in html:
        print("[openjarvis] Web bootstrap skipped: </head> missing")
        return

    index_path.write_text(html.replace("</head>", script + "\n</head>", 1), encoding="utf-8")
    print("[openjarvis] Web UI bootstrap injected (API key for model list)")


if __name__ == "__main__":
    main()
