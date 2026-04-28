"""Best-effort parser for user-typed reminder times.

Supports these forms:

    2026-05-01            -> that date at 9:00 local
    2026-05-01 17:00      -> exact wall time
    2026-05-01 5pm        -> exact wall time
    today 17:00           -> today at 17:00
    tomorrow 9am          -> tomorrow at 09:00
    fri 5pm / friday 5pm  -> next Friday at 17:00
    in 2h / in 30m / in 1d -> relative offset

If `dateparser` is installed, we delegate to it for anything we don't
recognise — but we never *require* it.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Optional


_WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def parse_when(text: str, *, now: Optional[_dt.datetime] = None) -> Optional[_dt.datetime]:
    """Return an aware local datetime, or None if unparseable.
    `text` is whatever the user typed."""
    if not text:
        return None
    s = text.strip().lower()
    if not s:
        return None
    if now is None:
        now = _dt.datetime.now().astimezone()

    # in <n><unit>
    m = re.fullmatch(r"in\s+(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|wk|week|weeks)", s)
    if m:
        n = int(m.group(1))
        u = m.group(2)
        if u.startswith(("s", "S")) and "min" not in u and "month" not in u:
            return now + _dt.timedelta(seconds=n)
        if u in {"m", "min", "mins", "minute", "minutes"}:
            return now + _dt.timedelta(minutes=n)
        if u in {"h", "hr", "hrs", "hour", "hours"}:
            return now + _dt.timedelta(hours=n)
        if u in {"d", "day", "days"}:
            return now + _dt.timedelta(days=n)
        if u in {"w", "wk", "week", "weeks"}:
            return now + _dt.timedelta(weeks=n)

    # today/tomorrow/<weekday>  [time]
    parts = s.split(maxsplit=1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    base_date: Optional[_dt.date] = None
    if head == "today":
        base_date = now.date()
    elif head in {"tomorrow", "tmrw", "tom"}:
        base_date = now.date() + _dt.timedelta(days=1)
    elif head in {"yesterday"}:
        base_date = now.date() - _dt.timedelta(days=1)
    elif head in _WEEKDAYS:
        target = _WEEKDAYS[head]
        delta = (target - now.weekday()) % 7
        if delta == 0:
            delta = 7   # "fri" said on a Friday means *next* Friday
        base_date = now.date() + _dt.timedelta(days=delta)

    if base_date is not None:
        t = _parse_time(rest) if rest else _dt.time(9, 0)
        if t is None:
            return None
        return _dt.datetime.combine(base_date, t).astimezone()

    # YYYY-MM-DD [time]
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ tT](.+))?$", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            base_date = _dt.date(y, mo, d)
        except ValueError:
            return None
        rest = (m.group(4) or "").strip()
        t = _parse_time(rest) if rest else _dt.time(9, 0)
        if t is None:
            return None
        return _dt.datetime.combine(base_date, t).astimezone()

    # bare time → today (or tomorrow if already past)
    t = _parse_time(s)
    if t is not None:
        candidate = _dt.datetime.combine(now.date(), t).astimezone()
        if candidate <= now:
            candidate = candidate + _dt.timedelta(days=1)
        return candidate

    # last-resort delegation
    try:
        import dateparser  # type: ignore[import-not-found]
    except ImportError:
        return None
    parsed = dateparser.parse(text)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


_TIME_RE = re.compile(
    r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?$",
    re.IGNORECASE,
)


def _parse_time(s: str) -> Optional[_dt.time]:
    s = s.strip().lower()
    if not s:
        return None
    m = _TIME_RE.match(s)
    if not m:
        return None
    h = int(m.group(1))
    minute = int(m.group(2) or 0)
    suffix = (m.group(3) or "").replace(".", "")
    if suffix == "pm" and h < 12:
        h += 12
    elif suffix == "am" and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= minute <= 59):
        return None
    return _dt.time(h, minute)
