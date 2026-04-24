"""Pick a next-action given current time + plan + today's blocks/events.

Returns one of three shapes:
  {"kind": "active", "title": ..., "ends_at": ..., "minutes_remaining": int}
  {"kind": "gap", "task_id": ..., "title": ..., "minutes_available": int, "why": ...}
  {"kind": "ahead", "suggestion": ..., "linked_goal_id": ...}
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import plan_store, schedule_day
from ._time import FLOATING, minutes_between, resolve_utc, system_tz


AHEAD_GAP_THRESHOLD_MIN = 10


def _task_by_id(plan: dict[str, Any], tid: str | None) -> dict[str, Any] | None:
    if not tid:
        return None
    for t in plan.get("tasks", []):
        if t["id"] == tid:
            return t
    return None


def _block_interval(b: dict[str, Any], now_tz: ZoneInfo) -> tuple[datetime, datetime]:
    return (
        resolve_utc(b["date"], b["start_time"], b["tz"], now_tz=now_tz),
        resolve_utc(b["date"], b["end_time"], b["tz"], now_tz=now_tz),
    )


def _matches_energy_window(
    plan: dict[str, Any],
    now_local: datetime,
    now_tz: ZoneInfo,
) -> str | None:
    """Return energy-window name if now_local falls within one, else None."""
    windows = (plan.get("preferences") or {}).get("energy_windows") or {}
    today = now_local.strftime("%Y-%m-%d")
    for name, w in windows.items():
        start = resolve_utc(today, w["start"], w["tz"], now_tz=now_tz)
        end = resolve_utc(today, w["end"], w["tz"], now_tz=now_tz)
        now_utc = now_local.astimezone(ZoneInfo("UTC"))
        if start <= now_utc < end:
            return name
    return None


def pick_next(
    plan: dict[str, Any],
    events: list[dict[str, Any]],
    now_local: datetime,
    now_tz: ZoneInfo | None = None,
) -> dict[str, Any]:
    now_tz = now_tz or system_tz()
    now_utc = now_local.astimezone(ZoneInfo("UTC"))
    today = now_local.date()

    # Build today's proposal so "next" and "today" agree on the plan.
    proposal = schedule_day.build_proposal(plan, events, today, now_tz=now_tz)
    blocks = proposal["blocks"]

    # 1. Active block?
    for b in blocks:
        start, end = _block_interval(b, now_tz)
        if start <= now_utc < end and b["item_type"] != "buffer":
            return {
                "kind": "active",
                "title": b.get("title") or b.get("item_id") or "(untitled)",
                "ends_at": end.astimezone(now_tz).strftime("%H:%M"),
                "minutes_remaining": max(minutes_between(now_utc, end), 0),
            }

    # 2. Gap? Find the next upcoming block (or day-end) and the gap size from now to it.
    upcoming_starts = sorted(
        [_block_interval(b, now_tz)[0] for b in blocks if b["item_type"] != "buffer"]
        + [datetime.combine(today, schedule_day.WORKDAY_END, tzinfo=now_tz).astimezone(ZoneInfo("UTC"))]
    )
    next_edge = next((s for s in upcoming_starts if s > now_utc), None)
    if next_edge is None:
        return {"kind": "ahead", "suggestion": "banking time — no blocks remaining today", "linked_goal_id": None}

    gap_minutes = minutes_between(now_utc, next_edge)
    if gap_minutes < AHEAD_GAP_THRESHOLD_MIN:
        # Look for a goal-advancing suggestion.
        active_goals = [g for g in plan.get("goals", []) if g["status"] == "active"]
        if active_goals:
            return {
                "kind": "ahead",
                "suggestion": f"short gap — advance goal {active_goals[0]['title']!r} with a small step",
                "linked_goal_id": active_goals[0]["id"],
            }
        return {"kind": "ahead", "suggestion": "short gap — bank the time", "linked_goal_id": None}

    # Rank open tasks with same heuristic as schedule_day.
    goals_by_id = {g["id"]: g for g in plan.get("goals", [])}
    ranked = schedule_day._rank_tasks(plan.get("tasks", []), goals_by_id, today)

    buffer_pct = float((plan.get("preferences") or {}).get("buffer_pct", 0.15))
    available = int(gap_minutes * (1 - buffer_pct))

    energy = _matches_energy_window(plan, now_local, now_tz)

    # Prefer high-priority tasks when in a deep-work window.
    if energy == "deep_work":
        ranked = sorted(ranked, key=lambda t: (0 if t.get("priority") == "high" else 1,))

    for t in ranked:
        if min(t["estimate_minutes"], available) >= 15 and t["estimate_minutes"] <= available:
            why_bits = []
            if t.get("deadline"):
                why_bits.append(f"deadline {t['deadline']}")
            if t.get("linked_goal_id") and goals_by_id.get(t["linked_goal_id"], {}).get("status") == "active":
                why_bits.append(f"advances {goals_by_id[t['linked_goal_id']]['title']!r}")
            if energy:
                why_bits.append(f"fits {energy} window")
            why = "; ".join(why_bits) or "top of the ranked queue"
            return {
                "kind": "gap",
                "task_id": t["id"],
                "title": t["title"],
                "minutes_available": gap_minutes,
                "why": why,
            }

    return {"kind": "ahead", "suggestion": "no ranked task fits this gap — bank or advance a goal", "linked_goal_id": None}


# ----- CLI -----
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="next_action")
    parser.add_argument("--plan", help="path to plan.json (defaults to CHRONOS_HOME)")
    parser.add_argument("--events", help="path to events JSON, or '-' for stdin")
    parser.add_argument("--now", help="ISO local datetime (default: now)")
    args = parser.parse_args(argv)

    if args.plan:
        plan = json.loads(Path(args.plan).read_text())
    else:
        plan = plan_store.load()
    events = schedule_day._load_events(args.events)
    now_tz = system_tz()
    now_local = datetime.fromisoformat(args.now).replace(tzinfo=now_tz) if args.now else datetime.now(tz=now_tz)
    out = pick_next(plan, events, now_local, now_tz=now_tz)
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
