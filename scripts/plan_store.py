"""CRUD + CLI for ~/.chronos/plan.json.

Usage examples:
  python3 -m scripts.plan_store show
  python3 -m scripts.plan_store summary
  python3 -m scripts.plan_store gaps
  python3 -m scripts.plan_store add-goal --title "..." --target-date 2026-07-24 --success-criteria "..."
  python3 -m scripts.plan_store add-routine --title "Gym" --days mon,wed,fri --start 07:00 --end 08:00 --duration 60
  python3 -m scripts.plan_store add-task --title "Design doc" --estimate 90 --priority high --deadline 2026-04-30 --linked-goal-id goal-xxx
  python3 -m scripts.plan_store set-block --id block-xxx --status accepted
  python3 -m scripts.plan_store archive

Data directory: ~/.chronos/  (override with CHRONOS_HOME env var, used by tests).
stdout = structured data. stderr = diagnostics.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ._schema import SCHEMA_VERSION, SchemaError, empty_plan, migrate, validate
from ._time import FLOATING, now_utc_iso, system_tz


# ----- paths -----
def chronos_home() -> Path:
    root = os.environ.get("CHRONOS_HOME")
    if root:
        return Path(root)
    return Path.home() / ".chronos"


def plan_path() -> Path:
    return chronos_home() / "plan.json"


def archive_dir() -> Path:
    return chronos_home() / "archive"


# ----- load / save -----
def load() -> dict[str, Any]:
    p = plan_path()
    if not p.exists():
        plan = empty_plan()
        save(plan)
        return plan
    raw = json.loads(p.read_text())
    raw = migrate(raw)
    validate(raw)
    return raw


def save(plan: dict[str, Any]) -> None:
    validate(plan)
    home = chronos_home()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = plan_path()
    # Atomic write: tempfile + rename.
    fd, tmp = tempfile.mkstemp(dir=home, prefix=".plan-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(plan, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


# ----- id gen -----
def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


# ----- mutations -----
def add_goal(plan: dict[str, Any], *, title: str, target_date: str | None, success_criteria: str | None) -> dict[str, Any]:
    entity = {
        "id": _new_id("goal"),
        "title": title,
        "target_date": target_date,
        "success_criteria": success_criteria or "",
        "status": "active",
        "updated_at": now_utc_iso(),
    }
    plan["goals"].append(entity)
    return entity


def add_routine(
    plan: dict[str, Any],
    *,
    title: str,
    days: list[str],
    window_start: str,
    window_end: str,
    duration_minutes: int,
    tz: str = FLOATING,
    linked_goal_id: str | None = None,
) -> dict[str, Any]:
    entity = {
        "id": _new_id("routine"),
        "title": title,
        "cadence": {"days": days, "tz": tz},
        "preferred_window": {"start": window_start, "end": window_end, "tz": tz},
        "duration_minutes": duration_minutes,
        "linked_goal_id": linked_goal_id,
        "status": "active",
        "updated_at": now_utc_iso(),
    }
    plan["routines"].append(entity)
    return entity


def add_task(
    plan: dict[str, Any],
    *,
    title: str,
    estimate_minutes: int,
    priority: str = "medium",
    deadline: str | None = None,
    linked_goal_id: str | None = None,
) -> dict[str, Any]:
    entity = {
        "id": _new_id("task"),
        "title": title,
        "estimate_minutes": estimate_minutes,
        "priority": priority,
        "deadline": deadline,
        "linked_goal_id": linked_goal_id,
        "status": "open",
        "updated_at": now_utc_iso(),
    }
    plan["tasks"].append(entity)
    return entity


def upsert_block(plan: dict[str, Any], block: dict[str, Any]) -> dict[str, Any]:
    block.setdefault("updated_at", now_utc_iso())
    block.setdefault("status", "proposed")
    for i, b in enumerate(plan["schedule_blocks"]):
        if b["id"] == block["id"]:
            plan["schedule_blocks"][i] = block
            return block
    plan["schedule_blocks"].append(block)
    return block


def set_block_status(plan: dict[str, Any], block_id: str, status: str) -> dict[str, Any]:
    for b in plan["schedule_blocks"]:
        if b["id"] == block_id:
            b["status"] = status
            b["updated_at"] = now_utc_iso()
            return b
    raise KeyError(f"block {block_id} not found")


# ----- summary + gaps -----
def summary(plan: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now(system_tz()).date()
    today_str = today.isoformat()
    open_tasks = [t for t in plan["tasks"] if t["status"] == "open"]
    overdue = [t for t in open_tasks if t.get("deadline") and t["deadline"] < today_str]
    active_goals = [g for g in plan["goals"] if g["status"] == "active"]
    todays_blocks = [b for b in plan["schedule_blocks"] if b["date"] == today_str]
    return {
        "today": today_str,
        "counts": {
            "goals_active": len(active_goals),
            "routines_active": len([r for r in plan["routines"] if r["status"] == "active"]),
            "tasks_open": len(open_tasks),
            "overdue_tasks": len(overdue),
            "todays_blocks": len(todays_blocks),
        },
        "overdue_task_ids": [t["id"] for t in overdue],
        "todays_block_ids": [b["id"] for b in todays_blocks],
    }


def gaps(plan: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    """Up to 3 ranked gap items the planning dialogue should surface."""
    today = today or datetime.now(system_tz()).date()
    today_str = today.isoformat()
    out: list[dict[str, Any]] = []

    # 1. Overdue open tasks (most urgent).
    for t in plan["tasks"]:
        if t["status"] == "open" and t.get("deadline") and t["deadline"] < today_str:
            out.append({"kind": "overdue_task", "id": t["id"], "message": f"Task {t['title']!r} deadline was {t['deadline']}."})

    # 2. Active goals with no linked open task updated in last 14 days.
    cutoff = (today - timedelta(days=14)).isoformat() + "T00:00:00Z"
    for g in plan["goals"]:
        if g["status"] != "active":
            continue
        linked = [t for t in plan["tasks"] if t.get("linked_goal_id") == g["id"] and t["status"] in ("open", "in_progress")]
        recent = [t for t in linked if (t.get("updated_at") or "") >= cutoff]
        if not recent:
            out.append({"kind": "stale_goal", "id": g["id"], "message": f"Goal {g['title']!r} has no recent linked tasks (14+ days)."})

    # 3. Active routines with no scheduled occurrence in last cadence-period.
    for r in plan["routines"]:
        if r["status"] != "active":
            continue
        last = max(
            (b["date"] for b in plan["schedule_blocks"] if b["item_type"] == "routine" and b.get("item_id") == r["id"]),
            default=None,
        )
        threshold = (today - timedelta(days=10)).isoformat()
        if last is None or last < threshold:
            out.append({"kind": "stale_routine", "id": r["id"], "message": f"Routine {r['title']!r} last scheduled: {last or 'never'}."})

    return out[:3]


# ----- archive -----
def archive(plan: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """Move past schedule_blocks and done tasks older than 30 days to archive/YYYY-MM.json."""
    today = today or datetime.now(system_tz()).date()
    cutoff_date = today - timedelta(days=30)
    cutoff_ts = cutoff_date.isoformat() + "T00:00:00Z"

    archived_blocks = [b for b in plan["schedule_blocks"] if b["date"] < cutoff_date.isoformat()]
    archived_tasks = [t for t in plan["tasks"] if t["status"] == "done" and (t.get("updated_at") or "") < cutoff_ts]

    if not archived_blocks and not archived_tasks:
        return {"archived_blocks": 0, "archived_tasks": 0}

    arch_dir = archive_dir()
    arch_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    month_key = cutoff_date.strftime("%Y-%m")
    arch_path = arch_dir / f"{month_key}.json"
    existing = json.loads(arch_path.read_text()) if arch_path.exists() else {"blocks": [], "tasks": []}
    existing["blocks"].extend(archived_blocks)
    existing["tasks"].extend(archived_tasks)
    arch_path.write_text(json.dumps(existing, indent=2, sort_keys=True))

    plan["schedule_blocks"] = [b for b in plan["schedule_blocks"] if b["date"] >= cutoff_date.isoformat()]
    plan["tasks"] = [t for t in plan["tasks"] if not (t["status"] == "done" and (t.get("updated_at") or "") < cutoff_ts)]

    return {"archived_blocks": len(archived_blocks), "archived_tasks": len(archived_tasks)}


# ----- CLI -----
def _print(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _parse_days(s: str) -> list[str]:
    return [d.strip().lower() for d in s.split(",") if d.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plan_store")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show", help="print the full plan")
    sub.add_parser("summary", help="print counts + today's items")
    sub.add_parser("gaps", help="print ranked gap items")
    sub.add_parser("archive", help="roll old blocks/tasks into archive")

    ag = sub.add_parser("add-goal")
    ag.add_argument("--title", required=True)
    ag.add_argument("--target-date")
    ag.add_argument("--success-criteria")

    ar = sub.add_parser("add-routine")
    ar.add_argument("--title", required=True)
    ar.add_argument("--days", required=True, help="comma-separated mon,tue,...")
    ar.add_argument("--start", required=True)
    ar.add_argument("--end", required=True)
    ar.add_argument("--duration", type=int, required=True)
    ar.add_argument("--tz", default=FLOATING)
    ar.add_argument("--linked-goal-id")

    at = sub.add_parser("add-task")
    at.add_argument("--title", required=True)
    at.add_argument("--estimate", type=int, required=True)
    at.add_argument("--priority", default="medium", choices=["low", "medium", "high"])
    at.add_argument("--deadline")
    at.add_argument("--linked-goal-id")

    sb = sub.add_parser("set-block")
    sb.add_argument("--id", required=True)
    sb.add_argument("--status", required=True, choices=["proposed", "accepted", "synced", "done", "conflicted"])

    args = parser.parse_args(argv)
    try:
        plan = load()
    except SchemaError as e:
        sys.stderr.write(f"schema error: {e}\n")
        return 2

    try:
        if args.cmd == "show":
            _print(plan)
            return 0
        if args.cmd == "summary":
            _print(summary(plan))
            return 0
        if args.cmd == "gaps":
            _print({"gaps": gaps(plan)})
            return 0
        if args.cmd == "archive":
            result = archive(plan)
            save(plan)
            _print(result)
            return 0
        if args.cmd == "add-goal":
            entity = add_goal(plan, title=args.title, target_date=args.target_date, success_criteria=args.success_criteria)
            save(plan)
            _print(entity)
            return 0
        if args.cmd == "add-routine":
            entity = add_routine(
                plan,
                title=args.title,
                days=_parse_days(args.days),
                window_start=args.start,
                window_end=args.end,
                duration_minutes=args.duration,
                tz=args.tz,
                linked_goal_id=args.linked_goal_id,
            )
            save(plan)
            _print(entity)
            return 0
        if args.cmd == "add-task":
            entity = add_task(
                plan,
                title=args.title,
                estimate_minutes=args.estimate,
                priority=args.priority,
                deadline=args.deadline,
                linked_goal_id=args.linked_goal_id,
            )
            save(plan)
            _print(entity)
            return 0
        if args.cmd == "set-block":
            entity = set_block_status(plan, args.id, args.status)
            save(plan)
            _print(entity)
            return 0
    except (SchemaError, KeyError, ValueError) as e:
        sys.stderr.write(f"error: {e}\n")
        return 1

    sys.stderr.write(f"unknown command: {args.cmd}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
