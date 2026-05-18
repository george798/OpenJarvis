"""Proactive Agent — runs on a cron (default 5am local) to autonomously handle
routine tasks based on learned user behavior.

Lifecycle per run
-----------------
1. Load USER.md + MEMORY.md for behavioral context.
2. Collect overnight data from connected sources via ``digest_collect``.
3. Use the LLM to classify each item and propose actions with a tier + permission key.
4. For each proposed action:
   - TRIVIAL tier          → queue + immediately approve
   - Known always_approve  → queue + immediately approve
   - Known always_deny     → skip silently
   - Everything else       → queue as pending, notify user
5. Execute all approved actions via ``execute_pending_actions``.
6. Send the user a concise summary: what was done + numbered list of what needs approval.

Approval reply format (user replies to the notification message):
    ``{action_id} yes``            approve one action
    ``{action_id} no``             deny one action
    ``always yes {action_id}``     approve + remember for this pattern
    ``always no {action_id}``      deny + remember for this pattern
    ``yes all`` / ``no all``       bulk decision

Wire up ``parse_approval_response`` from ``proactive_tools`` in your channel
message handler to process replies without running the full agent.

Scheduling
----------
The agent self-registers a 5am daily cron task when ``register_cron`` is
called from your app startup:

    from openjarvis.agents.proactive_agent import register_cron
    register_cron(scheduler, notification_channel_id="your-channel-id")
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from openjarvis.agents._stubs import AgentContext, AgentResult, ToolUsingAgent
from openjarvis.core.config import load_config
from openjarvis.core.registry import AgentRegistry
from openjarvis.core.types import Message, Role, ToolCall
from openjarvis.tools.approval_store import (
    DECISION_ALWAYS_APPROVE,
    DECISION_ALWAYS_DENY,
    STATUS_APPROVED,
    TIER_HIGH,
    TIER_LOW,
    TIER_MEDIUM,
    TIER_TRIVIAL,
    ApprovalStore,
)
from openjarvis.tools.proactive_tools import get_store, parse_approval_response


_SYSTEM_PROMPT = """You are a proactive personal assistant agent. You have already collected
data from the user's connected sources (email, messages, calendar). Your job is to:

1. Analyze each item and decide what action (if any) should be taken.
2. For each action, output a JSON object in your response inside a ```json ... ``` block.

Each action object must have these fields:
  - action_type: one of email_delete | email_archive | sms_send | sms_draft_reply |
                 calendar_decline | calendar_accept | no_action
  - description: human-readable sentence explaining what you will do
  - payload: dict with the data needed to execute (message_id, contact, body, event_id, etc.)
  - permission_key: pattern string like "email_delete:domain:noreply.github.com"
  - tier: one of trivial | low | medium | high
  - reasoning: one sentence why

Tier guidance:
  trivial — read-only or categorization only, no external effect
  low     — reversible, routine (delete a known-spam sender, archive newsletter)
  medium  — affects another party but is expected (reply to a simple scheduling text)
  high    — sends a message in the user's voice for the first time, or irreversible

Output a JSON array of action objects inside a single ```json ... ``` block.
Only include items where action_type is not 'no_action'.
If nothing needs to be done, output an empty array: ```json [] ```

