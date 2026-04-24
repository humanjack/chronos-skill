# chronos-skill

A Claude skill for time management. Plan across long-term goals, routines, and short-term tasks; schedule today; pick what to work on next; sync bidirectionally with Google Calendar.

## Layout

- [SKILL.md](SKILL.md) — skill entry with mode dispatch (plan, today, sync, next).
- [scripts/](scripts/) — `plan_store.py`, `schedule_day.py`, `next_action.py`, `calendar_sync.py`, plus `_time.py` / `_schema.py` helpers. Pure-stdlib Python; scripts emit structured JSON on stdout, diagnostics on stderr.
- [references/](references/) — data model, planning dialogue, scheduling heuristics, sync contract. Loaded on demand by the skill.
- [evals/](evals/) — 12 test cases per the [agentskills.io evaluation framework](https://agentskills.io/skill-creation/evaluating-skills), plus fixtures under `evals/files/`.

## Runtime data

`~/.chronos/plan.json` — single JSON source of truth. Schema v1 covers goals, routines, tasks, schedule_blocks, preferences, and calendar_sync metadata. Atomic writes, mode 0600, schema validation on every read/write. Monthly archive under `~/.chronos/archive/`. See [references/data-model.md](references/data-model.md).

Override storage location with `CHRONOS_HOME=/path`.

## Timezone handling

Every time-bearing field carries a `tz` in one of three modes:

| Mode | Example `tz` | Used for |
|---|---|---|
| Floating | `"floating"` | Routines, preference windows (follows user) |
| Zoned | `"America/Los_Angeles"` | Distributed-team meetings, client calls |
| UTC | `"UTC"` | System timestamps (`*_at` fields) |

UTC is derived on read. The user's current system TZ is read live every invocation for rendering and floating resolution.

## Development

```bash
# Run the full test suite (stdlib unittest, no external deps).
python3 -m unittest discover scripts -p "test_*.py"

# CLI smoke test against a fixture plan.
python3 -m scripts.schedule_day \
  --plan evals/files/plan-overcommitted.json \
  --events evals/files/events-back-to-back.json \
  --date 2026-04-28
```

Scripts require Python 3.9+ (for `zoneinfo`).

## Issues

- [#1](https://github.com/humanjack/chronos-skill/issues/1) — umbrella issue with v1 feature scope.
- [#2](https://github.com/humanjack/chronos-skill/issues/2) — implementation, test, and evaluation plan.
