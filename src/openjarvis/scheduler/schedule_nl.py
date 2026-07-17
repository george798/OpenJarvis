"""Natural-language schedule parsing (English -> cron/interval expressions)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True, slots=True)
class ParsedSchedule:
    schedule_type: str  # cron | interval | once
    schedule_value: str
    original: str


_DAY_MAP = {
    "monday": "1",
    "mon": "1",
    "tuesday": "2",
    "tue": "2",
    "tues": "2",
    "wednesday": "3",
    "wed": "3",
    "thursday": "4",
    "thu": "4",
    "thur": "4",
    "thurs": "4",
    "friday": "5",
    "fri": "5",
    "saturday": "6",
    "sat": "6",
    "sunday": "0",
    "sun": "0",
}


def _parse_time(text: str) -> Optional[Tuple[int, int]]:
    """Return (hour, minute) from fragments like '9am', '14:30', 'noon'."""
    lowered = text.lower().strip()
    if lowered in ("noon", "midday"):
        return 12, 0
    if lowered in ("midnight",):
        return 0, 0

    m = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
        lowered,
    )
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    meridiem = m.group(3)
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def parse_natural_schedule(text: str) -> Optional[ParsedSchedule]:
    """Parse common English schedules into cron/interval expressions.

    Examples:
        "every morning at 9am" -> cron ``0 9 * * *``
        "every day at 6:30pm" -> cron ``30 18 * * *``
        "every monday at 8am" -> cron ``0 8 * * 1``
        "every 30 minutes" -> interval ``1800``
        "every 2 hours" -> interval ``7200``
    """
    raw = text.strip()
    if not raw:
        return None

    lowered = raw.lower()

    # Already a cron expression (5 fields)
    parts = lowered.split()
    if len(parts) == 5 and all(p.replace("*", "").replace("/", "").isdigit() or p in "*,-/" for p in parts):
        return ParsedSchedule("cron", raw, raw)

    # Interval: every N minutes/hours
    interval = re.search(
        r"every\s+(\d+)\s*(minute|minutes|min|hour|hours|hr|hrs)\b",
        lowered,
    )
    if interval:
        amount = int(interval.group(1))
        unit = interval.group(2)
        seconds = amount * 60 if unit.startswith("min") else amount * 3600
        if seconds > 0:
            return ParsedSchedule("interval", str(seconds), raw)

    # Daily / weekday schedules with time
    time_match = _parse_time(lowered)
    if time_match is None:
        return None
    hour, minute = time_match

    day_match = re.search(
        r"\bevery\s+(monday|mon|tuesday|tue|tues|wednesday|wed|"
        r"thursday|thu|thur|thurs|friday|fri|saturday|sat|sunday|sun)\b",
        lowered,
    )
    if day_match:
        dow = _DAY_MAP.get(day_match.group(1), "*")
        cron = f"{minute} {hour} * * {dow}"
        return ParsedSchedule("cron", cron, raw)

    if re.search(r"\b(every day|daily|each day|every morning|every evening)\b", lowered):
        cron = f"{minute} {hour} * * *"
        return ParsedSchedule("cron", cron, raw)

    return None


__all__ = ["ParsedSchedule", "parse_natural_schedule"]
