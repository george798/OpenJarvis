"""Agent tool for the encrypted secret vault.

Lets the user hand credentials (API keys, client secrets, tokens) to the
assistant in chat and have them stored encrypted at rest. Values are never
echoed back in full: ``get`` returns a masked preview plus the environment
variable name that other tools (e.g. ``http_request`` headers via ``$VAR``)
can use to reference the plaintext without it entering model context.
"""

from __future__ import annotations

from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "****"
    return value[:4] + "…" + value[-4:]


@ToolRegistry.register("credential_manage")
class CredentialManageTool(BaseTool):
    """Store, list, and delete secrets in the encrypted vault."""

    tool_id = "credential_manage"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="credential_manage",
            description=(
                "Securely store credentials the user shares in chat (API "
                "keys, OAuth client ids/secrets, tokens) in an encrypted "
                "vault. Actions: 'set' (store; also exported as an env var), "
                "'get' (masked preview + env var name), 'list', 'delete'. "
                "Other tools can use a stored secret via its env var, e.g. "
                "http_request header 'Authorization': 'Bearer $MY_API_KEY'. "
                "Never repeat full secret values back to the user."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["set", "get", "list", "delete"],
                        "description": "Operation to perform.",
                    },
                    "key": {
                        "type": "string",
                        "description": (
                            "Secret name, e.g. 'google_client_secret' or "
                            "'openweather_api_key'."
                        ),
                    },
                    "value": {
                        "type": "string",
                        "description": "Secret value (only for set).",
                    },
                },
                "required": ["action"],
            },
            category="system",
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "list")
        key = (params.get("key") or "").strip()
        value = params.get("value") or ""
        try:
            from openjarvis.core import secret_vault as vault
        except ImportError as exc:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content=f"Vault unavailable: {exc}",
            )

        try:
            if action == "set":
                if not key or not value.strip():
                    return self._fail("'key' and 'value' are required for set.")
                var = vault.set_secret(key, value.strip())
                return self._ok(
                    f"Stored secret '{key}' (encrypted at rest). It is "
                    f"available to tools as the environment variable ${var} "
                    f"— e.g. an http_request header value 'Bearer ${var}'."
                )
            if action == "get":
                if not key:
                    return self._fail("'key' is required for get.")
                data = vault.load_vault()
                if key not in data:
                    return self._fail(
                        f"No secret named '{key}'. Use action='list' to see "
                        "stored keys."
                    )
                var = vault.env_var_name(key)
                return self._ok(
                    f"{key}: {_mask(data[key])} (env var: ${var}). Full "
                    "values are never shown — reference the env var instead."
                )
            if action == "list":
                data = vault.load_vault()
                if not data:
                    return self._ok("Vault is empty.")
                lines = [
                    f"- {k}: {_mask(v)} (env: ${vault.env_var_name(k)})"
                    for k, v in sorted(data.items())
                ]
                return self._ok("Stored secrets:\n" + "\n".join(lines))
            if action == "delete":
                if not key:
                    return self._fail("'key' is required for delete.")
                if vault.delete_secret(key):
                    return self._ok(f"Deleted secret '{key}'.")
                return self._fail(f"No secret named '{key}'.")
        except Exception as exc:
            return self._fail(f"credential_manage {action} failed: {exc}")
        return self._fail(f"Unknown action: {action}.")

    def _ok(self, content: str) -> ToolResult:
        return ToolResult(tool_name=self.spec.name, success=True, content=content)

    def _fail(self, content: str) -> ToolResult:
        return ToolResult(tool_name=self.spec.name, success=False, content=content)


__all__ = ["CredentialManageTool"]
