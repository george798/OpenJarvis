"""Agent tool for connecting external services (OAuth) from chat.

Lets the user hand OAuth app credentials (e.g. a Google client id/secret)
to the assistant in chat and have the whole connection configured without
touching the settings UI:

1. ``setup_oauth`` stores the client credentials and returns the consent
   URL — the agent should open it on the user's desktop with ``host_open``.
2. The existing server callback (``/v1/connectors/{id}/oauth/callback``)
   exchanges the code and persists access/refresh tokens.
3. ``status`` reports which providers are connected.
4. ``access_token`` refreshes a Google access token and exports it as the
   ``GOOGLE_ACCESS_TOKEN`` env var, so ``http_request`` can call any Google
   API with the header ``Authorization: Bearer $GOOGLE_ACCESS_TOKEN``.
"""

from __future__ import annotations

import os
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

# The URL the *user's browser* reaches the server on (the container cannot
# know its published host port). Override with OPENJARVIS_PUBLIC_URL.
_DEFAULT_PUBLIC_URL = "http://localhost:8000"

# Representative connector id per provider for the /oauth/start route.
_PROVIDER_START_CONNECTOR = {
    "google": "gcalendar",
    "strava": "strava",
    "spotify": "spotify",
}


def _public_url() -> str:
    return os.environ.get("OPENJARVIS_PUBLIC_URL", _DEFAULT_PUBLIC_URL).rstrip("/")


