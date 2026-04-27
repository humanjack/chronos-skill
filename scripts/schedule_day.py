"""Build a time-block proposal for a target date.

Inputs (all optional):
  --plan PATH       plan.json (default: load from CHRONOS_HOME or ~/.chronos/)
  --events PATH     normalized events JSON (list of {date,start_time,end_time,tz,title,chronos_task_id?}) or "-" for stdin
  --date YYYY-MM-DD target date (default: today in system tz)

Output on stdout: JSON { "date": ..., "blocks": [...], "overcommitted": bool, "defer_candidate_id": str|null, "notes": [...] }
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import plan_store
from ._time import (
    FLOATING,
    minutes_between,
    now_utc_iso,
    resolve_utc,
    system_tz,
    tz_name,
    weekday_abbr,
)

WORKDAY_START = time(6, 0)
WORKDAY_END = time(22, 0)
MIN_BLOCK_MINUTES = 15
MAX_BLOCK_MINUTES = 120


@dataclass
class Interval:
    start_utc: datetime
    end_utc: datetime

    def minutes(self) -> int:
        return minutes_between(self.start_utc, self.end_utc)

    def overlaps(self, other: "Interval") -> bool:
        return self.start_utc < other.end_utc and other.start_utc < self.end_utc


def _event_to_interval(ev: dict[str, Any], now_tz: ZoneInfo) -> Interval:
    return Interval(
        resolve_utc(ev["date"], ev["start_time"], ev["tz"], now_tz=now_tz),
        resolve_utc(ev["date"], ev["end_time"], ev["tz"], now_tz=now_tz),
    )


def _routine_occurs_on(routine: dict[str, Any], target: date, now_tz: ZoneInfo) -> bool:
    cadence = routine.get("cadence") or {}
    cad_tz = cadence.get("tz", FLOATING)
    tz = now_tz if cad_tz == FLOATING else ZoneInfo(cad_tz) if cad_tz != "UTC" else ZoneInfo("UTC")
    # For cadence purposes, target is a local date concept. Use weekday of target in resolved tz.
    local_date = target  # target is already a date; no shift needed for floating
    return weekday_abbr(local_date, tz) in cadence.get("days", [])


def rank_tasks(tasks: list[dict[str, Any]], goals_by_id: dict[str, dict[str, Any]], target: date) -> list[dict[str, Any]]:
    def deadline_days(t: dict[str, Any]) -> int:
        d = t.get("deadline")
        if not d:
            return 9999
        y, m, dd = (int(x) for x in d.split("-"))
        return (date(y, m, dd) - target).days

    def goal_alignment(t: dict[str, Any]) -> int:
        gid = t.get("linked_goal_id")
        if gid and goals_by_id.get(gid, {}).get("status") == "active":
            return 0
        return 1

    def priority_rank(t: dict[str, Any]) -> int:
        return {"high": 0, "medium": 1, "low": 2}.get(t.get("priority", "medium"), 1)

    # Stable sort by (deadline proximity, goal alignment, priority, id for determinism).
    return sorted(
        [t for t in tasks if t["status"] == "open"],
        key=lambda t: (deadline_days(t), goal_alignment(t), priority_rank(t), t["id"]),
    )


def _local_time_str(dt_utc: datetime, tz: ZoneInfo) -> tuple[str, str]:
    """Return (date_str, time_str) of dt_utc rendered in tz."""
    local = dt_utc.astimezone(tz)
    return local.strftime("%Y-%m-%d"), local.strftime("%H:%M")


def build_proposal(
    plan: dict[str, Any],
    events: list[dict[str, Any]],
    target: date,
    now_tz: ZoneInfo | None = None,
) -> dict[str, Any]:
    now_tz = now_tz or system_tz()
    target_str = target.isoformat()
    buffer_pct = float(plan.get("preferences", {}).get("buffer_pct", 0.15))

    # Day boundaries as UTC intervals.
    day_start = datetime.combine(target, WORKDAY_START, tzinfo=now_tz).astimezone(ZoneInfo("UTC"))
    day_end = datetime.combine(target, WORKDAY_END, tzinfo=now_tz).astimezone(ZoneInfo("UTC"))

    # --- 1. Anchor fixed events. ---
    anchored: list[tuple[Interval, dict[str, Any]]] = []
    notes: list[str] = []
    seen_ids: set[str] = set()
    for ev in events:
        if ev.get("date") != target_str:
            continue
        iv = _event_to_interval(ev, now_tz)
        block_id = f"block-ev{len(anchored):06x}"
        anchored.append((iv, {
            "id": block_id,
            "date": ev["date"],
            "start_time": ev["start_time"],
            "end_time": ev["end_time"],
            "tz": ev["tz"],
            "item_type": "task" if ev.get("chronos_task_id") else "external",
            "item_id": ev.get("chronos_task_id"),
            "google_event_id": ev.get("google_event_id"),
            "status": "proposed",
            "title": ev.get("title", ""),
            "source": "calendar",
        }))
        seen_ids.add(block_id)

    anchored.sort(key=lambda x: x[0].start_utc)

    # --- 2. Place due routines. ---
    routines_today = [r for r in plan.get("routines", []) if r["status"] == "active" and _routine_occurs_on(r, target, now_tz)]
    routines_scheduled = 0
    for r in routines_today:
        pw = r["preferred_window"]
        pw_start = resolve_utc(target_str, pw["start"], pw["tz"], now_tz=now_tz)
        pw_end = resolve_utc(target_str, pw["end"], pw["tz"], now_tz=now_tz)
        slot = _find_slot(anchored, pw_start, pw_end, r["duration_minutes"], day_start, day_end)
        if slot is None:
            # Try anywhere on the day.
            slot = _find_slot(anchored, day_start, day_end, r["duration_minutes"], day_start, day_end)
        if slot is None:
            notes.append(f"routine {r['title']!r} could not be placed — no free slot ≥ {r['duration_minutes']}m")
            continue
        iv, _start_time_str, _end_time_str = slot
        tz_for_display = now_tz if pw["tz"] == FLOATING else ZoneInfo(pw["tz"])
        local_date, local_start = _local_time_str(iv.start_utc, tz_for_display)
        _, local_end = _local_time_str(iv.end_utc, tz_for_display)
        blk = {
            "id": f"block-rt{routines_scheduled:06x}",
            "date": local_date,
            "start_time": local_start,
            "end_time": local_end,
            "tz": pw["tz"],
            "item_type": "routine",
            "item_id": r["id"],
            "google_event_id": None,
            "status": "proposed",
            "title": r["title"],
        }
        anchored.append((iv, blk))
        anchored.sort(key=lambda x: x[0].start_utc)
        routines_scheduled += 1

    # --- 3. Rank tasks. ---
    goals_by_id = {g["id"]: g for g in plan.get("goals", [])}
    ranked_tasks = rank_tasks(plan.get("tasks", []), goals_by_id, target)

    # --- 4. Fill gaps. ---
    filled: list[tuple[Interval, dict[str, Any]]] = list(anchored)
    placed_task_ids: set[str] = set()
    gap_iter_safety = 0
    while gap_iter_safety < 100:
        gap_iter_safety += 1
        gap = _largest_gap(filled, day_start, day_end)
        if gap is None or gap.minutes() < MIN_BLOCK_MINUTES:
            break
        available = int(gap.minutes() * (1 - buffer_pct))
        # Find highest-ranked remaining task whose estimate fits.
        placed = False
        for t in ranked_tasks:
            if t["id"] in placed_task_ids:
                continue
            est = min(t["estimate_minutes"], MAX_BLOCK_MINUTES)
            if est <= available:
                iv = Interval(gap.start_utc, gap.start_utc + timedelta(minutes=est))
                tz_for_display = now_tz
                local_date, local_start = _local_time_str(iv.start_utc, tz_for_display)
                _, local_end = _local_time_str(iv.end_utc, tz_for_display)
                blk = {
                    "id": f"block-tk{len(placed_task_ids):06x}",
                    "date": local_date,
                    "start_time": local_start,
                    "end_time": local_end,
                    "tz": tz_name(tz_for_display),
                    "item_type": "task",
                    "item_id": t["id"],
                    "google_event_id": None,
                    "status": "proposed",
                    "title": t["title"],
                }
                filled.append((iv, blk))
                filled.sort(key=lambda x: x[0].start_utc)
                placed_task_ids.add(t["id"])
                placed = True
                break
        if not placed:
            break

    # --- 5. Overcommitment check. ---
    # Semantics: overcommitted if any unplaced task can't fit in any remaining gap.
    # Tasks don't split across gaps, so summing gaps is wrong.
    remaining_open = [t for t in ranked_tasks if t["id"] not in placed_task_ids]
    remaining_gaps = [g for g in _all_gaps(filled, day_start, day_end) if g.minutes() >= MIN_BLOCK_MINUTES]
    max_gap_avail = max((int(g.minutes() * (1 - buffer_pct)) for g in remaining_gaps), default=0)
    unfit = [t for t in remaining_open if min(t["estimate_minutes"], MAX_BLOCK_MINUTES) > max_gap_avail]
    overcommitted = len(unfit) > 0
    defer_candidate = unfit[-1]["id"] if unfit else None
    if overcommitted:
        total_unfit = sum(t["estimate_minutes"] for t in unfit)
        notes.append(f"overcommitted: {len(unfit)} task(s) totaling {total_unfit}m don't fit any remaining gap; defer candidate {defer_candidate}")

    # --- 6. Buffer blocks. ---
    buffer_count = 0
    for gap in _all_gaps(filled, day_start, day_end):
        if gap.minutes() < MIN_BLOCK_MINUTES:
            continue
        local_date, local_start = _local_time_str(gap.start_utc, now_tz)
        _, local_end = _local_time_str(gap.end_utc, now_tz)
        filled.append((gap, {
            "id": f"block-bf{buffer_count:06x}",
            "date": local_date,
            "start_time": local_start,
            "end_time": local_end,
            "tz": tz_name(now_tz),
            "item_type": "buffer",
            "item_id": None,
            "google_event_id": None,
            "status": "proposed",
            "title": "buffer",
        }))
        buffer_count += 1
    filled.sort(key=lambda x: x[0].start_utc)

    return {
        "date": target_str,
        "blocks": [b for _, b in filled],
        "overcommitted": overcommitted,
        "defer_candidate_id": defer_candidate,
        "notes": notes,
        "generated_at": now_utc_iso(),
    }


# ----- gap helpers -----
def _all_gaps(placed: list[tuple[Interval, dict[str, Any]]], day_start: datetime, day_end: datetime) -> list[Interval]:
    if not placed:
        return [Interval(day_start, day_end)]
    sorted_placed = sorted(placed, key=lambda x: x[0].start_utc)
    gaps: list[Interval] = []
    cursor = day_start
    for iv, _ in sorted_placed:
        if iv.end_utc <= cursor:
            continue  # fully before cursor
        if iv.start_utc > cursor:
            gaps.append(Interval(cursor, min(iv.start_utc, day_end)))
        cursor = max(cursor, iv.end_utc)
        if cursor >= day_end:
            break
    if cursor < day_end:
        gaps.append(Interval(cursor, day_end))
    return gaps


def _largest_gap(placed: list[tuple[Interval, dict[str, Any]]], day_start: datetime, day_end: datetime) -> Interval | None:
    gaps = _all_gaps(placed, day_start, day_end)
    if not gaps:
        return None
    return max(gaps, key=lambda g: g.minutes())


def _find_slot(
    placed: list[tuple[Interval, dict[str, Any]]],
    window_start: datetime,
    window_end: datetime,
    duration_min: int,
    day_start: datetime,
    day_end: datetime,
) -> tuple[Interval, str, str] | None:
    all_gaps = _all_gaps(placed, day_start, day_end)
    for gap in all_gaps:
        overlap_start = max(gap.start_utc, window_start)
        overlap_end = min(gap.end_utc, window_end)
        if minutes_between(overlap_start, overlap_end) >= duration_min:
            iv = Interval(overlap_start, overlap_start + timedelta(minutes=duration_min))
            return iv, iv.start_utc.strftime("%H:%M"), iv.end_utc.strftime("%H:%M")
    return None


# ----- CLI -----
def _load_events(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    if path == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="schedule_day")
    parser.add_argument("--plan", help="path to plan.json (defaults to CHRONOS_HOME)")
    parser.add_argument("--events", help="path to events JSON, or '-' for stdin")
    parser.add_argument("--date", help="target date YYYY-MM-DD (default: today)")
    args = parser.parse_args(argv)

    if args.plan:
        plan = json.loads(Path(args.plan).read_text())
    else:
        plan = plan_store.load()
    events = _load_events(args.events)
    now_tz = system_tz()
    target = date.fromisoformat(args.date) if args.date else datetime.now(now_tz).date()

    proposal = build_proposal(plan, events, target, now_tz=now_tz)
    json.dump(proposal, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
