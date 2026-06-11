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

    payload = json.dumps(api_key)
    script = f"""<script {MARKER}>
(function() {{
  try {{
    var key = {payload};
    var raw = localStorage.getItem("openjarvis-settings") || "{{}}";
    var settings = JSON.parse(raw);
    // Always sync from container env so a wrong manual paste cannot stick.
    settings.apiKey = key;
    if (!settings.apiUrl) settings.apiUrl = window.location.origin;
    localStorage.setItem("openjarvis-settings", JSON.stringify(settings));
  }} catch (e) {{}}
}})();
</script>"""

    if MARKER in html:
        import re

        updated = re.sub(
            rf"<script {re.escape(MARKER)}>.*?</script>",
            script,
            html,
            count=1,
            flags=re.DOTALL,
        )
        if updated != html:
            index_path.write_text(updated, encoding="utf-8")
            print("[openjarvis] Web UI bootstrap updated (API key resynced)")
        else:
            print("[openjarvis] Web bootstrap present but could not update")
        return

    if "</head>" not in html:
        print("[openjarvis] Web bootstrap skipped: </head> missing")
        return

    index_path.write_text(html.replace("</head>", script + "\n</head>", 1), encoding="utf-8")
    print("[openjarvis] Web UI bootstrap injected (API key for model list)")


if __name__ == "__main__":
    main()
