"""Tests for calendar_sync."""

from __future__ import annotations

import os
import tempfile
import unittest

from scripts import calendar_sync, plan_store
from scripts._schema import empty_plan


class NormalizeTest(unittest.TestCase):
    def test_normalize_timed_zoned_event(self) -> None:
        raw = {
            "id": "g-evt-1",
            "summary": "Standup",
            "start": {"dateTime": "2026-04-28T09:00:00-07:00", "timeZone": "America/Los_Angeles"},
            "end": {"dateTime": "2026-04-28T09:30:00-07:00", "timeZone": "America/Los_Angeles"},
        }
        n = calendar_sync.normalize_event(raw)
        self.assertEqual(n["date"], "2026-04-28")
        self.assertEqual(n["start_time"], "09:00")
        self.assertEqual(n["end_time"], "09:30")
        self.assertEqual(n["tz"], "America/Los_Angeles")
        self.assertFalse(n["is_chronos"])

    def test_normalize_chronos_tagged(self) -> None:
        raw = {
            "id": "g-evt-2",
            "summary": "[chronos] Review PR",
            "description": "[chronos]\nmanaged",
            "start": {"dateTime": "2026-04-28T10:00:00Z", "timeZone": "UTC"},
            "end": {"dateTime": "2026-04-28T10:30:00Z", "timeZone": "UTC"},
            "extendedProperties": {"private": {"chronos_task_id": "task-abc123", "chronos_block_id": "block-def456"}},
        }
        n = calendar_sync.normalize_event(raw)
        self.assertTrue(n["is_chronos"])
        self.assertEqual(n["chronos_task_id"], "task-abc123")
        self.assertEqual(n["chronos_block_id"], "block-def456")

    def test_normalize_all_day(self) -> None:
        raw = {"id": "g-evt-3", "summary": "Holiday", "start": {"date": "2026-04-28"}, "end": {"date": "2026-04-29"}}
        n = calendar_sync.normalize_event(raw)
        self.assertTrue(n["is_all_day"])
        self.assertEqual(n["date"], "2026-04-28")


class ReconcileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        os.environ["CHRONOS_HOME"] = self.tmp
        self.plan = empty_plan()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("CHRONOS_HOME", None)

    def _block(self, **overrides) -> dict:
        b = {
            "id": "block-aaa11111",
            "date": "2026-04-28",
            "start_time": "09:00",
            "end_time": "10:00",
            "tz": "America/Los_Angeles",
            "item_type": "task",
            "item_id": "task-xyz",
            "google_event_id": None,
            "status": "accepted",
            "updated_at": "2026-04-28T00:00:00Z",
        }
        b.update(overrides)
        return b

    def test_create_for_accepted_unsynced(self) -> None:
        self.plan["schedule_blocks"].append(self._block())
        actions = calendar_sync.reconcile(self.plan, [])
        types = [a["type"] for a in actions]
        self.assertIn("create", types)

    def test_pull_time_change_when_remote_moved(self) -> None:
        self.plan["schedule_blocks"].append(self._block(google_event_id="g-1", status="synced"))
        pulled = [{
            "google_event_id": "g-1",
            "date": "2026-04-28",
            "start_time": "11:00",  # moved
            "end_time": "12:00",
            "tz": "America/Los_Angeles",
            "title": "[chronos] Task",
            "chronos_task_id": "task-xyz",
            "chronos_block_id": "block-aaa11111",
            "is_chronos": True,
            "is_all_day": False,
        }]
        actions = calendar_sync.reconcile(self.plan, pulled)
        ptc = [a for a in actions if a["type"] == "pull_time_change"]
        self.assertEqual(len(ptc), 1)
        self.assertEqual(ptc[0]["new"]["start_time"], "11:00")

    def test_orphan_when_tagged_without_local(self) -> None:
        pulled = [{
            "google_event_id": "g-lost",
            "date": "2026-04-28",
            "start_time": "09:00",
            "end_time": "10:00",
            "tz": "UTC",
            "title": "[chronos] Lost",
            "chronos_task_id": "task-nope",
            "chronos_block_id": "block-nope0000",
            "is_chronos": True,
            "is_all_day": False,
        }]
        actions = calendar_sync.reconcile(self.plan, pulled)
        types = [a["type"] for a in actions]
        self.assertIn("orphan", types)

    def test_mark_conflicted_on_external_overlap(self) -> None:
        self.plan["schedule_blocks"].append(self._block(status="synced", google_event_id="g-9"))
        pulled = [
            {"google_event_id": "g-9", "date": "2026-04-28", "start_time": "09:00", "end_time": "10:00",
             "tz": "America/Los_Angeles", "title": "[chronos] Task", "chronos_task_id": "task-xyz",
             "chronos_block_id": "block-aaa11111", "is_chronos": True, "is_all_day": False},
            {"google_event_id": "g-ext", "date": "2026-04-28", "start_time": "09:30", "end_time": "10:30",
             "tz": "America/Los_Angeles", "title": "External mtg", "chronos_task_id": None,
             "chronos_block_id": None, "is_chronos": False, "is_all_day": False},
        ]
        actions = calendar_sync.reconcile(self.plan, pulled)
        self.assertTrue(any(a["type"] == "mark_conflicted" for a in actions))

    def test_missing_remote_when_gid_gone(self) -> None:
        self.plan["schedule_blocks"].append(self._block(google_event_id="g-gone", status="synced"))
        actions = calendar_sync.reconcile(self.plan, [])
        self.assertTrue(any(a["type"] == "missing_remote" for a in actions))


class ApplyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        os.environ["CHRONOS_HOME"] = self.tmp
        self.plan = empty_plan()
        self.plan["schedule_blocks"].append({
            "id": "block-apply001",
            "date": "2026-04-28",
            "start_time": "09:00",
            "end_time": "10:00",
            "tz": "UTC",
            "item_type": "task",
            "item_id": "task-x",
            "google_event_id": None,
            "status": "accepted",
            "updated_at": "2026-04-28T00:00:00Z",
        })

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("CHRONOS_HOME", None)

    def test_apply_create_sets_gid_and_status(self) -> None:
        results = [{"action_id": "a1", "type": "create", "block_id": "block-apply001", "google_event_id": "g-new"}]
        summary = calendar_sync.apply_results(self.plan, results)
        self.assertEqual(summary["created"], 1)
        blk = self.plan["schedule_blocks"][0]
        self.assertEqual(blk["google_event_id"], "g-new")
        self.assertEqual(blk["status"], "synced")

    def test_apply_pull_time_change(self) -> None:
        results = [{"action_id": "a1", "type": "pull_time_change", "block_id": "block-apply001",
                    "new": {"date": "2026-04-28", "start_time": "11:00", "end_time": "12:00", "tz": "UTC"}}]
        summary = calendar_sync.apply_results(self.plan, results)
        self.assertEqual(summary["time_pulled"], 1)
        blk = self.plan["schedule_blocks"][0]
        self.assertEqual(blk["start_time"], "11:00")

    def test_apply_is_idempotent(self) -> None:
        results = [{"action_id": "a1", "type": "create", "block_id": "block-apply001", "google_event_id": "g-new"}]
        calendar_sync.apply_results(self.plan, results)
        calendar_sync.apply_results(self.plan, results)
        # Second apply shouldn't change the block further (same gid).
        self.assertEqual(self.plan["schedule_blocks"][0]["google_event_id"], "g-new")


if __name__ == "__main__":
    unittest.main()
