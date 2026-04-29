# chronos-skill

A Claude skill that helps you plan, schedule, and stay ahead of your work — backed by bidirectional Google Calendar sync so your plan and your calendar never drift apart.

Chronos isn't another task app. It's a thinking partner that understands your long-term direction, keeps your week realistic, and can answer *"what should I work on right now?"* with context — not just a list.

## What it does

Four modes, triggered by natural language:

| Mode | You say | Chronos does |
|---|---|---|
| **plan** | "help me plan", "set a goal", "I've got tasks this week" | Elicits goals, routines, and tasks through conversation; surfaces gaps (stale goals, overdue tasks, neglected routines) |
| **today** | "plan my day", "what should today look like" | Builds a time-blocked day honoring your calendar, placing routines, filling gaps with ranked tasks, reserving buffer, flagging overcommitment with a named defer candidate |
| **next** | "what should I do next", "I finished early" | Picks one action based on where you are: current block + minutes remaining, best-fit task for the current gap, or a goal-advancing suggestion |
| **sync** | "sync my calendar", "something's off" | Pulls Google Calendar, reconciles against the plan, round-trips edits, surfaces orphans and conflicts without auto-resolving |

## Installation

Chronos follows the [Agent Skills](https://agentskills.io) open standard and works in any compatible agent (Claude Code, VS Code Copilot, Cursor, Gemini CLI, and others).

### Claude Code

Install as a personal skill (available in all your projects):

```bash
git clone https://github.com/humanjack/chronos-skill ~/.claude/skills/chronos
```

Or install as a project skill (this project only):

```bash
git clone https://github.com/humanjack/chronos-skill .claude/skills/chronos
```

Claude Code picks up the skill automatically — no restart needed if the `.claude/skills/` directory already exists. If you just created the directory for the first time, restart Claude Code.

### VS Code (GitHub Copilot) and other agents

Skills go in `.agents/skills/` by default for most other clients:

```bash
git clone https://github.com/humanjack/chronos-skill .agents/skills/chronos
```

Check your agent's documentation if it uses a different path.

### Google Calendar sync

For the `sync` mode, a Google Calendar MCP server must be available to your agent — Chronos calls whatever MCP tools the harness exposes. The [google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp) server is a tested option. Chronos doesn't manage credentials itself.

## Quick start

Once installed, open a new session and just talk:

```text
> help me set up a plan for this quarter
> what should today look like?
> sync my calendar
> what should I do next?
```

Chronos persists your plan under `~/.chronos/` across sessions. Every invocation picks up where you left off. Override the location with `CHRONOS_HOME=/path`.

## How it's built

```
chronos-skill/
├── SKILL.md                  # skill entry + mode dispatch
├── scripts/                  # stdlib-only Python, structured JSON I/O
│   ├── plan_store.py         # CRUD + schema validation on ~/.chronos/plan.json
│   ├── schedule_day.py       # time-block proposal with buffer + overcommit check
│   ├── next_action.py        # active / gap / ahead branching
│   ├── calendar_sync.py      # normalize + reconcile + apply (pure data transform)
│   ├── _schema.py            # schema v1 validation + migration scaffold
│   └── _time.py              # three-mode tz helpers (floating / zoned / UTC)
├── references/               # loaded on demand by SKILL.md
│   ├── data-model.md
│   ├── planning-dialogue.md
│   ├── scheduling-heuristics.md
│   └── sync-contract.md
└── evals/                    # test harness per agentskills.io
    ├── evals.json            # 12 cases × 4 modes + tz edge cases
    └── files/                # fixture plans + normalized events
```

**Design choices worth knowing:**

- **Scripts are pure data transforms.** They never call MCP directly. Claude pulls calendar events, pipes them through `calendar_sync normalize`, reconciles, then executes `create_event` / `update_event` for each action the script emits. This keeps scripts deterministic and unit-testable.
- **Plan state lives outside the skill.** Source of truth is `~/.chronos/plan.json` — single JSON file, atomic writes (`tmp` + rename), mode `0600`, schema-validated on every read and write. Monthly archive under `~/.chronos/archive/YYYY-MM.json`. Override with `CHRONOS_HOME=/path`.
- **Timezone is a three-mode concern**, not just UTC. See below.

## Runtime data

`~/.chronos/plan.json` is the single source of truth. Schema v1 covers:

- `goals[]` — long-term outcomes with target_date and success_criteria.
- `routines[]` — recurring commitments with cadence + preferred_window.
- `tasks[]` — discrete work items with estimate + priority + optional deadline, optionally linked to a goal.
- `schedule_blocks[]` — proposed/accepted/synced time blocks for specific dates, each carrying a `google_event_id` once pushed.
- `preferences` — `buffer_pct`, named `energy_windows` that bias `/next` suggestions.
- `calendar_sync` — `last_pull_at`, `last_push_at`, `primary_calendar_id`.

Full schema in [references/data-model.md](references/data-model.md). `schema_version` is migrated forward on every read via the scaffold in [scripts/_schema.py](scripts/_schema.py).

## Timezone handling

Every time-bearing field carries its own `tz` in one of three modes:

| Mode | Example | Semantics | Used for |
|---|---|---|---|
| **Floating** | `"floating"` | Wall-clock, follows your current system TZ | Routines ("gym 7am wherever I am"), energy windows |
| **Zoned** | `"America/Los_Angeles"` | Wall-clock pinned to IANA zone | Distributed-team meetings, client calls |
| **UTC** | `"UTC"` | Absolute instant | System timestamps only (`*_at` fields) |

UTC is derived on read via `zoneinfo`; never denormalized and stored — avoids drift across DST transitions and round-trips with Google Calendar. Your current system TZ is read live at every invocation, so traveling to a different zone works automatically for floating items while zoned items stay anchored where they belong.

## Evaluation

Chronos follows the [agentskills.io evaluation framework](https://agentskills.io/skill-creation/evaluating-skills): each test case runs twice (with the skill loaded, without), and deltas in pass rate / time / tokens determine whether the skill is pulling its weight.

- 12 cases in [evals/evals.json](evals/evals.json) cover all four modes plus DST transitions and traveling-timezone scenarios.
- 6 fixtures in [evals/files/](evals/files/): empty / minimal / overcommitted plans, typical-day / back-to-back / orphan event sets.
- Workspace (gitignored): `chronos-skill-workspace/iteration-N/<eval-id>/{with_skill,without_skill}/{outputs,timing.json,grading.json}` + `benchmark.json` per iteration.
- Pass bars for v1: `delta.pass_rate ≥ +0.35`, zero programmatic-assertion failures, `stddev ≤ 0.15` across 3 runs, token overhead ≤ 2× baseline.

## Development

```bash
# Full unit test suite (stdlib unittest, no external deps).
python3 -m unittest discover scripts -p "test_*.py"

# CLI smoke test against a fixture plan.
python3 -m scripts.schedule_day \
  --plan evals/files/plan-overcommitted.json \
  --events evals/files/events-back-to-back.json \
  --date 2026-04-28

# Plan-store CLI (standalone).
CHRONOS_HOME=/tmp/chronos-test python3 -m scripts.plan_store summary
CHRONOS_HOME=/tmp/chronos-test python3 -m scripts.plan_store add-goal --title "Ship v2" --target-date 2027-01-01
```

Requires Python 3.9+ (for `zoneinfo`). No external dependencies.

## Status

v1 implementation complete — 35 unit tests green, CLI smoke works end-to-end. Iteration-1 of the eval loop and live Google Calendar round-trip are the next validation steps before declaring the skill ready.

## Issues and discussion

- [#1](https://github.com/humanjack/chronos-skill/issues/1) — umbrella issue with v1 feature scope.
- [#2](https://github.com/humanjack/chronos-skill/issues/2) — implementation, test, and evaluation plan, with design-decision comments on storage and timezone handling.

## License

MIT (or match the repo's existing license — update if different).
