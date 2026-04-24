# Scheduling heuristics

Rules used by `scripts/schedule_day.py` to build a day proposal and by `scripts/next_action.py` to pick a next action.

## `/chronos today` algorithm

Inputs:
- Plan (from `plan_store`).
- Pulled events for target date (and tomorrow for late-night sessions), already normalized.
- Target date (default: today in user's current system TZ).

Output: a list of `schedule_blocks` for the date, with `status: "proposed"`.

Steps:

1. **Anchor fixed events.** Insert every pulled event as a `schedule_blocks` entry with `item_type: "external"` (or `"task"` if it's a chronos-tagged event, keyed by `chronos_task_id`). Preserve `tz` from the event.

2. **Place due routines.** For every routine whose cadence matches the target date (resolved in `cadence.tz`):
   - Try to fit its `duration_minutes` inside its `preferred_window`.
   - If the window is blocked, find the nearest free slot ≥ duration on the same day. Slot must end before 22:00 user-local.
   - If no slot fits, emit a `conflicted` block at the preferred window start with a note.

3. **Rank remaining tasks.** From open tasks, sort by:
   1. `deadline_proximity` — `(deadline - date).days` ascending; null deadlines last.
   2. `goal_alignment` — tasks linked to an `active` goal score higher.
   3. `priority` — `high > medium > low`.
   4. `duration_fit` — prefer tasks whose `estimate_minutes` fits into the largest remaining gap.

4. **Fill gaps.** Walk gaps between anchored blocks in chronological order. For each gap, pick the highest-ranked task whose `estimate_minutes` ≤ gap minutes × (1 − `buffer_pct`). Insert as a `task` block. Remove from the pool. Continue until gap is under 15 minutes or tasks are exhausted.

5. **Reserve buffer.** Any unused tail of a gap becomes a `buffer` block (up to `buffer_pct` of original gap). Buffer blocks don't get pushed to Google Calendar.

6. **Overcommitment check.** If total estimated task minutes > available gap minutes × (1 − `buffer_pct`):
   - Emit the overcommitment flag on the proposal.
   - Name the lowest-ranked task that didn't fit as the defer candidate.

## `/chronos next` algorithm

Inputs: plan, today's cached events, current datetime (user local).

Return one of three shapes:

- **`active`** — current time is inside an accepted or synced schedule_block:
  ```json
  { "kind": "active", "title": "...", "ends_at": "...", "minutes_remaining": 22 }
  ```

- **`gap`** — current time is in a gap:
  ```json
  { "kind": "gap", "task_id": "...", "title": "...", "minutes_available": 45, "why": "..." }
  ```
  Pick the highest-ranked open task whose `estimate_minutes` ≤ gap minutes × (1 − buffer_pct). Bias toward tasks that match an energy window the current time falls in (e.g. `deep_work`). `why` is one sentence.

- **`ahead`** — gap is short (< 10 min) or there's nothing in the pool that fits:
  ```json
  { "kind": "ahead", "suggestion": "...", "linked_goal_id": "..." }
  ```
  Either suggest a goal-advancing micro-task ("spend 10 min on X toward Y") or suggest banking the time.

Tie-breaking: stable by task `id` to make the output deterministic for tests.

## Buffer percentage

Default `buffer_pct: 0.15`. In M5, `scripts/plan_store.py autotune-buffer` recomputes this from the last 14 days of `status: "done"` blocks: compare estimated vs actual (close-out time). If estimates systematically undershoot, bump the buffer.

## Constants

- Schedulable window: 06:00–22:00 user local. Outside this, routines are skipped and tasks aren't placed. (Early-morning routines like 7am gym fall in-window.)
- Minimum block: 15 minutes. Shorter gaps are absorbed into buffer.
- Maximum single block: 120 minutes. Longer tasks are split with a 10-minute break between halves.
