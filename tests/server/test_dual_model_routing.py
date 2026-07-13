"""Tests for dual-model chat routing."""

from __future__ import annotations

from types import SimpleNamespace

from openjarvis.server.dual_model_routing import (
    needs_tool_agent,
    resolve_chat_routing,
)


class _FakeEngine:
    engine_id = "lmstudio"


class _OllamaEngine:
    engine_id = "ollama"


def _config(*, dual=True, tool_model="qwen2.5-coder:7b", response_model="cursor-small"):
    return SimpleNamespace(
        agent=SimpleNamespace(
            dual_model_routing=dual,
            tool_model=tool_model,
            response_model=response_model,
        ),
        intelligence=SimpleNamespace(fallback_model="qwen2.5-coder:7b"),
    )


def test_needs_tool_agent_detects_browse():
    assert needs_tool_agent("Open Reddit and summarize the front page")
    assert needs_tool_agent("search the web for fish audio api")
    assert not needs_tool_agent("What is 2 plus 2?")


def test_cursor_plain_chat_stays_direct():
    routing = resolve_chat_routing(
        _config(),
        _FakeEngine(),
        "cursor-small",
        "Hello Jarvis",
    )
    assert routing.use_agent is False
    assert routing.stream_model == "cursor-small"


def test_cursor_tool_request_uses_ollama_agent():
    routing = resolve_chat_routing(
        _config(),
        _FakeEngine(),
        "cursor-small",
        "Open Reddit",
    )
    assert routing.use_agent is True
    assert routing.agent_model == "qwen2.5-coder:7b"
    assert routing.stream_model == "cursor-small"
    assert routing.polish_model == "cursor-small"


def test_ollama_model_always_uses_agent():
    routing = resolve_chat_routing(
        _config(),
        _OllamaEngine(),
        "qwen2.5-coder:7b",
        "Hello",
    )
    assert routing.use_agent is True
    assert routing.agent_model == "qwen2.5-coder:7b"
    assert routing.polish_model is None
