# Google Calendar sync contract

Rules used by `scripts/calendar_sync.py` and invoked by SKILL.md's `/chronos sync` mode. The script is a pure data-transform — Claude performs the actual MCP calls and feeds results in.

## Tagging

Chronos-owned events are tagged two ways (both must be present):

1. **Extended property:** `extendedProperties.private.chronos_task_id = <id>` — machine-readable, survives user edits of title/description.
2. **Description prefix:** first line is `[chronos]` — human-visible signal that the event is managed.

A missing extended property but present `[chronos]` prefix → treat as chronos-owned but orphaned (log a warning, try to recover via other signals like exact title+time match; fall back to orphan surface).

## Sync flow

`/chronos sync` runs:

1. **Pull.** Claude calls `list_events` for today + 1 day (and longer windows on demand). Feeds the raw event array to `calendar_sync.py reconcile` via stdin.
2. **Reconcile.** Script compares pulled events against local `schedule_blocks` and emits an action list on stdout:
   - `create` — local block has no `google_event_id`; needs to be pushed.
   - `update` — local block has `google_event_id` and some field diverged from remote.
   - `pull_time_change` — remote event's time changed; update local block.
   - `mark_conflicted` — external (untagged) event overlaps a chronos block.
   - `orphan` — chronos-tagged event has no matching local block.
   - `missing_remote` — local block has `google_event_id` that's gone upstream.
3. **Apply.** Claude executes each action via MCP tools (`create_event`, `update_event`) and feeds results back to `calendar_sync.py apply` to persist `google_event_id` + new timestamps.
4. **Report.** Summarize to user: counts per action type, notable conflicts, any orphans to resolve.

## Reconciliation rules

For each pulled event **E**:
- If `E.chronos_task_id` matches a local block's `item_id` and times differ → `pull_time_change` (remote wins for time; user may have dragged in Google UI).
- If `E.chronos_task_id` is present but no matching local block → `orphan`.
- If `E` is untagged and overlaps an accepted/synced local block → `mark_conflicted` on the local block.
- Else ignore (external event, no action).

For each local block **B** with `status in {accepted, synced}`:
- If `B.google_event_id is None` → `create`.
- If `B.google_event_id` doesn't appear in pulled events → `missing_remote`. Status must become `proposed` (unsynced) or `done` based on `B.date` vs today.
- If local fields diverged from matched remote (title, item_id) → `update`.

## Time modes on push

- `B.tz == "floating"` → resolve to user's current system TZ at push time; persist the resolved IANA name back onto `B.tz` so subsequent syncs are stable.
- `B.tz` is IANA → push `dateTime` (wall-clock without `Z`) + `timeZone` IANA string. Do not translate to UTC.
- `B.tz == "UTC"` → push `dateTime` with `Z` suffix; `timeZone` optional.

Google's event body on push:

```json
{
  "summary": "[chronos] <block title>",
  "description": "[chronos]\nmanaged block for <item_type>:<item_id>",
  "start": { "dateTime": "2026-04-28T09:00:00", "timeZone": "America/Los_Angeles" },
  "end":   { "dateTime": "2026-04-28T10:30:00", "timeZone": "America/Los_Angeles" },
  "extendedProperties": {
    "private": { "chronos_task_id": "task-g7h8i9", "chronos_block_id": "block-j1k2l3" }
  }
}
```

## Orphan resolution

When `orphan` actions are emitted, Claude presents them to the user with three choices:
- **Re-link** — pick an existing plan entity; update local + push an `update_event` with the matching `chronos_task_id`.
- **Adopt** — create a new task from the event's title; link and persist.
- **Delete** — remove the event from Google Calendar.

Don't auto-resolve. Orphans imply the user did something unusual (copied an event, restored from trash) and should confirm.

## Idempotency

- `reconcile` is read-only on the plan; it only emits actions.
- `apply` is idempotent: applying the same action twice yields the same state.
- Every action carries a unique `action_id` so apply-errors can be surfaced by id.
