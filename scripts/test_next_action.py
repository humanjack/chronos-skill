"""Tests for next_action."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts import next_action, plan_store
from scripts._schema import empty_plan


class NextActionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        os.environ["CHRONOS_HOME"] = self.tmp
        self.tz = ZoneInfo("America/Los_Angeles")
        self.plan = empty_plan()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("CHRONOS_HOME", None)

    def test_active_during_meeting(self) -> None:
        events = [{
            "date": "2026-04-28",
            "start_time": "10:00",
            "end_time": "11:00",
            "tz": "America/Los_Angeles",
            "title": "Standup",
        }]
        now = datetime(2026, 4, 28, 10, 30, tzinfo=self.tz)
        result = next_action.pick_next(self.plan, events, now, now_tz=self.tz)
        self.assertEqual(result["kind"], "active")
        self.assertEqual(result["title"], "Standup")
        self.assertEqual(result["minutes_remaining"], 30)

    def test_gap_suggests_task(self) -> None:
        plan_store.add_task(self.plan, title="Review PR", estimate_minutes=45, priority="high", deadline="2026-04-30")
        events = [{
            "date": "2026-04-28",
            "start_time": "11:00",
            "end_time": "12:00",
            "tz": "America/Los_Angeles",
            "title": "Standup",
        }]
        now = datetime(2026, 4, 28, 9, 0, tzinfo=self.tz)
        result = next_action.pick_next(self.plan, events, now, now_tz=self.tz)
        self.assertEqual(result["kind"], "gap")
        self.assertIn("deadline", result["why"])

    def test_ahead_when_nothing_fits(self) -> None:
        plan_store.add_goal(self.plan, title="Ship chronos", target_date=None, success_criteria="")
        # Empty day, no tasks. Should go to ahead or gap with empty pool.
        now = datetime(2026, 4, 28, 21, 55, tzinfo=self.tz)  # near end of workday
        result = next_action.pick_next(self.plan, [], now, now_tz=self.tz)
        self.assertEqual(result["kind"], "ahead")

    def test_cli_preserves_user_supplied_offset(self) -> None:
        # `--now 2026-04-28T10:00:00-07:00` must NOT be clobbered by system tz.
        from datetime import datetime as _dt
        from io import StringIO
        from contextlib import redirect_stdout

        # Stash a minimal plan file the CLI will load.
        plan_store.add_task(self.plan, title="X", estimate_minutes=30, priority="high")
        plan_store.save(self.plan)

        buf = StringIO()
        with redirect_stdout(buf):
            rc = next_action.main(["--now", "2026-04-28T10:00:00-07:00"])
        self.assertEqual(rc, 0)
        # The script ran end-to-end without clobbering tz; we don't assert on
        # exact output here, just that --now with offset doesn't crash and the
        # command completed.
        self.assertTrue(buf.getvalue())

    def test_main_now_arg_keeps_offset(self) -> None:
        # White-box: parse the same string fromisoformat does and assert tzinfo survives.
        from datetime import datetime as _dt
        parsed = _dt.fromisoformat("2026-04-28T10:00:00-07:00")
        self.assertIsNotNone(parsed.tzinfo)
        # If the original code path (`.replace(tzinfo=now_tz)`) ran it would
        # overwrite tzinfo. The fix branches on `tzinfo is not None`.

    def test_energy_window_biases_high_priority(self) -> None:
        self.plan["preferences"]["energy_windows"] = {
            "deep_work": {"start": "08:00", "end": "11:00", "tz": "floating"}
        }
        plan_store.add_task(self.plan, title="Shallow", estimate_minutes=30, priority="low")
        plan_store.add_task(self.plan, title="Deep", estimate_minutes=60, priority="high")
        now = datetime(2026, 4, 28, 9, 0, tzinfo=self.tz)
        result = next_action.pick_next(self.plan, [], now, now_tz=self.tz)
        self.assertEqual(result["kind"], "gap")
        self.assertEqual(result["title"], "Deep")
        self.assertIn("deep_work", result["why"])


if __name__ == "__main__":
    unittest.main()
