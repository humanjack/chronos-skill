"""Reconcile local plan against pulled Google Calendar events. Pure data transform.

Claude performs MCP I/O and feeds results in via stdin or --events/--results flags.

Subcommands:
  normalize    — convert raw Google event list into chronos-normalized shape
  reconcile    — emit action list {create,update,pull_time_change,mark_conflicted,orphan,missing_remote}
  apply        — given a results list {action_id, google_event_id, remote}, persist IDs back to the plan

All commands read plan from CHRONOS_HOME; emit JSON on stdout; diagnostics on stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from . import plan_store
from ._time import FLOATING, now_utc_iso


CHRONOS_TAG_PREFIX = "[chronos]"


# ---------- normalize ----------
def normalize_event(ev: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw Google event dict into chronos-normalized shape.

    Handles both `start.dateTime + timeZone` and `start.date` (all-day).
    """
    start = ev.get("start") or {}
    end = ev.get("end") or {}
    ext = ((ev.get("extendedProperties") or {}).get("private") or {})
    description = ev.get("description") or ""

    is_chronos = "chronos_task_id" in ext or description.startswith(CHRONOS_TAG_PREFIX)

    if "dateTime" in start:
        # Timed event.
        dt = start["dateTime"]
        tz = start.get("timeZone") or "UTC"
        date_part, time_part = _split_datetime(dt, tz)
        _end_date, end_time = _split_datetime(end.get("dateTime") or dt, end.get("timeZone") or tz)
        return {
            "google_event_id": ev.get("id"),
            "date": date_part,
            "start_time": time_part,
            "end_time": end_time,
            "tz": tz,
            "title": ev.get("summary", ""),
            "chronos_task_id": ext.get("chronos_task_id"),
            "chronos_block_id": ext.get("chronos_block_id"),
            "is_chronos": is_chronos,
            "is_all_day": False,
        }
    # All-day.
    return {
        "google_event_id": ev.get("id"),
        "date": start.get("date"),
        "start_time": "00:00",
        "end_time": "23:59",
        "tz": "UTC",
        "title": ev.get("summary", ""),
        "chronos_task_id": ext.get("chronos_task_id"),
        "chronos_block_id": ext.get("chronos_block_id"),
        "is_chronos": is_chronos,
        "is_all_day": True,
    }


def _split_datetime(iso: str, tz: str) -> tuple[str, str]:
    """Split ISO datetime into (YYYY-MM-DD, HH:MM). Keeps wall-clock time in the event's TZ."""
    # Google returns e.g. "2026-04-28T09:00:00-07:00" or "...Z"; we want the wall-clock date+time
    # in the event's declared TZ, not UTC-shifted.
    if tz in ("UTC", "", None):
        # Parse and normalize.
        s = iso.rstrip("Z")
        date_part, time_part = s.split("T")
        return date_part, time_part[:5]
    # Use only the date+time portion before the offset (Google already expresses it in TZ's wall-clock).
    s = iso
    # Strip offset: find last '+' or '-' after the 'T' (avoid date's '-')
    t_idx = s.find("T")
    if t_idx < 0:
        return s, "00:00"
    sign_idx = max(s.rfind("+"), s.rfind("-"))
    if sign_idx > t_idx:
        s = s[:sign_idx]
    elif s.endswith("Z"):
        s = s[:-1]
    date_part, time_part = s.split("T")
    return date_part, time_part[:5]


