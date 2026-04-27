"""Schema validation + forward-only migrations for ~/.chronos/plan.json."""

from __future__ import annotations

from typing import Any, Callable

from ._time import FLOATING, UTC, is_valid_tz

SCHEMA_VERSION = 1


class SchemaError(ValueError):
    pass


def empty_plan() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "timezone": "UTC",
        "preferences": {
            "buffer_pct": 0.15,
            "energy_windows": {},
        },
        "goals": [],
        "routines": [],
        "tasks": [],
        "schedule_blocks": [],
        "calendar_sync": {
            "last_pull_at": None,
            "last_push_at": None,
            "primary_calendar_id": None,
        },
    }


# --- migrations: ordered from_version -> fn(plan) -> plan ---
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def migrate(plan: dict[str, Any]) -> dict[str, Any]:
    v = plan.get("schema_version", 0)
    while v in MIGRATIONS:
        plan = MIGRATIONS[v](plan)
        v = plan["schema_version"]
    return plan


# --- validation ---
_VALID_STATUSES = {
    "goal": {"active", "done", "dropped"},
    "routine": {"active", "paused", "done"},
    "task": {"open", "in_progress", "done", "deferred"},
    "block": {"proposed", "accepted", "synced", "done", "conflicted"},
}
_VALID_PRIORITIES = {"low", "medium", "high"}
_VALID_ITEM_TYPES = {"task", "routine", "buffer", "external"}
_VALID_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise SchemaError(msg)


def _check_date(s: Any, field: str) -> None:
    _require(
        isinstance(s, str) and len(s) == 10 and s[4] == "-" and s[7] == "-",
        f"{field} must be YYYY-MM-DD, got {s!r}",
    )
    try:
        y, m, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
    except ValueError:
        raise SchemaError(f"{field} must be YYYY-MM-DD with numeric parts, got {s!r}")
    _require(1 <= m <= 12, f"{field} month must be 01-12, got {s!r}")
    _require(1 <= d <= 31, f"{field} day must be 01-31, got {s!r}")
    # Validate full calendar correctness (e.g. Feb 30, Apr 31).
    from datetime import date as _date
    try:
        _date(y, m, d)
    except ValueError as e:
        raise SchemaError(f"{field} is not a real calendar date ({s!r}): {e}")


def _check_time(s: Any, field: str) -> None:
    _require(
        isinstance(s, str) and len(s) == 5 and s[2] == ":",
        f"{field} must be HH:MM, got {s!r}",
    )
    try:
        h, m = int(s[0:2]), int(s[3:5])
    except ValueError:
        raise SchemaError(f"{field} must be HH:MM with numeric parts, got {s!r}")
    _require(0 <= h <= 23, f"{field} hour must be 00-23, got {s!r}")
    _require(0 <= m <= 59, f"{field} minute must be 00-59, got {s!r}")


def _check_tz(s: Any, field: str) -> None:
    _require(isinstance(s, str) and is_valid_tz(s), f"{field} must be a valid IANA timezone, 'floating', or 'UTC'; got {s!r}")


def _check_iso_utc_or_null(s: Any, field: str) -> None:
    if s is None:
        return
    _require(isinstance(s, str) and s.endswith("Z"), f"{field} must be ISO 8601 UTC with Z suffix or null, got {s!r}")


def _check_window(w: Any, field: str) -> None:
    _require(isinstance(w, dict), f"{field} must be an object")
    _check_time(w.get("start"), f"{field}.start")
    _check_time(w.get("end"), f"{field}.end")
    _check_tz(w.get("tz"), f"{field}.tz")


