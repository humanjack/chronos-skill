# Chronos evals

Evaluation harness following https://agentskills.io/skill-creation/evaluating-skills.

## Layout

- `evals.json` — 12 test cases spanning the 4 skill modes (plan, today, sync, next) plus two timezone edge cases.
- `files/` — fixture plans and normalized event lists referenced from `evals.json`.

## Workspace

Runtime artifacts (per-iteration runs, grading, benchmarks) live in a sibling directory, `chronos-skill-workspace/`, which is gitignored.

```
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

## Running one eval

Each case runs twice per iteration (with the chronos skill, without). The harness:

1. Copies the fixture plan into a fresh `CHRONOS_HOME` temp dir.
2. Spawns a Claude subagent with:
   - The test prompt.
   - `CHRONOS_HOME` pointing at the prepared dir.
   - Any events fixture piped via stdin for the relevant mode (`today`, `sync`, `next`).
   - For `with_skill` runs, the skill directory is loaded; for `without_skill`, it isn't.
3. Captures the subagent's final message + any files under `CHRONOS_HOME/` into `outputs/`.
4. Records `total_tokens` + `duration_ms` from the subagent completion notification into `timing.json`.

## Iteration 1

No assertions yet — write them after inspecting first-round outputs, per the framework. The purpose of iteration-1 is to see what the skill actually produces on each prompt so we can spot which behaviors to pin down as assertions.

## Pass bars for v1

- `delta.pass_rate ≥ +0.35` across all cases.
- Zero programmatic-assertion failures (schema validity, non-overlap, tag presence).
- Per-case pass_rate `stddev ≤ 0.15` across 3 runs.
- Token overhead ≤ 2× baseline.
