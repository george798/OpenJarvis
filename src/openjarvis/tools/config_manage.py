"""Self-configuration tool — read and modify OpenJarvis's own config.toml.

Gives the agent introspective access to its platform configuration:
read the raw TOML, get/set individual dotted keys (validated against the
``JarvisConfig`` dataclass tree), and list settable sections. Every write
creates a ``config.toml.bak`` backup first.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

_RESTART_HINT = (
    "Most settings only take effect after a restart. To restart yourself, call "
    "host_exec with: docker compose -f D:\\OpenJarvis\\compose\\docker-compose.yml "
    "restart jarvis  (warn the user first — the current chat session will drop "
    "for ~1 minute)."
)


def _config_path() -> Path:
    from openjarvis.core.config import DEFAULT_CONFIG_PATH

    return Path(os.environ.get("OPENJARVIS_CONFIG", DEFAULT_CONFIG_PATH)).expanduser()


def _coerce_value(value: Any, target_type: type) -> Any:
    """Coerce a value (usually a string from the model) to the field type."""
    if isinstance(value, target_type) and not isinstance(value, str):
        return value
    text = str(value)
    if target_type is bool:
        low = text.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"Invalid boolean: {text!r} (use true/false)")
    if target_type is int:
        return int(text)
    if target_type is float:
        return float(text)
    return text


@ToolRegistry.register("config_manage")
class ConfigManageTool(BaseTool):
    """Read and modify the OpenJarvis platform configuration (config.toml)."""

    tool_id = "config_manage"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="config_manage",
            description=(
                "Read or modify your own OpenJarvis platform configuration "
                "(config.toml). Actions: 'read' (whole file or one [section]), "
                "'get' (one dotted key, e.g. intelligence.temperature), "
                "'set' (change a dotted key; validated and backed up first), "
                "'list_keys' (valid top-level sections). Config changes "
                "usually need a restart to apply."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "get", "set", "list_keys"],
                        "description": "Operation to perform.",
                    },
                    "key": {
                        "type": "string",
                        "description": (
                            "Dotted config key for get/set (e.g. "
                            "'agent.max_turns', 'speech.tts_backend') or a "
                            "section name for read (e.g. 'agent')."
                        ),
                    },
                    "value": {
                        "type": "string",
                        "description": (
                            "New value for set. Booleans as true/false, "
                            "numbers as digits."
                        ),
                    },
                },
                "required": ["action"],
            },
            category="system",
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "read")
        key = (params.get("key") or "").strip()
        try:
            if action == "read":
                return self._read(key)
            if action == "get":
                return self._get(key)
            if action == "set":
                return self._set(key, params.get("value"))
            if action == "list_keys":
                return self._list_keys()
        except Exception as exc:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content=f"config_manage {action} failed: {exc}",
            )
        return ToolResult(
            tool_name=self.spec.name,
            success=False,
            content=f"Unknown action: {action}. Use read, get, set, or list_keys.",
        )

    # ------------------------------------------------------------------

    def _read(self, section: str) -> ToolResult:
        path = _config_path()
        if not path.exists():
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content=f"Config file not found: {path}",
            )
        if not section:
            return ToolResult(
                tool_name=self.spec.name,
                success=True,
                content=path.read_text(encoding="utf-8"),
                metadata={"path": str(path)},
            )
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        node: Any = data
        for part in section.split("."):
            if not isinstance(node, dict) or part not in node:
                return ToolResult(
                    tool_name=self.spec.name,
                    success=False,
                    content=(
                        f"Section '{section}' not found. Top-level sections: "
                        f"{', '.join(sorted(data.keys()))}"
                    ),
                )
            node = node[part]
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            content=json.dumps({section: node}, indent=2, default=str),
        )

    def _get(self, key: str) -> ToolResult:
        if not key:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content="'key' is required for get (e.g. 'agent.max_turns').",
            )
        path = _config_path()
        data: dict = {}
        if path.exists():
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        node: Any = data
        in_file = True
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                in_file = False
                break
        if in_file:
            return ToolResult(
                tool_name=self.spec.name,
                success=True,
                content=json.dumps({key: node}, indent=2, default=str),
            )
        # Fall back to the effective (default) value from the loaded config.
        from openjarvis.core.config import load_config, validate_config_key

        validate_config_key(key)  # raises with helpful message if invalid
        obj: Any = load_config()
        for part in key.split("."):
            obj = getattr(obj, part)
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            content=json.dumps({key: obj}, indent=2, default=str)
            + "\n(not set in config.toml — showing the effective default)",
        )

    def _set(self, key: str, value: Any) -> ToolResult:
        if not key or value is None:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content="Both 'key' and 'value' are required for set.",
            )
        import tomlkit

        from openjarvis.core.config import load_config, validate_config_key

        target_type = validate_config_key(key)
        typed_value = _coerce_value(value, target_type)

        path = _config_path()
        if path.exists():
            shutil.copy2(path, path.with_suffix(".toml.bak"))
            doc = tomlkit.parse(path.read_text(encoding="utf-8"))
        else:
            doc = tomlkit.document()
            path.parent.mkdir(parents=True, exist_ok=True)

        parts = key.split(".")
        current: Any = doc
        for part in parts[:-1]:
            if part not in current:
                current.add(part, tomlkit.table())
            current = current[part]
        old = current.get(parts[-1], None) if hasattr(current, "get") else None
        current[parts[-1]] = typed_value
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")

        # Invalidate the in-process cache so new load_config() calls see it.
        try:
            load_config.cache_clear()
        except Exception:
            pass

        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            content=(
                f"Set {key} = {typed_value!r} (was {old!r}). Backup saved to "
                f"{path.with_suffix('.toml.bak').name}. {_RESTART_HINT}"
            ),
        )

    def _list_keys(self) -> ToolResult:
        from openjarvis.core.config import _SETTABLE_SECTIONS

        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            content=(
                "Settable top-level config sections:\n- "
                + "\n- ".join(sorted(_SETTABLE_SECTIONS))
                + "\n\nUse action='read' with key='<section>' to inspect one, "
                "then action='set' with a dotted key to change a value."
            ),
        )


__all__ = ["ConfigManageTool"]
