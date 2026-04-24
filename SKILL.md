---
name: chronos
description: Plan, schedule, and keep track of work across long-term goals, routines, and short-term tasks. Syncs bidirectionally with Google Calendar. Use when the user mentions planning, goal-setting, reviewing their week, asking what's on today, what to do next, or wants to sync their schedule with their calendar.
---

# chronos

Chronos is a time-management partner. It helps the user plan (goals, routines, tasks), schedule today, pick what to work on next, and keep Google Calendar in sync with the plan. Plan data lives at `~/.chronos/plan.json` (see [references/data-model.md](references/data-model.md)).

## When to use

Trigger on natural-language signals:

- **plan** — "help me plan", "set a goal", "add a routine", "I have some tasks this week", "review my plan".
- **today** — "plan my day", "what should today look like", "schedule today".
- **next** — "what should I do next", "what am I doing right now", "I finished early, what now".
- **sync** — "sync with my calendar", "pull my calendar", "something's off with my calendar".

Ambiguous phrasing: if the user's ask spans modes (e.g. "let me add a task then plan today"), handle each in order.

## Available scripts

All scripts emit structured JSON to stdout; diagnostics to stderr. Run from the skill directory root.

- **`python3 -m scripts.plan_store show|summary|gaps`** — read the plan. `summary` returns counts + today's items; `gaps` returns up to 3 ranked attention items.
- **`python3 -m scripts.plan_store add-goal|add-routine|add-task`** — create entities (see `--help` per subcommand).
- **`python3 -m scripts.plan_store set-block --id <id> --status <status>`** — update a schedule block.
- **`python3 -m scripts.plan_store archive`** — roll entries older than 30 days into monthly archive.
- **`python3 -m scripts.schedule_day [--events PATH|-] [--date YYYY-MM-DD]`** — build the time-block proposal for a day.
- **`python3 -m scripts.next_action [--events PATH|-] [--now ISO]`** — pick the next action given current time.
- **`python3 -m scripts.calendar_sync normalize|reconcile|apply`** — calendar sync pipeline (claude runs MCP, script reconciles).

## Workflow per mode

### plan

1. Run `python3 -m scripts.plan_store summary` and `python3 -m scripts.plan_store gaps`. Mention what already exists and surface up to 2 gaps.
2. Conduct the elicitation dialogue per [references/planning-dialogue.md](references/planning-dialogue.md). Ask only what's unclear; infer the rest.
3. Write each confirmed entity immediately via `add-goal`/`add-routine`/`add-task`. Don't batch.
4. Close with the most relevant gap, or nothing. Don't recap what the user just said.

### today

1. Call the Google Calendar MCP `list_events` for today (start `00:00` to tomorrow `00:00` in the user's system TZ). Capture the full event list.
2. Pipe raw events through `python3 -m scripts.calendar_sync normalize --events -` to get chronos-normalized shape.
3. Run `python3 -m scripts.schedule_day --events - --date <today>` with the normalized events on stdin.
4. Present the proposal — meetings first, routines, tasks, buffers. If `overcommitted: true`, **say so explicitly** and name the `defer_candidate_id`. Don't silently drop work.
5. On user accept, for each non-buffer block call `python3 -m scripts.plan_store set-block --id <id> --status accepted` and proceed to the push step from `sync`.

### sync

1. Pull: `list_events` → `calendar_sync normalize` → capture normalized events.
2. Reconcile: `python3 -m scripts.calendar_sync reconcile --events -` → get an action list.
3. For each action, execute against the Google Calendar MCP:
   - `create` → `create_event` with the contract from [references/sync-contract.md](references/sync-contract.md) (tag with `[chronos]` prefix + `extendedProperties.private.chronos_task_id/chronos_block_id`). Capture the returned `id`.
   - `update` / `pull_time_change` → `update_event`.
   - `missing_remote` → no MCP call needed; apply will mark local block as proposed.
   - `orphan` → DON'T auto-resolve. Present to user with three options: re-link, adopt, delete. Re-link/adopt emit an `update_event`; delete emits `delete_event`.
   - `mark_conflicted` → local-only; no MCP call.
4. Apply: feed a results array (one per action with `google_event_id` where applicable) to `python3 -m scripts.calendar_sync apply --results -`.
5. Report to user: counts per action type + any orphan choices they need to make.

### next

1. Run `python3 -m scripts.next_action`. For freshest signal, pull calendar first and pipe normalized events via `--events -`; otherwise rely on cached plan blocks.
2. Read the `kind` field:
   - `active` — state the title + minutes remaining; no other commentary.
   - `gap` — name the task, the `minutes_available`, and the one-sentence `why`.
   - `ahead` — state the suggestion verbatim and offer to pick a different task if the user disagrees.
3. Never stuff a full day view into the response. The user asked "what next", not "what's the whole day".

## Timezone rules

Every time-bearing field carries `tz` in one of three modes: `floating`, IANA name (e.g. `America/Los_Angeles`), or `UTC`. See [references/data-model.md](references/data-model.md) for the model and [references/sync-contract.md](references/sync-contract.md) for push behavior (floating resolves to current system TZ at push time). When the user mentions a specific TZ ("9am PT", "Tokyo time"), pass that IANA name through to the script; otherwise use defaults (`floating` for routines, current system TZ for one-off blocks).

## Style

- Don't announce which script you're running. Announce the outcome.
- For every mode, the user's last message is the authority. Don't invent tasks or events; only act on what's in the plan or the calendar.
- When the user confirms or accepts something, persist immediately. A dropped conversation should never cost work.
- Never echo the full plan back. Reference specific ids, not walls of JSON.

## References

- [references/data-model.md](references/data-model.md) — schema for `~/.chronos/plan.json`, migrations, timezone modes.
- [references/planning-dialogue.md](references/planning-dialogue.md) — how to run the plan conversation.
- [references/scheduling-heuristics.md](references/scheduling-heuristics.md) — prioritization and buffer rules.
- [references/sync-contract.md](references/sync-contract.md) — Google Calendar tagging, reconciliation, and push payload shape.
