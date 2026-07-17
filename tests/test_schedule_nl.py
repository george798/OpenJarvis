"""Tests for natural-language schedule parsing."""

from __future__ import annotations

from openjarvis.scheduler.schedule_nl import parse_natural_schedule


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
