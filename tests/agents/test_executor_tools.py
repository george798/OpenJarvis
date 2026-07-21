"""Tests for tool wiring in AgentExecutor."""

from __future__ import annotations

import functools

from openjarvis.agents.executor import AgentExecutor
from openjarvis.agents.manager import AgentManager
from openjarvis.core.events import EventBus
from openjarvis.core.types import Role
from tests.agents.fake_engine import FakeEngine
from tests.agents.scenario_harness import FakeSystem


def _register_agent():
    """Re-register MonitorOperativeAgent (cleared by autouse fixture)."""
    from openjarvis.agents.monitor_operative import MonitorOperativeAgent
    from openjarvis.core.registry import AgentRegistry

    if not AgentRegistry.contains("monitor_operative"):
        AgentRegistry.register("monitor_operative")(MonitorOperativeAgent)


def test_executor_runs_with_tools_from_config(tmp_path):
    """Executor should resolve tool names from config and complete tick."""
    _register_agent()

    engine = FakeEngine([{"content": "test response"}])
    system = FakeSystem(engine=engine)

    mgr = AgentManager(db_path=str(tmp_path / "test.db"))
    agent = mgr.create_agent(
        "test",
        agent_type="monitor_operative",
        config={
            "system_prompt": "You are a test agent.",
            "tools": ["think"],
            "instruction": "test",
        },
    )
    mgr.send_message(agent["id"], "hello", mode="immediate")

    executor = AgentExecutor(manager=mgr, event_bus=EventBus())
    executor.set_system(system)

    executor.execute_tick(agent["id"])
    result_agent = mgr.get_agent(agent["id"])
    assert result_agent["status"] == "idle"
    assert result_agent["total_runs"] == 1
    mgr.close()


def test_executor_handles_missing_tools(tmp_path):
    """Executor should not crash if tool names don't exist in registry."""
    _register_agent()

    engine = FakeEngine([{"content": "test response"}])
    system = FakeSystem(engine=engine)

    mgr = AgentManager(db_path=str(tmp_path / "test.db"))
    agent = mgr.create_agent(
        "test",
        agent_type="monitor_operative",
        config={
            "system_prompt": "You are a test agent.",
            "tools": ["nonexistent_tool_xyz"],
            "instruction": "test",
        },
    )
    mgr.send_message(agent["id"], "hello", mode="immediate")

    executor = AgentExecutor(manager=mgr, event_bus=EventBus())
    executor.set_system(system)

    executor.execute_tick(agent["id"])
    result_agent = mgr.get_agent(agent["id"])
    assert result_agent["status"] == "idle"
    assert result_agent["total_runs"] == 1
    mgr.close()


def test_executor_handles_string_tools(tmp_path):
    """Executor should handle comma-separated tool string as well as list."""
    _register_agent()

    engine = FakeEngine([{"content": "test response"}])
    system = FakeSystem(engine=engine)

    mgr = AgentManager(db_path=str(tmp_path / "test.db"))
    agent = mgr.create_agent(
        "test",
        agent_type="monitor_operative",
        config={
            "system_prompt": "You are a test agent.",
            "tools": "think,calculator",
            "instruction": "test",
        },
    )
    mgr.send_message(agent["id"], "hello", mode="immediate")

    executor = AgentExecutor(manager=mgr, event_bus=EventBus())
    executor.set_system(system)

    executor.execute_tick(agent["id"])
    result_agent = mgr.get_agent(agent["id"])
    assert result_agent["status"] == "idle"
    mgr.close()


def _register_orchestrator():
    from openjarvis.agents.orchestrator import OrchestratorAgent
    from openjarvis.core.registry import AgentRegistry

    if not AgentRegistry.contains("orchestrator"):
        AgentRegistry.register("orchestrator")(OrchestratorAgent)


def test_executor_passes_max_turns_and_isolates_agent_prompt(tmp_path, monkeypatch):
    """Orchestrator ticks must honor max_turns and keep the agent's own prompt.

    Regression: executor never forwarded max_turns/temperature, and wired
    prompt_builder with the main-brain default_system_prompt so consolidator
    ticks burned turns on host_* tools / identical knowledge_search loops.
    """
    from openjarvis.agents.orchestrator import OrchestratorAgent
    from openjarvis.core.config import JarvisConfig, MemoryFilesConfig

    _register_orchestrator()

    captured: dict = {}
    orig_init = OrchestratorAgent.__init__

    @functools.wraps(orig_init)
    def _spy_init(self, engine, model, **kwargs):
        captured["kwargs"] = dict(kwargs)
        return orig_init(self, engine, model, **kwargs)

    monkeypatch.setattr(OrchestratorAgent, "__init__", _spy_init)

    soul = tmp_path / "SOUL.md"
    soul.write_text("MAIN BRAIN PERSONA — use host_exec freely.", encoding="utf-8")
    cfg = JarvisConfig()
    cfg.agent.default_system_prompt = (
        "MAIN_BRAIN_DEFAULT — you are a general assistant with host tools."
    )
    cfg.agent.context_from_memory = False
    cfg.memory_files = MemoryFilesConfig(
        soul_path=str(soul),
        memory_path=str(tmp_path / "MEMORY.md"),
        user_path=str(tmp_path / "USER.md"),
    )

    engine = FakeEngine([{"content": "Consolidated OK."}])
    system = FakeSystem(engine=engine, config=cfg)

    mgr = AgentManager(db_path=str(tmp_path / "test.db"))
    agent = mgr.create_agent(
        "Memory Consolidator",
        agent_type="orchestrator",
        config={
            "system_prompt": "You are the Memory Consolidator — nightly sleep cycle.",
            "tools": ["think"],
            "instruction": "Consolidate memory.",
            "max_turns": 12,
            "temperature": 0.2,
        },
    )

    executor = AgentExecutor(manager=mgr, event_bus=EventBus())
    executor.set_system(system)
    executor.execute_tick(agent["id"])

    kw = captured["kwargs"]
    assert kw.get("max_turns") == 12
    assert kw.get("temperature") == 0.2
    assert kw.get("system_prompt", "").startswith("You are the Memory Consolidator")

    builder = kw.get("prompt_builder")
    assert builder is not None
    # Specialized agents get persona-only builder — never the main-brain template
    built = builder.build()
    assert "MAIN_BRAIN_DEFAULT" not in built

    # And the messages the agent actually sends must lead with consolidator prompt
    gen_messages = engine.last_messages
    assert gen_messages is not None
    assert gen_messages[0].role == Role.SYSTEM
    sys_content = gen_messages[0].content
    assert sys_content.startswith("You are the Memory Consolidator")
    assert "MAIN_BRAIN_DEFAULT" not in sys_content

    result_agent = mgr.get_agent(agent["id"])
    assert result_agent["status"] == "idle"
    mgr.close()
