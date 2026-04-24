# Planning dialogue

How to conduct `/chronos plan` conversations. Goal: elicit goals, routines, tasks with minimum friction. Persist via `scripts/plan_store.py`.

## Entry

User triggers planning with natural-language intent: "help me plan", "set a goal", "add a routine", "I have some tasks for this week".

1. Call `python3 scripts/plan_store.py summary` to get the current plan at a glance (counts, stale goals, overdue tasks).
2. If plan is empty, greet and start with goals. If plan exists, mention what's already there and ask what they want to change.
3. Always surface up to 2 gaps from the summary before closing (see Gap detection).

## Eliciting each entity

### Goals

Ask in this order, only if not already given:
- **Title** — short, outcome-oriented.
- **Target date** — "when do you want this done by?" Accept relative ("in 3 months") and absolute. Convert to `YYYY-MM-DD` and confirm.
- **Success criteria** — "how will you know it's done?" Push back gently if vague. Skip if user has no clear answer; flag the goal as needing criteria later.

Skip asking if the answer is already in what the user said. Don't re-ask to confirm every field.

### Routines

- **Title**.
- **Cadence** — parse from user's phrasing ("M/W/F mornings", "every weekday", "Sundays"). Store as `["mon","wed","fri"]`.
- **Preferred window** — "what time of day?" Default `tz: "floating"`. Ask for a specific TZ only if user signals one ("in PT", "in Tokyo time").
- **Duration** — "how long does it take?" Default 60 minutes if not given.
- **Link to goal?** — only ask if the routine clearly advances an existing goal.

### Tasks

- **Title**.
- **Estimate** — ask only if > a rough bucket is needed for scheduling; default 30/60/90 based on phrasing ("quick" / unspecified / "deep").
- **Priority** — infer from phrasing ("urgent" → high, otherwise medium). Confirm only when ambiguous.
- **Deadline** — parse relative dates ("by Friday"). Resolve against today. Null if not mentioned.
- **Linked goal** — auto-link if the task title references a goal; otherwise ask once if a linked goal seems likely.

## Persistence

After each entity is confirmed, write immediately via:

```bash
python3 scripts/plan_store.py add-goal --title "..." --target-date "..." --success-criteria "..."
python3 scripts/plan_store.py add-routine --title "..." --days mon,wed,fri --start 07:00 --end 08:00 --duration 60
python3 scripts/plan_store.py add-task --title "..." --estimate 90 --priority high --deadline 2026-04-30 --linked-goal-id goal-xxx
```

Each command returns JSON with the created entity's `id` on stdout and diagnostics on stderr. Don't batch — write as you go so a dropped conversation doesn't lose work.

## Gap detection

Call `python3 scripts/plan_store.py gaps` at the start and end of every planning session. It returns up to 3 items, already ranked. Examples:

- "Goal 'ship chronos' has no linked tasks added in the last 14 days — want to add one now?"
- "Gym routine's last scheduled occurrence was 2026-04-10 (more than one cadence ago) — still active?"
- "Task 'review Sam's PR' deadline was 2026-04-20 — what should I do with it?"

Raise at most 2 per session. One-sentence framing; user can skip with "not now".

## Style rules

- **Don't form-fill.** Ask the 1–2 things that are actually unclear; infer or default the rest.
- **Don't confirm defaults.** Say what you assumed; let the user correct if wrong.
- **Don't recap the plan at the end.** The user knows what they just said. Close with the most relevant gap, if any.