User context is provided below — use it to tailor decisions to their patterns.
"""


def _load_md_file(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _extract_json_block(text: str) -> Optional[List[Dict[str, Any]]]:
    """Extract the first ```json ... ``` block from LLM output."""
    import re

    match = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return None


@AgentRegistry.register("proactive")
class ProactiveAgent(ToolUsingAgent):
    """Autonomous agent that handles routine tasks based on learned user behavior."""

    agent_id = "proactive"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._notification_channel_id: str = kwargs.pop("notification_channel_id", "")
        self._hours_back: int = kwargs.pop("hours_back", 24)
        self._approval_store: Optional[ApprovalStore] = kwargs.pop("approval_store", None)
        self._timezone: str = kwargs.pop("timezone", "America/Los_Angeles")
        super().__init__(*args, **kwargs)

        # Fall back to config.toml [proactive] section if not explicitly set
        if not self._notification_channel_id:
            try:
                cfg = load_config()
                p = cfg.proactive
                self._notification_channel_id = p.notification_channel
                self._hours_back = p.hours_back
                self._timezone = p.timezone
            except Exception:
                pass

    def _get_already_seen_ids(self, store: ApprovalStore) -> Set[str]:
        """Return doc_ids already present in the approval store (any status).

        Prevents re-proposing items that were already queued, approved, denied,
        or executed in a previous run — even if they're still technically unread.
        The doc_id is stored in payload["doc_id"] when the LLM includes it.
        """
        seen: Set[str] = set()
        try:
            conn = store._conn
            rows = conn.execute("SELECT payload FROM pending_actions").fetchall()
            for (payload_json,) in rows:
                try:
                    payload = json.loads(payload_json) if payload_json else {}
                    doc_id = payload.get("doc_id", "")
                    if doc_id:
                        seen.add(doc_id)
                    # Also track raw message_ids for Gmail
                    msg_id = payload.get("message_id", "")
                    if msg_id:
                        seen.add(f"gmail:{msg_id}")
                except (json.JSONDecodeError, KeyError):
                    pass
        except Exception:
            pass
        return seen

    def _store(self) -> ApprovalStore:
        if self._approval_store is None:
            self._approval_store = get_store()
        return self._approval_store

    def _build_system_prompt(self) -> str:
        user_md = _load_md_file(Path.home() / ".openjarvis" / "USER.md")
        memory_md = _load_md_file(Path.home() / ".openjarvis" / "MEMORY.md")
        now = datetime.now()
        context_block = ""
        if user_md or memory_md:
            context_block = "\n\n---\nUSER CONTEXT:\n"
            if user_md:
                context_block += f"\n{user_md.strip()}\n"
            if memory_md:
                context_block += f"\n{memory_md.strip()}\n"
        return (
            _SYSTEM_PROMPT
            + f"\nToday is {now.strftime('%A, %B %d, %Y')} ({self._timezone})."
            + context_block
        )

    def run(
        self,
        input: str = "",
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        self._emit_turn_start(input or "proactive_run")

        store = self._store()
        store.expire_stale()

        # --- Step 1: Collect data — only items user hasn't acted on ---
        sources = ["gmail", "imessage", "gcalendar", "slack", "google_tasks"]
        seen_ids = self._get_already_seen_ids(store)
        collect_call = ToolCall(
            id="proactive-collect-1",
            name="digest_collect",
            arguments=json.dumps({
                "sources": sources,
                "hours_back": self._hours_back,
                "unacted_only": True,
                "seen_ids": list(seen_ids),
            }),
        )
        collect_result = self._executor.execute(collect_call)
        if not collect_result.success or not collect_result.content.strip():
            self._emit_turn_end(turns=1)
            return AgentResult(
                content="No data collected from connectors — nothing to do.",
                turns=1,
            )

        # --- Step 2: Ask LLM to classify items and propose actions ---
        messages = [
            Message(role=Role.SYSTEM, content=self._build_system_prompt()),
            Message(
                role=Role.USER,
                content=(
                    f"Here is the data collected from the last {self._hours_back} hours:\n\n"
                    f"{collect_result.content}\n\n"
                    "Analyze each item and output the JSON array of proposed actions."
                ),
            ),
        ]
        llm_result = self._generate(messages)
        raw_output = self._strip_think_tags(llm_result.get("content", ""))
        proposed: List[Dict[str, Any]] = _extract_json_block(raw_output) or []

        # --- Step 3: Route each proposed action ---
        auto_approve_ids: List[str] = []
        pending_actions = []

        for item in proposed:
            action_type = item.get("action_type", "")
            tier = item.get("tier", TIER_MEDIUM)
            permission_key = item.get("permission_key", f"{action_type}:default")
            description = item.get("description", "")
            payload = item.get("payload", {})

            if not action_type or action_type == "no_action":
                continue

            # Check remembered permission first
            rule = store.get_permission(permission_key)
            if rule and rule.decision == DECISION_ALWAYS_DENY:
                continue

            # Queue the action
            action = store.queue_action(
                action_type=action_type,
                description=description,
                payload=payload,
                permission_key=permission_key,
                tier=tier,
            )

            if tier == TIER_TRIVIAL or (rule and rule.decision == DECISION_ALWAYS_APPROVE):
                store.update_status(action.id, STATUS_APPROVED)
                auto_approve_ids.append(action.id)
            else:
                pending_actions.append(action)

        # --- Step 4: Execute all auto-approved actions ---
        executed_results: List[Dict[str, Any]] = []
        if auto_approve_ids:
            exec_call = ToolCall(
                id="proactive-exec-1",
                name="execute_pending_actions",
                arguments=json.dumps({"action_ids": auto_approve_ids}),
            )
            exec_result = self._executor.execute(exec_call)
            if exec_result.success and exec_result.content:
                try:
                    executed_results = json.loads(exec_result.content)
                except json.JSONDecodeError:
                    pass

        # --- Step 5: Build and send notification ---
        notification = self._build_notification(executed_results, pending_actions)

        if notification and self._notification_channel_id:
            send_call = ToolCall(
                id="proactive-notify-1",
                name="channel_send",
                arguments=json.dumps({
                    "channel": self._notification_channel_id,
                    "content": notification,
                }),
            )
            self._executor.execute(send_call)
            for action in pending_actions:
                store.update_status(action.id, action.status, notification_sent=True)

        self._emit_turn_end(turns=1)
        return AgentResult(
            content=notification or "Nothing to report.",
            turns=1,
            metadata={
                "auto_executed": len(executed_results),
                "pending_approval": len(pending_actions),
            },
        )

    def _build_notification(
        self,
        executed: List[Dict[str, Any]],
        pending: List[Any],
    ) -> str:
        lines: List[str] = []

        if executed:
            successes = [r for r in executed if r.get("success")]
            failures = [r for r in executed if not r.get("success")]
            lines.append(f"Done automatically ({len(successes)} actions):")
            for r in successes:
                lines.append(f"  ✓ {r['description']}")
            for r in failures:
                lines.append(f"  ✗ {r['description']} — {r.get('message', 'error')}")

        if pending:
            if lines:
                lines.append("")
            lines.append(f"Needs your approval ({len(pending)} actions):")
            for action in pending:
                tier_label = {"low": "low-risk", "medium": "medium", "high": "HIGH"}.get(
                    action.tier, action.tier
                )
                lines.append(f"  [{action.id}] ({tier_label}) {action.description}")
            lines.append("")
            lines.append(
                "Reply with: '{id} yes/no' to decide. "
                "Add 'always' to remember (e.g. 'always yes {id}'). "
                "'yes all' / 'no all' for bulk."
            )

        if not lines:
            return ""
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Convenience: register the 5am cron task
# ---------------------------------------------------------------------------


def register_cron(
    scheduler: Any,
    *,
    notification_channel_id: str = "",
    cron_expr: str = "",
    hours_back: int = 0,
    timezone: str = "",
) -> Any:
    """Register the proactive agent as a daily cron task.

    All defaults are read from ``config.toml [proactive]`` when not explicitly
    passed.  Call this once from app startup after the scheduler is started.

    Parameters
    ----------
    scheduler:
        A ``TaskScheduler`` instance.
    notification_channel_id:
        Override the channel ID from config.  If empty, uses ``notification_channel``
        from ``[proactive]`` in config.toml.
    cron_expr:
        Override the cron schedule.  Defaults to config value (``"0 5 * * *"``).
    hours_back:
        Override hours of data to scan.  Defaults to config value (24).
    timezone:
        Override timezone string.  Defaults to config value.
    """
    try:
        cfg = load_config()
        p = cfg.proactive
        notification_channel_id = notification_channel_id or p.notification_channel
        cron_expr = cron_expr or p.schedule
        hours_back = hours_back or p.hours_back
        timezone = timezone or p.timezone
    except Exception:
        cron_expr = cron_expr or "0 5 * * *"
        hours_back = hours_back or 24
        timezone = timezone or "America/Los_Angeles"

    return scheduler.create_task(
        prompt="Run the proactive agent: collect overnight data, execute approved actions, notify pending approvals.",
        schedule_type="cron",
        schedule_value=cron_expr,
        agent="proactive",
        context_mode="isolated",
        metadata={
            "notification_channel_id": notification_channel_id,
            "hours_back": hours_back,
            "timezone": timezone,
        },
    )
