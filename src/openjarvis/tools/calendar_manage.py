"""Agent tool for Google Calendar read/write actions.

Wraps :class:`GCalendarConnector` so chat agents can list, search, accept,
decline, and delete events without hand-rolling ``http_request`` calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


def _strip_event_id(raw: str) -> str:
    """Accept bare ids or ``gcalendar:<id>`` forms."""
    eid = (raw or "").strip()
    if eid.startswith("gcalendar:"):
        eid = eid[len("gcalendar:") :]
    return eid


def _rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@ToolRegistry.register("calendar_manage")
class CalendarManageTool(BaseTool):
    """List / search / accept / decline / delete Google Calendar events."""

    tool_id = "calendar_manage"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calendar_manage",
            description=(
                "Manage Google Calendar events the user has connected via OAuth. "
                "Actions: 'list_today' (events for today), "
                "'search' (keyword search — requires query), "
                "'delete' (delete an event — requires event_id), "
                "'accept' / 'decline' (RSVP — requires event_id). "
                "event_id is the id after 'gcalendar:' in digest/knowledge results."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list_today",
                            "search",
                            "delete",
                            "accept",
                            "decline",
                        ],
                        "description": "Operation to perform.",
                    },
                    "event_id": {
                        "type": "string",
                        "description": (
                            "Event id for delete/accept/decline "
                            "(with or without 'gcalendar:' prefix)."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": "Search keywords for action=search.",
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar id (default 'primary').",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max events for list/search (default 20).",
                    },
                },
                "required": ["action"],
            },
            category="productivity",
        )

    def execute(self, **params: Any) -> ToolResult:
        action = str(params.get("action", "")).strip().lower()
        calendar_id = str(params.get("calendar_id") or "primary").strip() or "primary"
        max_results = max(1, min(int(params.get("max_results") or 20), 50))

        try:
            from openjarvis.connectors.gcalendar import (
                GCalendarConnector,
                _gcal_api_events_list,
            )
            from openjarvis.connectors.google_auth import call_with_refresh
        except Exception as exc:
            return ToolResult(
                tool_name="calendar_manage",
                content=f"Google Calendar connector unavailable: {exc}",
                success=False,
            )

        conn = GCalendarConnector()
        if not conn.is_connected():
            return ToolResult(
                tool_name="calendar_manage",
                content=(
                    "Google Calendar is not connected. Use connector_manage "
                    "action=setup_oauth provider=google, then complete consent."
                ),
                success=False,
            )

        try:
            if action == "list_today":
                now = datetime.now(timezone.utc)
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=1)
                resp = call_with_refresh(
                    _gcal_api_events_list,
                    conn._credentials_path,
                    calendar_id,
                    time_min=_rfc3339(start),
                    time_max=_rfc3339(end),
                    max_results=max_results,
                )
                return self._format_events(resp.get("items") or [], label="today")

            if action == "search":
                query = str(params.get("query") or "").strip()
                if not query:
                    return ToolResult(
                        tool_name="calendar_manage",
                        content="query is required for action=search.",
                        success=False,
                    )
                now = datetime.now(timezone.utc)
                resp = call_with_refresh(
                    _gcal_api_events_list,
                    conn._credentials_path,
                    calendar_id,
                    time_min=_rfc3339(now - timedelta(days=7)),
                    max_results=max_results,
                    query=query,
                )
                return self._format_events(
                    resp.get("items") or [], label=f"search:{query}"
                )

            if action in {"delete", "accept", "decline"}:
                event_id = _strip_event_id(str(params.get("event_id") or ""))
                if not event_id:
                    return ToolResult(
                        tool_name="calendar_manage",
                        content=f"event_id is required for action={action}.",
                        success=False,
                    )
                if action == "delete":
                    conn.delete_event(event_id, calendar_id=calendar_id)
                    msg = f"Deleted calendar event {event_id}."
                elif action == "accept":
                    conn.accept_event(event_id, calendar_id=calendar_id)
                    msg = f"Accepted calendar event {event_id}."
                else:
                    conn.decline_event(event_id, calendar_id=calendar_id)
                    msg = f"Declined calendar event {event_id}."
                return ToolResult(
                    tool_name="calendar_manage",
                    content=msg,
                    success=True,
                )

            return ToolResult(
                tool_name="calendar_manage",
                content=(
                    f"Unknown action '{action}'. Use list_today, search, "
                    "delete, accept, or decline."
                ),
                success=False,
            )
        except Exception as exc:
            return ToolResult(
                tool_name="calendar_manage",
                content=f"Calendar action failed: {exc}",
                success=False,
            )

    def _format_events(
        self, items: List[Dict[str, Any]], *, label: str
    ) -> ToolResult:
        if not items:
            return ToolResult(
                tool_name="calendar_manage",
                content=f"No events found ({label}).",
                success=True,
                metadata={"num_results": 0},
            )
        lines = [f"Google Calendar ({label}) — {len(items)} event(s):", ""]
        for ev in items:
            eid = ev.get("id", "")
            title = ev.get("summary") or "(no title)"
            start = (ev.get("start") or {}).get("dateTime") or (
                ev.get("start") or {}
            ).get("date", "")
            end = (ev.get("end") or {}).get("dateTime") or (
                ev.get("end") or {}
            ).get("date", "")
            loc = ev.get("location") or ""
            lines.append(f"- [{eid}] {start} → {end} — {title}")
            if loc:
                lines.append(f"  location: {loc}")
        return ToolResult(
            tool_name="calendar_manage",
            content="\n".join(lines),
            success=True,
            metadata={"num_results": len(items)},
        )


__all__ = ["CalendarManageTool"]