def validate(plan: dict[str, Any]) -> None:
    """Raise SchemaError if plan is malformed. Silent on success."""
    _require(isinstance(plan, dict), "plan must be an object")
    _require(plan.get("schema_version") == SCHEMA_VERSION, f"schema_version must be {SCHEMA_VERSION}")
    _check_tz(plan.get("timezone"), "timezone")

    prefs = plan.get("preferences")
    _require(isinstance(prefs, dict), "preferences must be an object")
    _require(isinstance(prefs.get("buffer_pct"), (int, float)) and 0.0 <= prefs["buffer_pct"] <= 0.5, "preferences.buffer_pct must be in [0.0, 0.5]")
    ew = prefs.get("energy_windows", {})
    _require(isinstance(ew, dict), "preferences.energy_windows must be an object")
    for name, w in ew.items():
        _check_window(w, f"preferences.energy_windows.{name}")

    for g in plan.get("goals", []):
        _require(isinstance(g.get("id"), str) and g["id"].startswith("goal-"), f"goal.id malformed: {g.get('id')!r}")
        _require(isinstance(g.get("title"), str) and g["title"], f"goal.title required: {g.get('id')}")
        if g.get("target_date") is not None:
            _check_date(g["target_date"], f"goal.{g['id']}.target_date")
        _require(g.get("status") in _VALID_STATUSES["goal"], f"goal.status invalid: {g.get('status')}")
        _check_iso_utc_or_null(g.get("updated_at"), f"goal.{g['id']}.updated_at")

    for r in plan.get("routines", []):
        _require(isinstance(r.get("id"), str) and r["id"].startswith("routine-"), f"routine.id malformed: {r.get('id')!r}")
        _require(isinstance(r.get("title"), str) and r["title"], f"routine.title required: {r.get('id')}")
        cad = r.get("cadence") or {}
        days = cad.get("days") or []
        _require(isinstance(days, list) and days and all(d in _VALID_DAYS for d in days), f"routine.cadence.days invalid: {days}")
        _check_tz(cad.get("tz"), f"routine.{r['id']}.cadence.tz")
        _check_window(r.get("preferred_window"), f"routine.{r['id']}.preferred_window")
        _require(isinstance(r.get("duration_minutes"), int) and r["duration_minutes"] > 0, f"routine.duration_minutes invalid: {r.get('duration_minutes')}")
        _require(r.get("status") in _VALID_STATUSES["routine"], f"routine.status invalid: {r.get('status')}")

    for t in plan.get("tasks", []):
        _require(isinstance(t.get("id"), str) and t["id"].startswith("task-"), f"task.id malformed: {t.get('id')!r}")
        _require(isinstance(t.get("title"), str) and t["title"], f"task.title required: {t.get('id')}")
        _require(isinstance(t.get("estimate_minutes"), int) and t["estimate_minutes"] > 0, f"task.estimate_minutes invalid: {t.get('estimate_minutes')}")
        _require(t.get("priority") in _VALID_PRIORITIES, f"task.priority invalid: {t.get('priority')}")
        if t.get("deadline") is not None:
            _check_date(t["deadline"], f"task.{t['id']}.deadline")
        _require(t.get("status") in _VALID_STATUSES["task"], f"task.status invalid: {t.get('status')}")

    for b in plan.get("schedule_blocks", []):
        _require(isinstance(b.get("id"), str) and b["id"].startswith("block-"), f"block.id malformed: {b.get('id')!r}")
        _check_date(b.get("date"), f"block.{b['id']}.date")
        _check_time(b.get("start_time"), f"block.{b['id']}.start_time")
        _check_time(b.get("end_time"), f"block.{b['id']}.end_time")
        _check_tz(b.get("tz"), f"block.{b['id']}.tz")
        _require(b.get("item_type") in _VALID_ITEM_TYPES, f"block.item_type invalid: {b.get('item_type')}")
        _require(b.get("status") in _VALID_STATUSES["block"], f"block.status invalid: {b.get('status')}")

    cs = plan.get("calendar_sync") or {}
    _check_iso_utc_or_null(cs.get("last_pull_at"), "calendar_sync.last_pull_at")
    _check_iso_utc_or_null(cs.get("last_push_at"), "calendar_sync.last_push_at")
