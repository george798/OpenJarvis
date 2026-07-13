"""Inline web UI bootstrap — API key + PWA cache bust for Docker/local serve."""

from __future__ import annotations

import json
import re

MARKER = 'id="openjarvis-docker-bootstrap"'
SW_CLEAR_KEY = "oj-sw-cleared-v3"

SW_KILLER_JS = """\
(function () {
  try {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.getRegistrations().then(function (regs) {
        regs.forEach(function (r) { r.unregister(); });
      });
    }
    if ("caches" in window) {
      caches.keys().then(function (keys) {
        keys.forEach(function (k) { caches.delete(k); });
      });
    }
  } catch (e) {}
})();
"""


def build_bootstrap_script(api_key: str) -> str:
    """Return an inline <script> that seeds credentials before the app bundle runs."""
    if not api_key:
        return ""
    payload = json.dumps(api_key)
    sw_key = json.dumps(SW_CLEAR_KEY)
    return f"""<script {MARKER}>
(function() {{
  try {{
    var key = {payload};
    window.__OPENJARVIS_API_KEY__ = key;
    var raw = localStorage.getItem("openjarvis-settings") || "{{}}";
    var settings = JSON.parse(raw);
    settings.apiKey = key;
    if (!settings.apiUrl) settings.apiUrl = window.location.origin;
    settings.speechEnabled = true;
    settings.voiceAssistantEnabled = true;
    settings.ttsEnabled = true;
    if (settings.ttsVoiceId === undefined) settings.ttsVoiceId = "onyx";
    localStorage.setItem("openjarvis-settings", JSON.stringify(settings));
    if (!sessionStorage.getItem({sw_key})) {{
      sessionStorage.setItem({sw_key}, "1");
      var reload = false;
      if ("serviceWorker" in navigator) {{
        navigator.serviceWorker.getRegistrations().then(function(regs) {{
          regs.forEach(function(r) {{ r.unregister(); }});
        }});
        reload = true;
      }}
      if ("caches" in window) {{
        caches.keys().then(function(keys) {{
          keys.forEach(function(k) {{ caches.delete(k); }});
        }});
        reload = true;
      }}
      if (reload) {{
        setTimeout(function() {{ location.reload(); }}, 100);
      }}
    }}
  }} catch (e) {{}}
}})();
</script>"""


def inject_bootstrap(html: str, api_key: str) -> str:
    """Insert or replace the bootstrap script at the top of <head>."""
    script = build_bootstrap_script(api_key)
    if not script:
        return html
    cleaned = re.sub(
        rf"<script {re.escape(MARKER)}>.*?</script>\s*",
        "",
        html,
        count=1,
        flags=re.DOTALL,
    )
    return re.sub(
        r"(<head[^>]*>)",
        r"\1\n" + script + "\n",
        cleaned,
        count=1,
        flags=re.IGNORECASE,
    )