@ToolRegistry.register("connector_manage")
class ConnectorManageTool(BaseTool):
    """Configure and inspect OAuth connections to external services."""

    tool_id = "connector_manage"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="connector_manage",
            description=(
                "Connect external services (Google Calendar/Gmail/Drive, "
                "Strava, Spotify) via OAuth, directly from chat. Actions: "
                "'setup_oauth' (store the user's client_id/client_secret and "
                "get the consent URL — open it with host_open so the user "
                "can approve), 'status' (which providers are connected), "
                "'access_token' (refresh a provider access token and export "
                "it as an env var for http_request, e.g. "
                "$GOOGLE_ACCESS_TOKEN), 'sync' (re-index a connected data "
                "source into knowledge.db — use connector_id gdrive, gmail, "
                "obsidian, etc.; set replace=true to re-extract files after "
                "content fixes), 'sync_status' (poll sync progress). "
                "Providers: google, strava, spotify."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "setup_oauth",
                            "status",
                            "access_token",
                            "sync",
                            "sync_status",
                        ],
                        "description": "Operation to perform.",
                    },
                    "provider": {
                        "type": "string",
                        "enum": ["google", "strava", "spotify"],
                        "description": "OAuth provider.",
                    },
                    "connector_id": {
                        "type": "string",
                        "description": (
                            "Data source to sync (sync/sync_status), e.g. "
                            "'gdrive', 'gmail', 'obsidian', 'slack'."
                        ),
                    },
                    "replace": {
                        "type": "boolean",
                        "description": (
                            "For sync: soft-delete existing chunks for this "
                            "source and re-ingest from scratch (default false)."
                        ),
                    },
                    "client_id": {
                        "type": "string",
                        "description": "OAuth client id (setup_oauth only).",
                    },
                    "client_secret": {
                        "type": "string",
                        "description": "OAuth client secret (setup_oauth only).",
                    },
                },
                "required": ["action"],
            },
            category="system",
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "status")
        provider_name = (params.get("provider") or "").strip().lower()
        try:
            if action == "setup_oauth":
                return self._setup_oauth(
                    provider_name,
                    (params.get("client_id") or "").strip(),
                    (params.get("client_secret") or "").strip(),
                )
            if action == "status":
                return self._status()
            if action == "access_token":
                return self._access_token(provider_name)
            if action == "sync":
                return self._sync(
                    (params.get("connector_id") or "").strip(),
                    replace=bool(params.get("replace", False)),
                )
            if action == "sync_status":
                return self._sync_status((params.get("connector_id") or "").strip())
        except Exception as exc:
            return self._fail(f"connector_manage {action} failed: {exc}")
        return self._fail(f"Unknown action: {action}.")

    # ------------------------------------------------------------------

    def _setup_oauth(
        self, provider_name: str, client_id: str, client_secret: str
    ) -> ToolResult:
        from openjarvis.connectors.oauth import (
            OAUTH_PROVIDERS,
            get_client_credentials,
            save_client_credentials,
        )

        provider = OAUTH_PROVIDERS.get(provider_name)
        if provider is None:
            return self._fail(
                f"Unknown provider '{provider_name}'. "
                f"Available: {', '.join(OAUTH_PROVIDERS)}."
            )

        if client_id and client_secret:
            save_client_credentials(provider, client_id, client_secret)
        elif not get_client_credentials(provider):
            return self._fail(
                f"No stored client credentials for {provider.display_name} "
                f"and none provided. Ask the user to create them at "
                f"{provider.setup_url} ({provider.setup_hint}) and share the "
                "client_id and client_secret here."
            )

        connector_id = _PROVIDER_START_CONNECTOR[provider_name]
        base = _public_url()
        start_url = f"{base}/v1/connectors/{connector_id}/oauth/start"
        # Browser links cannot carry a Bearer header; the auth middleware
        # accepts the API key as ?token= for exactly this flow.
        api_key = os.environ.get("OPENJARVIS_API_KEY", "")
        if api_key:
            start_url += f"?token={api_key}"
        redirect_uri = f"{base}/v1/connectors/{connector_id}/oauth/callback"

        return self._ok(
            f"{provider.display_name} client credentials saved.\n"
            f"1. Make sure this redirect URI is authorized in the OAuth app "
            f"({provider.setup_url}): {redirect_uri}\n"
            f"2. Open this consent URL on the user's desktop (use host_open):"
            f"\n{start_url}\n"
            "3. After the user approves, tokens are stored automatically — "
            "verify with connector_manage action='status'."
        )

    def _status(self) -> ToolResult:
        from openjarvis.connectors.oauth import (
            _CONNECTORS_DIR,
            OAUTH_PROVIDERS,
            get_client_credentials,
            load_tokens,
        )

        lines: list[str] = []
        for name, provider in OAUTH_PROVIDERS.items():
            has_client = get_client_credentials(provider) is not None
            has_tokens = False
            for filename in provider.credential_files:
                tokens = load_tokens(str(_CONNECTORS_DIR / filename))
                if tokens and (
                    tokens.get("refresh_token") or tokens.get("access_token")
                ):
                    has_tokens = True
                    break
            if has_tokens:
                state = "connected"
            elif has_client:
                state = "client credentials saved, awaiting user consent"
            else:
                state = "not configured"
            lines.append(f"- {name} ({provider.display_name}): {state}")
        return self._ok(
            "OAuth provider status:\n"
            + "\n".join(lines)
            + "\n\nUse setup_oauth to configure a provider, or access_token "
            "to get a fresh token for API calls."
        )

    def _access_token(self, provider_name: str) -> ToolResult:
        if provider_name != "google":
            return self._fail(
                "access_token currently supports provider='google' only."
            )
        from openjarvis.connectors.oauth import (
            _CONNECTORS_DIR,
            refresh_google_token,
        )

        token = refresh_google_token(str(_CONNECTORS_DIR / "google.json"))
        if not token:
            return self._fail(
                "Could not refresh a Google access token. Run "
                "setup_oauth first (and complete the browser consent)."
            )
        os.environ["GOOGLE_ACCESS_TOKEN"] = token
        return self._ok(
            "Fresh Google access token exported as $GOOGLE_ACCESS_TOKEN "
            "(valid ~1 hour). Call Google APIs with http_request using the "
            "header {'Authorization': 'Bearer $GOOGLE_ACCESS_TOKEN'} — for "
            "example GET https://www.googleapis.com/calendar/v3/calendars/"
            "primary/events?maxResults=10 for upcoming calendar events."
        )

    def _sync(self, connector_id: str, *, replace: bool = False) -> ToolResult:
        if not connector_id:
            return self._fail(
                "connector_id is required for sync (e.g. 'gdrive', 'obsidian')."
            )
        import threading
        import time

        import openjarvis.connectors  # noqa: F401 — register connectors
        from openjarvis.connectors.pipeline import IngestionPipeline
        from openjarvis.connectors.store import KnowledgeStore
        from openjarvis.connectors.sync_engine import SyncEngine
        from openjarvis.core.registry import ConnectorRegistry

        if not ConnectorRegistry.contains(connector_id):
            return self._fail(
                f"Unknown connector '{connector_id}'. "
                f"Available: {', '.join(sorted(ConnectorRegistry.keys()))}."
            )
        inst = ConnectorRegistry.get(connector_id)()
        if not inst.is_connected():
            return self._fail(
                f"Connector '{connector_id}' is not connected. "
                "Connect it in Data Sources first."
            )

        store = KnowledgeStore()
        engine = SyncEngine(pipeline=IngestionPipeline(store=store))
        if replace:
            now = time.time()
            deleted = store._conn.execute(
                "UPDATE knowledge_chunks SET deleted_at = ? "
                "WHERE source = ? AND deleted_at IS NULL",
                (now, connector_id),
            ).rowcount
            store._conn.commit()
            engine._conn.execute(
                "DELETE FROM sync_state WHERE connector_id = ?",
                (connector_id,),
            )
            engine._conn.commit()
            replace_note = f" Replaced {deleted} prior chunk(s)."
        else:
            replace_note = ""

        def _run() -> None:
            try:
                s = KnowledgeStore()
                bg_engine = SyncEngine(pipeline=IngestionPipeline(store=s))
                bg_engine.sync(inst)
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()
        return self._ok(
            f"Started background sync for '{connector_id}'.{replace_note} "
            "Poll with connector_manage action=sync_status. When complete, "
            "search lesson content via knowledge_search with "
            f"source='{connector_id}'. To distill into reusable skills, use "
            "skill_manage create after reviewing search results."
        )

    def _sync_status(self, connector_id: str) -> ToolResult:
        if not connector_id:
            return self._fail("connector_id is required for sync_status.")
        from openjarvis.connectors.pipeline import IngestionPipeline
        from openjarvis.connectors.store import KnowledgeStore
        from openjarvis.connectors.sync_engine import SyncEngine

        cp = SyncEngine(
            pipeline=IngestionPipeline(store=KnowledgeStore()),
        ).get_checkpoint(connector_id)
        chunks = KnowledgeStore()._conn.execute(
            "SELECT COUNT(*) FROM knowledge_chunks "
            "WHERE source = ? AND deleted_at IS NULL",
            (connector_id,),
        ).fetchone()[0]
        if not cp:
            return self._ok(
                f"{connector_id}: no sync checkpoint yet. "
                f"Active chunks in knowledge.db: {chunks}."
            )
        err = cp.get("error")
        return self._ok(
            f"{connector_id} sync status:\n"
            f"- items_synced (checkpoint): {cp.get('items_synced', 0)}\n"
            f"- active chunks in knowledge.db: {chunks}\n"
            f"- last_sync: {cp.get('last_sync')}\n"
            f"- error: {err or 'none'}"
        )

    def _ok(self, content: str) -> ToolResult:
        return ToolResult(tool_name=self.spec.name, success=True, content=content)

    def _fail(self, content: str) -> ToolResult:
        return ToolResult(tool_name=self.spec.name, success=False, content=content)


__all__ = ["ConnectorManageTool"]
