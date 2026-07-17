"""Outbound self-improving skill loop.

When the agent solves a complex task, it writes a reusable skill document so
it never has to figure that out again (behaviour inspired by
NousResearch/hermes-agent). This module implements that reflection step for
OpenJarvis agents.

After a tool-using agent finishes a task with enough successful tool calls,
``maybe_learn_skill`` asks the same engine to decide — in one cheap extra
call — whether the task is a repeatable procedure, and if so distills it
into a skill TOML via ``SkillManageTool``. Runs on a daemon thread so the
user-facing response is never delayed.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from openjarvis.agents._stubs import AgentResult

logger = logging.getLogger(__name__)

_REFLECT_PROMPT = """You are the skill-learning module of Jarvis. A task was \
just completed using tools. Decide whether the procedure is REUSABLE — i.e. \
the same sequence of tool calls (with different arguments) would help with \
similar future requests. One-off lookups, casual questions, and failed \
attempts are NOT reusable.

Task the user asked for:
{task}

Tool calls that were executed (name -> success):
{trace}

Existing skills (do not duplicate): {existing}

Respond with ONLY a JSON object, no other text:
{{"reusable": true/false,
 "name": "short_snake_case_name",
 "description": "One sentence: when to use this skill and what it does.",
 "steps": [{{"tool_name": "...", "arguments_template": "hints for the arguments"}}]}}

If not reusable, respond exactly: {{"reusable": false}}"""

_learning_lock = threading.Lock()
_in_flight: set[str] = set()


def _load_settings() -> tuple[bool, int, str]:
    try:
        from openjarvis.core.config import load_config

        cfg = load_config()
        skills = getattr(cfg, "skills", None)
        if skills is None or not getattr(skills, "enabled", True):
            return False, 3, "~/.openjarvis/skills/"
        return (
            bool(getattr(skills, "learn_from_tasks", True)),
            int(getattr(skills, "learn_min_tool_calls", 3)),
            str(getattr(skills, "skills_dir", "~/.openjarvis/skills/")),
        )
    except Exception:
        return True, 3, "~/.openjarvis/skills/"


def _existing_skill_names(skills_dir: str) -> list[str]:
    from pathlib import Path

    d = Path(skills_dir).expanduser()
    if not d.exists():
        return []
    return sorted(f.stem for f in d.glob("*.toml"))


def _extract_json(text: str) -> Optional[dict]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _reflect_and_store(
    engine: Any, model: str, task: str, result: "AgentResult", skills_dir: str
) -> None:
    key = task.strip()[:200].lower()
    with _learning_lock:
        if key in _in_flight:
            return
        _in_flight.add(key)
    try:
        trace_lines = [
            f"- {tr.tool_name} -> {'ok' if tr.success else 'FAILED'}"
            for tr in result.tool_results
        ]
        existing = _existing_skill_names(skills_dir)

        from openjarvis.core.types import Message, Role

        prompt = _REFLECT_PROMPT.format(
            task=task[:1500],
            trace="\n".join(trace_lines[:30]),
            existing=", ".join(existing[:50]) or "(none)",
        )
        response = engine.generate(
            [Message(role=Role.USER, content=prompt)],
            model=model,
            temperature=0.1,
            max_tokens=800,
        )
        parsed = _extract_json(response.get("content", "") or "")
        if not parsed or not parsed.get("reusable"):
            return

        name = re.sub(r"[^a-z0-9_]", "", str(parsed.get("name", "")).lower())
        if not name or name in existing:
            return
        description = str(parsed.get("description", ""))[:500]
        steps = parsed.get("steps") or []
        if not isinstance(steps, list) or not steps:
            return

        from openjarvis.tools.skill_manage import SkillManageTool

        tool = SkillManageTool(skills_dir=skills_dir)
        outcome = tool.execute(
            action="create",
            name=name,
            description=description,
            steps=[s for s in steps if isinstance(s, dict)],
        )
        if outcome.success:
            logger.info("Skill loop: learned new skill '%s'", name)
    except Exception as exc:
        logger.debug("Skill loop reflection failed: %s", exc)
    finally:
        with _learning_lock:
            _in_flight.discard(key)


def maybe_learn_skill(agent: Any, task: str, result: "AgentResult") -> None:
    """Fire-and-forget: learn a reusable skill from a completed task.

    Call after an agent run completes. No-op unless the task used enough
    successful tool calls and ``skills.learn_from_tasks`` is enabled.
    """
    try:
        if not task or result is None:
            return
        if result.metadata and result.metadata.get("max_turns_exceeded"):
            return
        successful = [tr for tr in (result.tool_results or []) if tr.success]
        enabled, min_calls, skills_dir = _load_settings()
        if not enabled or len(successful) < min_calls:
            return
        # Don't learn from tasks that were mostly failures
        if len(successful) < len(result.tool_results) / 2:
            return
        engine = getattr(agent, "_engine", None)
        model = getattr(agent, "_model", "") or getattr(agent, "model", "")
        if engine is None or not model:
            return
        thread = threading.Thread(
            target=_reflect_and_store,
            args=(engine, model, task, result, skills_dir),
            daemon=True,
            name="skill-loop-reflect",
        )
        thread.start()
    except Exception as exc:
        logger.debug("Skill loop skipped: %s", exc)


__all__ = ["maybe_learn_skill"]
