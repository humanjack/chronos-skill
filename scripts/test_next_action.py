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
