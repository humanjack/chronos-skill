"""Timezone and time helpers. Three modes: floating, zoned (IANA), UTC."""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

FLOATING = "floating"
UTC = "UTC"


def _system_tz_name() -> str | None:
    """Best-effort IANA name for the current system tz. Returns None if unknown."""
    # 1. Honor the TZ env var when it's a valid IANA key.
    env = os.environ.get("TZ")
    if env:
        try:
            ZoneInfo(env)
            return env
        except ZoneInfoNotFoundError:
            pass
    # 2. /etc/localtime is typically a symlink into /usr/share/zoneinfo/<IANA>.
    #    macOS resolves to /var/db/timezone/zoneinfo/<IANA>. Both contain "zoneinfo/".
    try:
        link = Path("/etc/localtime").resolve()
        s = str(link)
        idx = s.find("zoneinfo/")
        if idx >= 0:
            candidate = s[idx + len("zoneinfo/"):]
            try:
                ZoneInfo(candidate)
                return candidate
            except ZoneInfoNotFoundError:
                pass
    except OSError:
        pass
    return None


def system_tz() -> tzinfo:
    """User's current system timezone. Read live at every call.

    Prefers an IANA-keyed ZoneInfo so DST and minute offsets work correctly.
    Falls back to whatever fixed-offset tzinfo `astimezone` provides — never
    fabricates an Etc/GMT zone (which silently drops DST and minute offsets).
    """
    name = _system_tz_name()
    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            pass
    local = datetime.now().astimezone().tzinfo
    if isinstance(local, ZoneInfo):
        return local
    iana = getattr(local, "key", None)
    if iana:
        try:
            return ZoneInfo(iana)
        except ZoneInfoNotFoundError:
            pass
    return local or timezone.utc


def tz_name(tz: tzinfo) -> str:
    """Storage-safe string for a tzinfo: IANA key when available, else 'UTC'.

    Used at the boundary where we need to record a `tz` on a schedule_block.
    Bare-offset names like 'PDT' aren't IANA and would fail later validation,
    so we conservatively store 'UTC' rather than risk that.
    """
    if isinstance(tz, ZoneInfo):
        return tz.key
    if tz is timezone.utc:
        return "UTC"
    iana = getattr(tz, "key", None)
    if iana and iana in available_timezones():
        return iana
    return "UTC"


def resolve_tz(tz_str: str, now_tz: tzinfo | None = None) -> tzinfo:
    """Resolve a tz string to a tzinfo. `floating` uses `now_tz` or system tz."""
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
    now_tz: tzinfo | None = None,
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
    """Parse an ISO 8601 UTC timestamp. Input MUST end with 'Z'.

    Rejects naive timestamps explicitly: without 'Z', `astimezone()` would
    interpret the wall-clock time as the local zone, silently producing
    incorrect UTC values.
    """
    if not s.endswith("Z"):
        raise ValueError(f"timestamp must end with 'Z' (got {s!r})")
    return datetime.fromisoformat(s[:-1] + "+00:00").astimezone(ZoneInfo("UTC"))


def weekday_abbr(d: date, tz: tzinfo | None = None) -> str:
    """Lowercase 3-letter weekday abbrev in the given tz. `d` is treated as that tz's local date."""
    return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][d.weekday()]


def parse_time(t: str) -> time:
    h, m = (int(x) for x in t.split(":"))
    return time(h, m)


def minutes_between(a: datetime, b: datetime) -> int:
    """Whole minutes from a to b (b - a). Negative if a > b."""
    return int((b - a).total_seconds() // 60)
