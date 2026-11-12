---
description: Turn the approved spec into plan.md, a folder structure with short CLAUDE.md files, and failing tests, then stop for your approval.
---

# /specforge:plan

You are starting the **plan** phase. Work from the project root.

Read files with the Read tool, not `cat` or other shell commands, and use only the shell commands this file names (`specforge`, git, and `specforge test` to run the tests, which runs what the commit gate runs, not `npm test`, `node --test`, `pytest` or another way to run them). Run each shell command on its own, not chained with `;`, `&&` or a pipe and not followed by `echo`; the exit status is reported anyway. Headless runs allow exactly those commands, so anything else is refused.

## Gate: refuse if the spec is not approved

Run `specforge state get` first.

- If there is no state, refuse. Tell the user to run `/specforge:spec` first.
- If the state is corrupt, refuse and report the error. Do not repair it.
- If `spec_approved` is `false`, refuse. Tell the user: "The spec is not approved. Review `.spec/spec.md`, then run `/specforge:approve spec`." Do nothing else.
- If `plan_approved` is already `true`, ask the user before replacing `.spec/plan.md`.

## Steps

1. Run `specforge state set-phase plan`.

2. Read `.spec/spec.md` and `.spec/requirement.md`. Note the `spec_version` from the state.

   Run `specforge config`. Its `code_dirs` and `test_dirs` are the folders the hooks know: they hold source code back until the plan is approved, and the build's commit gate and `specforge red` look for tests only under `test_dirs`. Plan the source under a `code_dirs` folder and the tests under a `test_dirs` folder (`src/` and `tests/` with the defaults), even where the project's tools would choose another name, such as Node's `test/`. Don't put tests next to the code (`src/a.test.js`, `src/__tests__/`): the hooks treat them as code, so they can't be written in this phase, unless their folder is in `test_dirs`. If the project already keeps its code or tests in other folders, stop and ask the user to add those folders to `code_dirs` or `test_dirs` in `.spec/config.json`, then run `/specforge:plan` again. Never edit `.spec/config.json` yourself, unless the user asks (see step 6).

3. Load the `spec-writing` skill and write `.spec/plan.md` from `${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/templates/plan.md`:
   - Milestones, each a demonstrable slice.
   - Small tasks, each tagged with the AC-IDs it satisfies, for example `T1 (M1, AC-1)`. Every AC must be covered by at least one task.
   - A folder structure, with the source and tests under the folders from `specforge config`.
   - The traceability table from each AC to its Source (the AC's tags exactly as written in `spec.md`, such as `C1, E2`, or "—" if it has none), its tasks and its test file. Then run `specforge trace --plan`. If it reports a mismatch, fix the table and run it again until it passes. It also notes any planned folder outside `code_dirs` and `test_dirs`, which the hooks don't gate: move it under them, or ask the user.

4. Create the planned folders. In each source or test folder, add a short `CLAUDE.md` from `${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/templates/folder-CLAUDE.md`. Each one is at most 10 lines.

5. Run the `specforge:test-writer` subagent. Give it **only** the acceptance criteria from `.spec/spec.md`, the test folder layout, and the `test_dirs` names: tests go only under those folders. It must not see implementation ideas. It writes failing tests, one or more per AC, and names each test after its AC-ID.

   If `.spec/holdout.md` lists hidden ACs (`HAC-1`, …), also run the `specforge:holdout-writer` subagent. Give it `.spec/holdout.md` and the interface from `.spec/spec.md`'s Scope. It writes their tests to `.spec/holdout/`, which a hook keeps everyone else from writing. Never mention hidden ACs or their tests in `.spec/plan.md`, and never put them in the test folders.

6. Run the tests with `specforge test` and confirm they fail for the right reason (the feature is missing), not because of a syntax error or a broken import. Fix the tests if they fail for the wrong reason. Then run `specforge trace --tests`. It checks that every AC has a test under a `test_dirs` folder, where the build looks for it. If it fails, move the tests it names under a `test_dirs` folder, or ask the user to add their folder to `test_dirs`, and run it again until it passes.

   Then run `specforge red --all`. With `test_filter` set in `.spec/config.json`, it runs each task's tests on their own, as the build will, and records nothing. It passes only if every task's tests ran and failed and every test file loaded. A test file that loads the code at the top fails to load until a later task's code exists, which would block the build; load the code inside each test instead, and run it again until it passes.

   If `test_filter` isn't set, `specforge red --all` says whether the build will stop at the first task: the commit gate then runs the full suite, which can't pass until later tasks are done. If it will, it suggests a filter it has checked, or says why it has none. Don't change the tests or the plan to avoid it, and don't add the filter yourself: add it to `.spec/config.json` only if the user asks, before the plan is approved, and then run `specforge red --all` again. Do not commit in this phase.

7. **Stop.** Summarize the milestones, the task count, the `specforge trace --plan`, `specforge trace --tests` and `specforge red --all` results, and which tests were created and are failing. If `specforge red --all` said the build will stop at the first task, say so, and give its suggested `"test_filter"` line exactly as printed (or why it had none). Tell the user to add it to `.spec/config.json` before approving, since once the plan is approved a change to it means approving the plan again. Then tell the user to review `.spec/plan.md` and the tests, then run `/specforge:approve plan`, then `/specforge:build`. If there are hidden ACs, tell them to start a fresh session first (`/clear`, or a new window) and run `/specforge:build` there: this conversation has seen the hidden checks, so the hooks won't let it write code. Do not approve anything yourself and do not write any production code.
