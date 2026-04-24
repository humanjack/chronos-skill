# Chronos data model (schema v1)

Source of truth: `~/.chronos/plan.json`. Validated by [scripts/_schema.py](../scripts/_schema.py) on every read and write.

## Top-level shape

```json
{
  "schema_version": 1,
  "timezone": "America/Los_Angeles",
  "preferences": {
    "buffer_pct": 0.15,
    "energy_windows": {
      "deep_work": { "start": "08:00", "end": "11:00", "tz": "floating" }
    }
  },
  "goals": [ ... ],
  "routines": [ ... ],
  "tasks": [ ... ],
  "schedule_blocks": [ ... ],
  "calendar_sync": {
    "last_pull_at": "2026-04-24T17:32:11Z",
    "last_push_at": null,
    "primary_calendar_id": null
  }
}
```

- `schema_version` — integer. `plan_store` runs forward-only migrations on load when this lags the current version.
- `timezone` — IANA string. Default hint for new inputs; not authoritative for rendering (system TZ wins).
- `preferences.buffer_pct` — fraction of gap time reserved as buffer in `/chronos today`. Default 0.15.
- `preferences.energy_windows` — optional named wall-clock windows used by `/chronos next` to bias gap suggestions.

## Entities

### Goals

```json
{
  "id": "goal-a1b2c3",
  "title": "Ship chronos v1",
  "target_date": "2026-07-24",
  "success_criteria": "Daily usage for 14 days; ≥20 round-trip edits.",
  "status": "active",
  "updated_at": "2026-04-24T17:32:11Z"
}
```

- `id` — `goal-<token>`; stable.
- `target_date` — date-only (`YYYY-MM-DD`). No timezone — target dates are calendar-day concepts.
- `status` — `active | done | dropped`.

### Routines

```json
{
  "id": "routine-d4e5f6",
  "title": "Gym",
  "cadence": { "days": ["mon", "wed", "fri"], "tz": "floating" },
  "preferred_window": { "start": "07:00", "end": "08:00", "tz": "floating" },
  "duration_minutes": 60,
  "linked_goal_id": null,
  "status": "active",
  "updated_at": "2026-04-24T17:32:11Z"
}
```

- `cadence.days` — lowercase day abbreviations from `mon|tue|wed|thu|fri|sat|sun`. Interpreted in `cadence.tz` — `floating` means user's current local days.
- `preferred_window` — wall-clock window; see timezone modes below.
- `duration_minutes` — how long each occurrence takes.

### Tasks

```json
{
  "id": "task-g7h8i9",
  "title": "Draft design doc",
  "estimate_minutes": 90,
  "priority": "high",
  "deadline": "2026-04-30",
  "linked_goal_id": "goal-a1b2c3",
  "status": "open",
  "updated_at": "2026-04-24T17:32:11Z"
}
```

- `priority` — `low | medium | high`. Used in the ordering rule in [scheduling-heuristics.md](scheduling-heuristics.md).
- `deadline` — date-only; nullable.
- `status` — `open | in_progress | done | deferred`.

### Schedule blocks

```json
{
  "id": "block-j1k2l3",
  "date": "2026-04-28",
  "start_time": "09:00",
  "end_time": "10:30",
  "tz": "America/Los_Angeles",
  "item_type": "task",
  "item_id": "task-g7h8i9",
  "google_event_id": null,
  "status": "proposed",
  "updated_at": "2026-04-24T17:32:11Z"
}
```

- `item_type` — `task | routine | buffer | external`.
- `item_id` — foreign key into `tasks`/`routines`; `null` for `buffer` and `external`.
- `tz` — see timezone modes.
- `status` — `proposed | accepted | synced | done | conflicted`.
- `google_event_id` — set after push; used for reconciliation.

## Timezone modes

Every time-bearing field carries an explicit `tz`. Three modes:

| Mode | `tz` value | Meaning | Used for |
|---|---|---|---|
| Floating | `"floating"` | Wall-clock, follows user's current system TZ | Routines, preference windows |
| Zoned | IANA name (e.g. `"America/Los_Angeles"`) | Wall-clock pinned to that zone | Distributed-team meetings, client calls |
| UTC | `"UTC"` | Absolute instant | System timestamps only (`*_at` fields) |

Canonical UTC instants are derived on read via `scripts._time.resolve_utc(date, time, tz, now_tz=…)`. Never stored denormalized — avoids drift on DST transitions.

System timestamps (`updated_at`, `last_pull_at`, etc.) are UTC ISO 8601 with a `Z` suffix.

## IDs

`<entity>-<8-char-token>` where token is `secrets.token_hex(4)`. Stable across edits.

## Migrations

- Any non-backward-compatible schema change bumps `schema_version`.
- `scripts._schema.MIGRATIONS` is an ordered dict `{from_version: migration_fn}`.
- `plan_store.load()` applies migrations in order before validation.
- Migrations are pure functions: dict-in, dict-out.
