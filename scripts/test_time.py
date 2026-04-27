"""Tests for _time helpers."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from scripts import _time


class ParseIsoUtcTest(unittest.TestCase):
    def test_z_terminated_parses(self) -> None:
        dt = _time.parse_iso_utc("2026-04-28T17:30:00Z")
        self.assertEqual(dt.tzinfo, ZoneInfo("UTC"))
        self.assertEqual(dt.hour, 17)

    def test_naive_input_rejected(self) -> None:
        # Without 'Z', `datetime.fromisoformat` returns a naive datetime, and a
        # naive .astimezone() would silently treat it as local time. Reject loudly.
        with self.assertRaises(ValueError):
            _time.parse_iso_utc("2026-04-28T17:30:00")

    def test_offset_input_rejected(self) -> None:
        # Even a valid offset isn't accepted — the function's contract is "UTC, with Z".
        with self.assertRaises(ValueError):
            _time.parse_iso_utc("2026-04-28T17:30:00+00:00")


class SystemTzTest(unittest.TestCase):
    def test_returns_a_tzinfo(self) -> None:
        tz = _time.system_tz()
        # Whatever it returns must be a tzinfo. Accept ZoneInfo or fixed-offset timezone.
        self.assertIsNotNone(tz.utcoffset(datetime.now()))

    def test_no_etc_gmt_construction(self) -> None:
        # Regression: previously the fallback produced Etc/GMT zones, which lose DST
        # and minute offsets. Make sure we no longer return that.
        tz = _time.system_tz()
        if isinstance(tz, ZoneInfo):
            self.assertFalse(tz.key.startswith("Etc/GMT"))


class ResolveUtcTest(unittest.TestCase):
    def test_zoned(self) -> None:
        dt = _time.resolve_utc("2026-04-28", "09:00", "America/Los_Angeles")
        # PDT in late April → UTC-7 → 09:00 PT == 16:00 UTC
        self.assertEqual(dt.hour, 16)

    def test_utc_explicit(self) -> None:
        dt = _time.resolve_utc("2026-04-28", "09:00", "UTC")
        self.assertEqual(dt.hour, 9)

    def test_floating_with_explicit_now_tz(self) -> None:
        dt = _time.resolve_utc("2026-04-28", "09:00", "floating", now_tz=ZoneInfo("Europe/London"))
        # London BST in late April = UTC+1 → 09:00 local == 08:00 UTC
        self.assertEqual(dt.hour, 8)


if __name__ == "__main__":
    unittest.main()
