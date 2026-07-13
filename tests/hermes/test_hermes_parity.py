"""Tests for Hermes parity helpers."""

from __future__ import annotations

from openjarvis.core.config import JarvisConfig, ToolsetsConfig
from openjarvis.hermes.schedule_nl import parse_natural_schedule
from openjarvis.hermes.toolsets import normalize_platform, resolve_tool_names


class TestScheduleNL:
    def test_every_morning(self):
        parsed = parse_natural_schedule("every morning at 9am")
        assert parsed is not None
        assert parsed.schedule_type == "cron"
        assert parsed.schedule_value == "0 9 * * *"

    def test_every_30_minutes(self):
        parsed = parse_natural_schedule("every 30 minutes")
        assert parsed is not None
        assert parsed.schedule_type == "interval"
        assert parsed.schedule_value == "1800"

    def test_monday_schedule(self):
        parsed = parse_natural_schedule("every monday at 8am")
        assert parsed is not None
        assert parsed.schedule_value == "0 8 * * 1"


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
