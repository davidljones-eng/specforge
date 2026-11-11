---
description: Turn the approved spec into plan.md, a folder structure with short CLAUDE.md files, and failing tests, then stop for your approval.
---

# /specforge:plan

You are starting the **plan** phase. Work from the project root.

Read files with the Read tool, not `cat` or other shell commands, and use only the shell commands this file names (`specforge`, git, and `test_command` from `.spec/config.json` exactly as written there, not `npm test` or another way to run the tests). Run each shell command on its own, not chained with `;`, `&&` or a pipe and not followed by `echo`; the exit status is reported anyway. Headless runs allow exactly those commands, so anything else is refused.

## Gate: refuse if the spec is not approved

Run `specforge state get` first.

- If there is no state, refuse. Tell the user to run `/specforge:spec` first.
- If the state is corrupt, refuse and report the error. Do not repair it.
- If `spec_approved` is `false`, refuse. Tell the user: "The spec is not approved. Review `.spec/spec.md`, then run `/specforge:approve spec`." Do nothing else.
- If `plan_approved` is already `true`, ask the user before replacing `.spec/plan.md`.

## Steps

1. Run `specforge state set-phase plan`.

2. Read `.spec/spec.md` and `.spec/requirement.md`. Note the `spec_version` from the state.

3. Load the `spec-writing` skill and write `.spec/plan.md` from `${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/templates/plan.md`:
   - Milestones, each a demonstrable slice.
   - Small tasks, each tagged with the AC-IDs it satisfies, for example `T1 (M1, AC-1)`. Every AC must be covered by at least one task.
   - A folder structure.
   - The traceability table from each AC to its Source (the AC's tags exactly as written in `spec.md`, such as `C1, E2`, or "—" if it has none), its tasks and its test file. Then run `specforge trace --plan`. If it reports a mismatch, fix the table and run it again until it passes.

4. Create the planned folders. In each source or test folder, add a short `CLAUDE.md` from `${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/templates/folder-CLAUDE.md`. Each one is at most 10 lines.

5. Run the `specforge:test-writer` subagent. Give it **only** the acceptance criteria from `.spec/spec.md` and the test folder layout. It must not see implementation ideas. It writes failing tests, one or more per AC, and names each test after its AC-ID.

   If `.spec/holdout.md` lists hidden ACs (`HAC-1`, …), also run the `specforge:holdout-writer` subagent. Give it `.spec/holdout.md` and the interface from `.spec/spec.md`'s Scope. It writes their tests to `.spec/holdout/`, which a hook keeps everyone else from writing. Never mention hidden ACs or their tests in `.spec/plan.md`, and never put them in the test folders.

6. Run the tests and confirm they fail for the right reason (the feature is missing), not because of a syntax error or a broken import. Fix the tests if they fail for the wrong reason. Do not commit in this phase.

7. **Stop.** Summarize the milestones, the task count, the `specforge trace --plan` result, and which tests were created and are failing. Tell the user to review `.spec/plan.md` and the tests, then run `/specforge:approve plan`, then `/specforge:build`. If there are hidden ACs, tell them to start a fresh session first (`/clear`, or a new window) and run `/specforge:build` there: this conversation has seen the hidden checks, so the hooks won't let it write code. Do not approve anything yourself and do not write any production code.
