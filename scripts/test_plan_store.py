"""Tests for plan_store. Run: python3 -m unittest scripts.test_plan_store"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from scripts import plan_store
from scripts._schema import SCHEMA_VERSION, SchemaError, empty_plan, validate


class PlanStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        os.environ["CHRONOS_HOME"] = self.tmp

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("CHRONOS_HOME", None)

    def test_load_creates_empty_plan(self) -> None:
        plan = plan_store.load()
        self.assertEqual(plan["schema_version"], SCHEMA_VERSION)
        self.assertEqual(plan["goals"], [])
        self.assertTrue(plan_store.plan_path().exists())

    def test_save_round_trip(self) -> None:
        plan = plan_store.load()
        plan_store.add_goal(plan, title="Ship chronos", target_date="2026-07-01", success_criteria="2 weeks daily use")
        plan_store.save(plan)
        reloaded = plan_store.load()
        self.assertEqual(len(reloaded["goals"]), 1)
        self.assertEqual(reloaded["goals"][0]["title"], "Ship chronos")

    def test_save_rejects_invalid(self) -> None:
        plan = empty_plan()
        plan["goals"].append({"id": "bad-id", "title": "x", "status": "active"})
        with self.assertRaises(SchemaError):
            plan_store.save(plan)

    def test_atomic_write_file_mode(self) -> None:
        plan = plan_store.load()
        plan_store.save(plan)
        mode = plan_store.plan_path().stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_add_routine(self) -> None:
        plan = plan_store.load()
        r = plan_store.add_routine(
            plan,
            title="Gym",
            days=["mon", "wed", "fri"],
            window_start="07:00",
            window_end="08:00",
            duration_minutes=60,
        )
        self.assertTrue(r["id"].startswith("routine-"))
        plan_store.save(plan)

    def test_add_task_with_deadline(self) -> None:
        plan = plan_store.load()
        t = plan_store.add_task(plan, title="Draft doc", estimate_minutes=90, priority="high", deadline="2026-04-30")
        self.assertEqual(t["priority"], "high")
        self.assertEqual(t["deadline"], "2026-04-30")
        plan_store.save(plan)

    def test_summary_counts(self) -> None:
        plan = plan_store.load()
        plan_store.add_goal(plan, title="G1", target_date=None, success_criteria="")
        plan_store.add_task(plan, title="T1", estimate_minutes=30)
        s = plan_store.summary(plan)
        self.assertEqual(s["counts"]["goals_active"], 1)
        self.assertEqual(s["counts"]["tasks_open"], 1)

    def test_gaps_surfaces_overdue(self) -> None:
        plan = plan_store.load()
        yesterday = (date(2026, 4, 24) - timedelta(days=1)).isoformat()
        plan_store.add_task(plan, title="Overdue", estimate_minutes=30, deadline=yesterday)
        g = plan_store.gaps(plan, today=date(2026, 4, 24))
        kinds = [item["kind"] for item in g]
        self.assertIn("overdue_task", kinds)

    def test_gaps_surfaces_stale_goal(self) -> None:
        plan = plan_store.load()
        plan_store.add_goal(plan, title="Ship", target_date=None, success_criteria="")
        g = plan_store.gaps(plan, today=date(2026, 4, 24))
        self.assertTrue(any(item["kind"] == "stale_goal" for item in g))

    def test_set_block_status(self) -> None:
        plan = plan_store.load()
        blk = {
            "id": "block-abc12345",
            "date": "2026-04-24",
            "start_time": "09:00",
            "end_time": "10:00",
            "tz": "UTC",
            "item_type": "task",
            "item_id": "task-x",
            "google_event_id": None,
        }
        plan_store.upsert_block(plan, blk)
        plan_store.set_block_status(plan, "block-abc12345", "accepted")
        self.assertEqual(plan["schedule_blocks"][0]["status"], "accepted")
        plan_store.save(plan)

    def test_archive_moves_old_blocks(self) -> None:
        plan = plan_store.load()
        old_date = (date(2026, 4, 24) - timedelta(days=45)).isoformat()
        plan_store.upsert_block(plan, {
            "id": "block-old11111",
            "date": old_date,
            "start_time": "09:00",
            "end_time": "10:00",
            "tz": "UTC",
            "item_type": "task",
            "item_id": "task-x",
            "google_event_id": None,
            "status": "done",
        })
        plan_store.upsert_block(plan, {
            "id": "block-new22222",
            "date": "2026-04-24",
            "start_time": "09:00",
            "end_time": "10:00",
            "tz": "UTC",
            "item_type": "task",
            "item_id": "task-y",
            "google_event_id": None,
            "status": "accepted",
        })
        plan_store.save(plan)
        result = plan_store.archive(plan, today=date(2026, 4, 24))
        self.assertEqual(result["archived_blocks"], 1)
        self.assertEqual(len(plan["schedule_blocks"]), 1)
        plan_store.save(plan)

    def test_cli_add_goal(self) -> None:
        rc = plan_store.main(["add-goal", "--title", "CLI Goal", "--target-date", "2026-07-01"])
        self.assertEqual(rc, 0)
        plan = plan_store.load()
        self.assertEqual(plan["goals"][0]["title"], "CLI Goal")

    def test_cli_handles_corrupt_plan_json(self) -> None:
        # Corrupt the plan file directly.
        plan_store.load()  # initialize
        plan_store.plan_path().write_text("{not valid json")
        rc = plan_store.main(["summary"])
        # Should exit 2, not throw.
        self.assertEqual(rc, 2)

    def test_archive_file_is_mode_600(self) -> None:
        plan = plan_store.load()
        old_date = (date(2026, 4, 24) - timedelta(days=45)).isoformat()
        plan_store.upsert_block(plan, {
            "id": "block-archmode",
            "date": old_date,
            "start_time": "09:00",
            "end_time": "10:00",
            "tz": "UTC",
            "item_type": "task",
            "item_id": "task-x",
            "google_event_id": None,
            "status": "done",
        })
        plan_store.save(plan)
        plan_store.archive(plan, today=date(2026, 4, 24))
        # Archive file should exist for the cutoff month and be mode 0600.
        cutoff_month = (date(2026, 4, 24) - timedelta(days=30)).strftime("%Y-%m")
        arch_file = plan_store.archive_dir() / f"{cutoff_month}.json"
        self.assertTrue(arch_file.exists(), f"archive file missing: {arch_file}")
        mode = arch_file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