# ---------- reconcile ----------
def reconcile(plan: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Emit an action list for Claude to execute via MCP."""
    actions: list[dict[str, Any]] = []
    blocks_by_gid = {b["google_event_id"]: b for b in plan.get("schedule_blocks", []) if b.get("google_event_id")}
    seen_gids: set[str] = set()

    # Walk pulled events.
    for ev in events:
        gid = ev.get("google_event_id")
        if gid:
            seen_gids.add(gid)

        if ev.get("is_chronos"):
            # Chronos-tagged: expect a matching local block.
            matched = None
            # Match by chronos_block_id first, then google_event_id, then chronos_task_id.
            if ev.get("chronos_block_id"):
                matched = next((b for b in plan["schedule_blocks"] if b["id"] == ev["chronos_block_id"]), None)
            if not matched and gid:
                matched = blocks_by_gid.get(gid)
            if not matched and ev.get("chronos_task_id"):
                matched = next(
                    (b for b in plan["schedule_blocks"] if b.get("item_id") == ev["chronos_task_id"] and not b.get("google_event_id")),
                    None,
                )
            if not matched:
                actions.append({
                    "action_id": _aid(),
                    "type": "orphan",
                    "google_event_id": gid,
                    "event": ev,
                })
                continue
            # Check for time drift.
            if (
                matched["start_time"] != ev["start_time"]
                or matched["end_time"] != ev["end_time"]
                or matched["date"] != ev["date"]
            ):
                actions.append({
                    "action_id": _aid(),
                    "type": "pull_time_change",
                    "block_id": matched["id"],
                    "google_event_id": gid,
                    "new": {"date": ev["date"], "start_time": ev["start_time"], "end_time": ev["end_time"], "tz": ev["tz"]},
                })
        else:
            # External event. Flag overlaps with accepted/synced chronos blocks.
            for b in plan.get("schedule_blocks", []):
                if b.get("status") not in ("accepted", "synced"):
                    continue
                if b.get("date") != ev.get("date"):
                    continue
                if _overlaps(b, ev):
                    actions.append({
                        "action_id": _aid(),
                        "type": "mark_conflicted",
                        "block_id": b["id"],
                        "conflicting_event": {"google_event_id": ev.get("google_event_id"), "title": ev.get("title")},
                    })

    # Walk local blocks needing attention.
    for b in plan.get("schedule_blocks", []):
        if b.get("status") in ("accepted",) and not b.get("google_event_id"):
            actions.append({
                "action_id": _aid(),
                "type": "create",
                "block_id": b["id"],
                "block": b,
            })
        elif b.get("google_event_id") and b["google_event_id"] not in seen_gids and b.get("status") == "synced":
            actions.append({
                "action_id": _aid(),
                "type": "missing_remote",
                "block_id": b["id"],
                "google_event_id": b["google_event_id"],
            })
    return actions


def _aid() -> str:
    return f"action-{uuid.uuid4().hex[:8]}"


def _overlaps(block: dict[str, Any], ev: dict[str, Any]) -> bool:
    # Compare wall-clock ranges on the same date, ignoring tz differences for flagging (conservative).
    return not (block["end_time"] <= ev["start_time"] or ev["end_time"] <= block["start_time"])


# ---------- apply ----------
def apply_results(plan: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist MCP results back onto plan. Each result: {action_id, type, block_id, google_event_id?, new?}."""
    updates: dict[str, int] = {"created": 0, "updated": 0, "time_pulled": 0, "conflicted": 0, "orphans": 0, "missing_remote_cleared": 0}
    by_id = {b["id"]: b for b in plan.get("schedule_blocks", [])}

    for r in results:
        t = r.get("type")
        block_id = r.get("block_id")
        if t == "create" and block_id in by_id:
            by_id[block_id]["google_event_id"] = r.get("google_event_id")
            by_id[block_id]["status"] = "synced"
            by_id[block_id]["updated_at"] = now_utc_iso()
            updates["created"] += 1
        elif t == "update" and block_id in by_id:
            by_id[block_id]["updated_at"] = now_utc_iso()
            updates["updated"] += 1
        elif t == "pull_time_change" and block_id in by_id:
            new = r.get("new", {})
            by_id[block_id].update({
                k: v for k, v in new.items() if k in ("date", "start_time", "end_time", "tz")
            })
            by_id[block_id]["updated_at"] = now_utc_iso()
            updates["time_pulled"] += 1
        elif t == "mark_conflicted" and block_id in by_id:
            by_id[block_id]["status"] = "conflicted"
            by_id[block_id]["updated_at"] = now_utc_iso()
            updates["conflicted"] += 1
        elif t == "orphan":
            updates["orphans"] += 1  # no local mutation; surfaced to user
        elif t == "missing_remote" and block_id in by_id:
            by_id[block_id]["google_event_id"] = None
            by_id[block_id]["status"] = "proposed"
            by_id[block_id]["updated_at"] = now_utc_iso()
            updates["missing_remote_cleared"] += 1

    plan["calendar_sync"]["last_pull_at"] = now_utc_iso()
    return updates


# ---------- CLI ----------
def _load_json(path: str | None) -> Any:
    if not path:
        return []
    if path == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="calendar_sync")
    sub = parser.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("normalize", help="normalize raw Google events")
    n.add_argument("--events", required=True, help="path to raw events JSON, or '-' for stdin")

    r = sub.add_parser("reconcile", help="emit action list from normalized events")
    r.add_argument("--events", required=True, help="path to normalized events JSON, or '-' for stdin")

    a = sub.add_parser("apply", help="persist MCP results back to plan")
    a.add_argument("--results", required=True, help="path to results JSON, or '-' for stdin")

    args = parser.parse_args(argv)
    if args.cmd == "normalize":
        events = _load_json(args.events)
        normalized = [normalize_event(e) for e in events]
        json.dump(normalized, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    plan = plan_store.load()
    if args.cmd == "reconcile":
        events = _load_json(args.events)
        actions = reconcile(plan, events)
        json.dump({"actions": actions}, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    if args.cmd == "apply":
        results = _load_json(args.results)
        summary = apply_results(plan, results)
        plan_store.save(plan)
        json.dump(summary, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
