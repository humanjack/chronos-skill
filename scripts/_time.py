"""Timezone and time helpers. Three modes: floating, zoned (IANA), UTC."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, available_timezones

FLOATING = "floating"
UTC = "UTC"


def system_tz() -> ZoneInfo:
    """User's current system timezone. Read live at every call."""
    name = datetime.now().astimezone().tzname() or "UTC"
    # tzname() can return abbreviations like 'PDT' which aren't IANA; fall back via offset.
    try:
        return ZoneInfo(name)
    except Exception:
        offset = datetime.now().astimezone().utcoffset() or timedelta(0)
        # Best effort — Etc/GMT offsets have inverted signs.
        hours = int(offset.total_seconds() // 3600)
        return ZoneInfo(f"Etc/GMT{-hours:+d}") if hours else ZoneInfo("UTC")


def resolve_tz(tz_str: str, now_tz: ZoneInfo | None = None) -> ZoneInfo:
    """Resolve a tz string to a ZoneInfo. `floating` uses `now_tz` or system tz."""
    if tz_str == FLOATING:
        return now_tz or system_tz()
    if tz_str == UTC:
        return ZoneInfo("UTC")
    return ZoneInfo(tz_str)


def is_valid_tz(tz_str: str) -> bool:
    if tz_str in (FLOATING, UTC):
        return True
    return tz_str in available_timezones()


def resolve_utc(
    date_str: str,
    time_str: str,
    tz_str: str,
    now_tz: ZoneInfo | None = None,
) -> datetime:
    """Convert a (date, wall-clock time, tz) triple to a UTC-aware datetime.

    For `floating` tz, uses `now_tz` (or system tz) for resolution.
    """
    tz = resolve_tz(tz_str, now_tz=now_tz)
    y, m, d = (int(x) for x in date_str.split("-"))
    hh, mm = (int(x) for x in time_str.split(":"))
    local = datetime(y, m, d, hh, mm, tzinfo=tz)
    return local.astimezone(ZoneInfo("UTC"))


def now_utc() -> datetime:
    return datetime.now(tz=ZoneInfo("UTC"))


def now_utc_iso() -> str:
    return now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(s: str) -> datetime:
    """Parse an ISO 8601 UTC timestamp ending in Z."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(ZoneInfo("UTC"))


def weekday_abbr(d: date, tz: ZoneInfo | None = None) -> str:
    """Lowercase 3-letter weekday abbrev in the given tz. `d` is treated as that tz's local date."""
    return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][d.weekday()]


def parse_time(t: str) -> time:
    h, m = (int(x) for x in t.split(":"))
    return time(h, m)


def minutes_between(a: datetime, b: datetime) -> int:
    """Whole minutes from a to b (b - a). Negative if a > b."""
    return int((b - a).total_seconds() // 60)
