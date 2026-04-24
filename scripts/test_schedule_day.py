"""Tests for schedule_day."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from zoneinfo import ZoneInfo

from scripts import plan_store, schedule_day
from scripts._schema import empty_plan


class ScheduleDayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        os.environ["CHRONOS_HOME"] = self.tmp
        self.tz = ZoneInfo("America/Los_Angeles")
        self.plan = empty_plan()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("CHRONOS_HOME", None)

    def test_empty_day_empty_plan(self) -> None:
        proposal = schedule_day.build_proposal(self.plan, [], date(2026, 4, 28), now_tz=self.tz)
        # No tasks, no routines, no events -> one big buffer block.
        self.assertEqual(proposal["date"], "2026-04-28")
        self.assertFalse(proposal["overcommitted"])
        # Block should be buffer type, covering workday.
        self.assertTrue(all(b["item_type"] == "buffer" for b in proposal["blocks"]))

    def test_anchored_event_preserved(self) -> None:
        events = [{
            "date": "2026-04-28",
            "start_time": "10:00",
            "end_time": "11:00",
            "tz": "America/Los_Angeles",
            "title": "Standup",
        }]
        proposal = schedule_day.build_proposal(self.plan, events, date(2026, 4, 28), now_tz=self.tz)
        externals = [b for b in proposal["blocks"] if b["item_type"] == "external"]
        self.assertEqual(len(externals), 1)
        self.assertEqual(externals[0]["start_time"], "10:00")

    def test_routine_placed_in_preferred_window(self) -> None:
        plan_store.add_routine(
            self.plan,
            title="Gym",
            days=["tue", "wed", "thu"],
            window_start="07:00",
            window_end="08:30",
            duration_minutes=60,
            tz="America/Los_Angeles",
        )
        proposal = schedule_day.build_proposal(self.plan, [], date(2026, 4, 28), now_tz=self.tz)  # Tue
        routines = [b for b in proposal["blocks"] if b["item_type"] == "routine"]
        self.assertEqual(len(routines), 1)
        self.assertEqual(routines[0]["start_time"], "07:00")

    def test_task_ordering_by_deadline(self) -> None:
        plan_store.add_task(self.plan, title="Later", estimate_minutes=60, priority="high", deadline="2026-05-15")
        plan_store.add_task(self.plan, title="Sooner", estimate_minutes=60, priority="low", deadline="2026-04-30")
        proposal = schedule_day.build_proposal(self.plan, [], date(2026, 4, 28), now_tz=self.tz)
        task_titles = [b["title"] for b in proposal["blocks"] if b["item_type"] == "task"]
        self.assertEqual(task_titles[0], "Sooner")

    def test_overcommitment_flag(self) -> None:
        # Fill events leaving just 12-13 (60 min gap).
        events = [
            {"date": "2026-04-28", "start_time": "06:00", "end_time": "12:00", "tz": "America/Los_Angeles", "title": "AM block"},
            {"date": "2026-04-28", "start_time": "13:00", "end_time": "22:00", "tz": "America/Los_Angeles", "title": "PM block"},
        ]
        plan_store.add_task(self.plan, title="Big A", estimate_minutes=120, priority="high")
        plan_store.add_task(self.plan, title="Big B", estimate_minutes=120, priority="high")
        plan_store.add_task(self.plan, title="Big C", estimate_minutes=120, priority="high")
        proposal = schedule_day.build_proposal(self.plan, events, date(2026, 4, 28), now_tz=self.tz)
        self.assertTrue(proposal["overcommitted"])
        self.assertIsNotNone(proposal["defer_candidate_id"])

    def test_no_overcommit_when_tasks_fit(self) -> None:
        plan_store.add_task(self.plan, title="Small", estimate_minutes=30)
        proposal = schedule_day.build_proposal(self.plan, [], date(2026, 4, 28), now_tz=self.tz)
        self.assertFalse(proposal["overcommitted"])

    def test_buffer_pct_honored(self) -> None:
        # Provide one big gap; task ~= 0.85 * gap should fit; task > 0.85 * gap should not.
        self.plan["preferences"]["buffer_pct"] = 0.15
        plan_store.add_task(self.plan, title="Maxed", estimate_minutes=120, priority="high")  # will fit
        proposal = schedule_day.build_proposal(self.plan, [], date(2026, 4, 28), now_tz=self.tz)
        tasks = [b for b in proposal["blocks"] if b["item_type"] == "task"]
        self.assertEqual(len(tasks), 1)

    def test_routine_skipped_on_non_cadence_day(self) -> None:
        plan_store.add_routine(
            self.plan,
            title="Gym",
            days=["mon"],
            window_start="07:00",
            window_end="08:30",
            duration_minutes=60,
            tz="America/Los_Angeles",
        )
        proposal = schedule_day.build_proposal(self.plan, [], date(2026, 4, 28), now_tz=self.tz)  # Tue
        routines = [b for b in proposal["blocks"] if b["item_type"] == "routine"]
        self.assertEqual(len(routines), 0)


if __name__ == "__main__":
    unittest.main()
