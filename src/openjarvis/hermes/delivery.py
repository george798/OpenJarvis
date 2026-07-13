"""Deliver scheduled task output to messaging channels."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def deliver_scheduled_result(
    *,
    task_id: str,
    prompt: str,
    result_text: str,
    success: bool,
    error_text: str,
    metadata: dict[str, Any],
    channel_bridge: Any,
    default_channel: str = "",
    default_recipient: str = "",
) -> bool:
    """Send a scheduler run result to a configured channel.

    Task metadata keys (Hermes-style):
        deliver_to: channel adapter id (e.g. ``telegram``, ``discord``)
        deliver_recipient: conversation / sender id on that channel
        no_agent: when true, deliver the prompt itself (reminder mode)
    """
    if channel_bridge is None:
        return False

    meta = metadata or {}
    channel = str(meta.get("deliver_to") or default_channel or "").strip()
    recipient = str(meta.get("deliver_recipient") or default_recipient or "").strip()
    if not channel or not recipient:
        return False

    if meta.get("no_agent"):
        body = prompt
    elif success:
        body = result_text or "(no output)"
    else:
        body = f"Task failed: {error_text or 'unknown error'}"

    header = f"[scheduled task {task_id}]"
    if prompt and not meta.get("no_agent"):
        header = f"{header} {prompt[:120]}"
    message = f"{header}\n\n{body}"

    try:
        sent = channel_bridge.send(
            channel,
            message,
            conversation_id=recipient,
        )
        if not sent:
            logger.warning(
                "Failed to deliver scheduler result to %s:%s",
                channel,
                recipient,
            )
        return bool(sent)
    except Exception:
        logger.exception(
            "Scheduler delivery error for task %s -> %s:%s",
            task_id,
            channel,
            recipient,
        )
        return False
