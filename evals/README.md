# Chronos evals

Evaluation harness following https://agentskills.io/skill-creation/evaluating-skills.

## Layout

- `evals.json` — 12 test cases spanning the 4 skill modes (plan, today, sync, next) plus two timezone edge cases.
- `files/` — fixture plans and normalized event lists referenced from `evals.json`.

## Workspace

Runtime artifacts (per-iteration runs, grading, benchmarks) live in a sibling directory, `chronos-skill-workspace/`, which is gitignored.

```text
chronos-skill-workspace/
└── iteration-N/
    ├── <eval-id>/
    │   ├── with_skill/
    │   │   ├── outputs/
    │   │   ├── timing.json
    │   │   └── grading.json
    │   └── without_skill/
    │       ├── outputs/
    │       ├── timing.json
    │       └── grading.json
    ├── benchmark.json
    └── feedback.json
```

## Running evals

Each case runs twice per iteration — once with the skill loaded, once without — and the delta tells you whether Chronos is pulling its weight.

### Set up the workspace

```bash
# Sibling to the skill directory, gitignored.
mkdir -p ../chronos-skill-workspace/iteration-1
```

### Run one case

Pick a case from `evals.json`, e.g. `plan-from-scratch` (id 1). Run it as a Claude subagent for both configurations:

**With skill:**

```
Execute this task in a fresh context:
- Skill path: /path/to/chronos-skill
- CHRONOS_HOME: /tmp/chronos-eval-1-with
- Task: [paste the "prompt" field from evals.json]
- Input files: [copy the fixture listed in "files" to CHRONOS_HOME]
- For modes today/sync/next, pipe the events fixture via stdin to the relevant script.
- Save all outputs (final message, any files written under CHRONOS_HOME) to:
  ../chronos-skill-workspace/iteration-1/plan-from-scratch/with_skill/outputs/
```

**Without skill** (baseline — same prompt, no skill path):

```
Execute this task in a fresh context:
- CHRONOS_HOME: /tmp/chronos-eval-1-without
- Task: [same prompt]
- Input files: [same fixture]
- Save outputs to:
  ../chronos-skill-workspace/iteration-1/plan-from-scratch/without_skill/outputs/
```

Each run should start with a clean context — no leftover state from earlier runs or from skill development.

### Capture timing

When each subagent task finishes, the task completion notification includes `total_tokens` and `duration_ms`. Record them immediately:

```json
{
  "total_tokens": 84852,
  "duration_ms": 23332
}
```

Save to `with_skill/timing.json` and `without_skill/timing.json` alongside `outputs/`.

### Grade outputs

After both runs complete, evaluate each assertion from `evals.json` against the actual outputs. Give the outputs and assertions to an LLM and ask it to produce a `grading.json`:

```json
{
  "assertion_results": [
    { "text": "...", "passed": true, "evidence": "..." },
    { "text": "...", "passed": false, "evidence": "..." }
  ],
  "summary": { "passed": 2, "failed": 1, "total": 3, "pass_rate": 0.67 }
}
```

For programmatic assertions (valid JSON output, schema conformance, non-overlapping blocks), a verification script is more reliable than LLM judgment. The scripts in `../scripts/` can be used directly for schema checks.

### Aggregate into benchmark.json

Once every case in the iteration is graded, compute summary statistics and write `../chronos-skill-workspace/iteration-1/benchmark.json`:

```json
{
  "run_summary": {
    "with_skill":    { "pass_rate": { "mean": 0.80, "stddev": 0.08 }, "tokens": { "mean": 4200, "stddev": 500 } },
    "without_skill": { "pass_rate": { "mean": 0.40, "stddev": 0.12 }, "tokens": { "mean": 2300, "stddev": 300 } },
    "delta":         { "pass_rate": 0.40, "tokens": 1900 }
  }
}
```

## Iteration 1

No assertions yet — write them after inspecting first-round outputs, per the framework. The purpose of iteration-1 is to see what the skill actually produces on each prompt so we can spot which behaviors to pin down as assertions.

## Pass bars for v1

- `delta.pass_rate ≥ +0.35` across all cases.
- Zero programmatic-assertion failures (schema validity, non-overlap, tag presence).
- Per-case pass_rate `stddev ≤ 0.15` across 3 runs.
- Token overhead ≤ 2× baseline.
