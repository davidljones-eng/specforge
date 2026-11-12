---
description: Build the approved plan on a feature branch using red, green, refactor per task, with approval pauses set by the build mode.
argument-hint: [--mode strict|checkpoint|auto]
---

# /specforge:build

Arguments from the user: $ARGUMENTS

You are starting the **build** phase. Work from the project root.

Read files with the Read tool, not `cat` or other shell commands, and use only the shell commands this file names (`specforge`, git, and `specforge test` to run the tests, which runs what the commit gate runs, not `npm test`, `node --test`, `pytest` or another way to run them). Run each shell command on its own, not chained with `;`, `&&` or a pipe and not followed by `echo`; the exit status is reported anyway. Headless runs allow exactly those commands, so anything else is refused.

## Gate: refuse unless spec and plan are both approved

Run `specforge state get` first.

- No state, or corrupt state: refuse and say why. Do not repair it.
- `spec_approved` is `false`: refuse. Tell the user to review `.spec/spec.md` and run `/specforge:approve spec`.
- `plan_approved` is `false`: refuse. Tell the user to review `.spec/plan.md` and run `/specforge:approve plan`.

## Mode

If the arguments contain `--mode <value>`, the value must be `strict`, `checkpoint` or `auto`. If it is anything else, refuse and list the valid modes. Otherwise run `specforge state set-mode <value>`. With no `--mode`, use the `build_mode` already in the state (default `checkpoint`).

The mode decides when you stop and ask the user to approve before continuing:

- **strict**: after every task.
- **checkpoint** (default): after each milestone in `.spec/plan.md`.
- **auto**: only before risky actions. Risky means deleting files, adding or changing dependencies, schema or data migrations, calling external services, force pushes or history rewrites, and anything outside the approved plan.

## Steps

1. Run `specforge state set-phase build`.

2. Create a feature branch with `git checkout -b feature/<short-name>` from the spec title, unless you are already on a feature branch. Never commit to main or master.

   **Continuing an earlier build.** If tasks in `.spec/plan.md` are already ticked, this run continues a build that stopped earlier: at a pause, after a failure, or cut off. Start at the first unticked task and don't redo ticked ones. Being run again is the user's go-ahead for the next milestone (or the next task, in strict mode), so don't pause before starting it.

   Run `git status`. Changes an earlier session left uncommitted are unfinished work on the first unticked task: check them against that task, run the tests, and finish and commit them through the usual steps. Never discard them: no `git checkout -- <file>`, `git restore`, `git stash` or `git reset`. If they don't belong to that task, stop and ask the user.

3. Work through the tasks in `.spec/plan.md` in order. For each task, follow red, green, refactor:
   - **RED**: write the failing test for the task's AC-ID (or confirm the planned one exists), run it with `specforge test <AC-ID> ...` (the task's AC-IDs), and show that it fails for the right reason. Commit with a `test:` prefix. If `.spec/config.json` sets `test_filter`, then run `specforge red`. It runs the current task's tests as committed and records RED only if they fail; until then the hooks block the task's code. If it says the tests already pass, either the tests are too weak (write a stricter failing test, commit it with `test:`, and run it again) or the behavior is already there (tick the task with no code change and say so in your summary). Never weaken or skip a test to get past it.
   - **GREEN**: write the minimum code to pass. Nothing extra. Run the **full** suite with `specforge test`. Commit with a `feat:` prefix.
   - **REFACTOR**: clean up with the tests still green. Run the full suite with `specforge test`. Commit with a `refactor:` prefix, only if you changed something.
   - Tick the task off in `.spec/plan.md` only **after** its `feat:` (and `refactor:`) commits, and before starting the next task.

4. Keep commits small, one concern each. A commit that is not a `test:` commit is blocked by a hook while tests fail. The hook tests exactly what is being committed, so stage every file the commit needs.

   **Per-task scope.** When the plan phase has already written tests for later tasks, the full suite keeps failing until the last task is done. If `.spec/config.json` sets `test_filter`, the commit hook runs only the tests for the ticked tasks plus the current task (the first unticked one), so each task's `feat:` commit can pass on its own. Tests for later tasks are expected to fail until their task comes up; that is not a reason to batch tasks together. If `test_filter` is not set and the full suite cannot pass yet, run `specforge red --all`: it suggests a filter it has checked for this project's runner (such as `"node --test --test-name-pattern {ac_regex} tests/*.test.js"` or `"pytest -q -k {pytest_k}"`). Tell the user and give them that line; they set it themselves and then approve the plan again (`/specforge:approve plan`). Don't commit several tasks at once instead.

5. **Pause** for approval according to the mode above. Summarize what was done and what is next, then say how to go on: reply to continue, or run `/specforge:build` again, in this session or a new one (headless: `claude -p "/specforge:build"`). A new run starts at the first unticked task. Then end your turn; don't wait or ask in any other way.

   Before you stop, for any reason (a pause, a failure, a question, or the end of the build), commit the ticks in `.spec/plan.md` that aren't committed yet, as a `chore:` commit of `.spec/plan.md` alone. The commit hook allows it: it tests exactly what is committed, which is your last `feat:` commit plus the ticks, and with `test_filter` set a commit that changes no code runs only the done tasks' tests. The ticks are how the next run knows where to start, so they belong in the branch's history next to the code.

## Rules

- Never edit or delete a test just to make it pass. If a test looks wrong, explain why and ask the user.
- If the same test fails 3 times, stop. Report what you tried and what you think is wrong. Do not keep guessing.
- If a hook blocks an edit or commit, read the reason and follow it. Do not work around a hook.
- If `specforge red` or a commit says a test file failed to load, its tests didn't run. If those tests were approved with the plan, don't stub a later task's code or rewrite the tests to get past it: stop and tell the user, who can run `/specforge:unapprove plan`, have the tests changed to load the code inside each test, and approve the plan again.
- If a hook says this session has seen the hidden checks, stop and tell the user to start a fresh session (`/clear`, or a new window) and run `/specforge:build` there. In a build session, `.spec/holdout.md`, `.spec/holdout/`, `.spec/source.md` and `.spec/requirement.md` are off limits while hidden ACs exist; work from `.spec/spec.md` and `.spec/plan.md`.
- Never edit .spec/state.json, and do not try to approve anything yourself.
- Never change `.spec/config.json`: its settings were approved with the plan, and the hooks block it. If a setting needs to change (for example to add `test_filter`), stop and ask the user.
- If the spec turns out to be wrong or incomplete, stop and tell the user. Changing `.spec/spec.md` resets both approvals, so the user must approve again.

When every task is done and the full suite passes, tell the user to run `/specforge:review`.
