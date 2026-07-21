"""Tests for calendar_manage agent tool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from openjarvis.tools.calendar_manage import CalendarManageTool, _strip_event_id


def test_strip_event_id() -> None:
    assert _strip_event_id("gcalendar:abc123") == "abc123"
    assert _strip_event_id("abc123") == "abc123"


def test_delete_requires_event_id() -> None:
    tool = CalendarManageTool()
    with patch(
        "openjarvis.connectors.gcalendar.GCalendarConnector"
    ) as mock_cls:
        inst = MagicMock()
        inst.is_connected.return_value = True
        mock_cls.return_value = inst
        result = tool.execute(action="delete")
    assert result.success is False
    assert "event_id" in result.content


def test_delete_calls_connector() -> None:
    tool = CalendarManageTool()
    with patch(
        "openjarvis.connectors.gcalendar.GCalendarConnector"
    ) as mock_cls:
        inst = MagicMock()
        inst.is_connected.return_value = True
        mock_cls.return_value = inst
        result = tool.execute(action="delete", event_id="gcalendar:evt99")
    assert result.success is True
    inst.delete_event.assert_called_once_with("evt99", calendar_id="primary")
    assert "Deleted" in result.content
