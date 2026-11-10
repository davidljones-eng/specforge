---
description: Build the approved plan on a feature branch using red, green, refactor per task, with approval pauses set by the build mode.
argument-hint: [--mode strict|checkpoint|auto]
---

# /specforge:build

Arguments from the user: $ARGUMENTS

You are starting the **build** phase. Work from the project root.

## Gate: refuse unless spec and plan are both approved

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state.py" get` first.

- No state, or corrupt state: refuse and say why. Do not repair it.
- `spec_approved` is `false`: refuse. Tell the user to review `.spec/spec.md` and run `/specforge:approve spec`.
- `plan_approved` is `false`: refuse. Tell the user to review `.spec/plan.md` and run `/specforge:approve plan`.

## Mode

If the arguments contain `--mode <value>`, the value must be `strict`, `checkpoint` or `auto`. If it is anything else, refuse and list the valid modes. Otherwise run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state.py" set-mode <value>`. With no `--mode`, use the `build_mode` already in the state (default `checkpoint`).

The mode decides when you stop and ask the user to approve before continuing:

- **strict**: after every task.
- **checkpoint** (default): after each milestone in `.spec/plan.md`.
- **auto**: only before risky actions. Risky means deleting files, adding or changing dependencies, schema or data migrations, calling external services, force pushes or history rewrites, and anything outside the approved plan.

## Steps

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state.py" set-phase build`.

2. Create a feature branch with `git checkout -b feature/<short-name>` from the spec title, unless you are already on a feature branch. Never commit to main or master.

3. Work through the tasks in `.spec/plan.md` in order. For each task, follow red, green, refactor:
   - **RED**: write the failing test for the task's AC-ID (or confirm the planned one exists), run it, and show that it fails for the right reason. Commit with a `test:` prefix. If `.spec/config.json` sets `test_filter`, then run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/red.py"`. It runs the current task's tests as committed and records RED only if they fail; until then the hooks block the task's code. If it says the tests already pass, either the tests are too weak (write a stricter failing test, commit it with `test:`, and run it again) or the behavior is already there (tick the task with no code change and say so in your summary). Never weaken or skip a test to get past it.
   - **GREEN**: write the minimum code to pass. Nothing extra. Run the **full** suite. Commit with a `feat:` prefix.
   - **REFACTOR**: clean up with the tests still green. Run the full suite. Commit with a `refactor:` prefix, only if you changed something.
   - Tick the task off in `.spec/plan.md` only **after** its `feat:` (and `refactor:`) commits, and before starting the next task.

4. Keep commits small, one concern each. A commit that is not a `test:` commit is blocked by a hook while tests fail. The hook tests exactly what is being committed, so stage every file the commit needs.

   **Per-task scope.** When the plan phase has already written tests for later tasks, the full suite keeps failing until the last task is done. If `.spec/config.json` sets `test_filter`, the commit hook runs only the tests for the ticked tasks plus the current task (the first unticked one), so each task's `feat:` commit can pass on its own. Tests for later tasks are expected to fail until their task comes up; that is not a reason to batch tasks together. If `test_filter` is not set and the full suite cannot pass yet, tell the user and suggest setting it (for example `"test_filter": "node --test --test-name-pattern {ac_regex}"` or `"pytest -q -k {pytest_k}"`) rather than committing several tasks at once.

5. Pause for approval according to the mode above, summarizing what was done and what is next.

## Rules

- Never edit or delete a test just to make it pass. If a test looks wrong, explain why and ask the user.
- If the same test fails 3 times, stop. Report what you tried and what you think is wrong. Do not keep guessing.
- If a hook blocks an edit or commit, read the reason and follow it. Do not work around a hook.
- Never edit .spec/state.json, and do not try to approve anything yourself.
- If the spec turns out to be wrong or incomplete, stop and tell the user. Changing `.spec/spec.md` resets both approvals, so the user must approve again.

When every task is done and the full suite passes, tell the user to run `/specforge:review`.
