"""Tests for _schema validation. Focuses on date/time strictness."""

from __future__ import annotations

import unittest

from scripts import _schema
from scripts._schema import SchemaError, empty_plan, validate


class DateTimeStrictnessTest(unittest.TestCase):
    def _plan_with_block(self, **block_overrides) -> dict:
        plan = empty_plan()
        block = {
            "id": "block-strict01",
            "date": "2026-04-28",
            "start_time": "09:00",
            "end_time": "10:00",
            "tz": "UTC",
            "item_type": "task",
            "item_id": "task-x",
            "google_event_id": None,
            "status": "proposed",
            "updated_at": "2026-04-28T00:00:00Z",
        }
        block.update(block_overrides)
        plan["schedule_blocks"].append(block)
        return plan

    def test_invalid_month_rejected(self) -> None:
        plan = self._plan_with_block(date="2026-13-01")
        with self.assertRaises(SchemaError):
            validate(plan)

    def test_invalid_day_rejected(self) -> None:
        plan = self._plan_with_block(date="2026-02-30")  # Feb 30 isn't real
        with self.assertRaises(SchemaError):
            validate(plan)

    def test_non_numeric_date_rejected(self) -> None:
        plan = self._plan_with_block(date="abcd-ef-gh")
        with self.assertRaises(SchemaError):
            validate(plan)

    def test_invalid_hour_rejected(self) -> None:
        plan = self._plan_with_block(start_time="25:00")
        with self.assertRaises(SchemaError):
            validate(plan)

    def test_invalid_minute_rejected(self) -> None:
        plan = self._plan_with_block(end_time="10:99")
        with self.assertRaises(SchemaError):
            validate(plan)

    def test_well_formed_passes(self) -> None:
        plan = self._plan_with_block(date="2026-02-28", start_time="23:59", end_time="00:00")
        # Should not raise.
        validate(plan)


if __name__ == "__main__":
    unittest.main()
