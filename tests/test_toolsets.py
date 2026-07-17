"""Tests for per-platform tool restrictions."""

from __future__ import annotations

from openjarvis.core.config import JarvisConfig, ToolsetsConfig
from openjarvis.core.toolsets import normalize_platform, resolve_tool_names


class TestToolsets:
    def test_normalize_platform(self):
        assert normalize_platform("telegram_bot") == "telegram"
        assert normalize_platform("discord") == "discord"

    def test_cli_allows_all(self):
        cfg = JarvisConfig()
        cfg.toolsets = ToolsetsConfig(enabled=True, cli="*")
        cfg.agent.tools = "think,calculator,shell_exec"
        assert resolve_tool_names("cli", cfg) is None

    def test_telegram_restricts_tools(self):
        cfg = JarvisConfig()
        cfg.toolsets = ToolsetsConfig(enabled=True)
        cfg.agent.tools = (
            "think,calculator,browser_navigate,shell_exec,knowledge_search"
        )
        allowed = resolve_tool_names("telegram", cfg)
        assert allowed is not None
        assert "browser_navigate" not in allowed
        assert "shell_exec" not in allowed
        assert "think" in allowed
