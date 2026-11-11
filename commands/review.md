---
description: Review the finished build with a fresh code-reviewer, write review.md mapping every AC to its code and test, then open a pull request. Never merges.
---

# /specforge:review

You are starting the **review** phase. Work from the project root.

Read files with the Read tool, not `cat` or other shell commands, and use only the shell commands this file names (`specforge`, git, and `test_command` from `.spec/config.json` exactly as written there, not `npm test` or another way to run the tests). Run each shell command on its own, not chained with `;`, `&&` or a pipe and not followed by `echo`; the exit status is reported anyway. Headless runs allow exactly those commands, so anything else is refused.

## Gate

Run `specforge state get`. If there is no state, the state is corrupt, or `spec_approved` and `plan_approved` are not both `true`, refuse and say what is missing. Then run `specforge state set-phase review`.

## Steps

1. Run the **full test** suite (use `test_command` from `.spec/config.json` if set, otherwise the project's usual runner). Keep the complete output.

2. Run a fresh `specforge:code-reviewer` subagent. It must not inherit your build context. Give it `.spec/spec.md`, `.spec/holdout.md` if it exists, `.spec/plan.md`, and the diff of the feature branch against its base branch. It reports, per acceptance criterion, the implementing code, the covering test, and any problems.

3. Check the git history for red before green. Run `git log --reverse --format=%s` on the feature branch. For each task, a `test:` commit must appear before the `feat:` commit that implements it. List every violation under Gaps.

4. Write `.spec/review.md` from `${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/templates/review.md`:
   - Cover every AC from `.spec/spec.md` in the coverage table, with the code location (`file:line`), the test (`file::name`) and a status of pass, fail or missing.
   - Gaps found: missing or weak tests, uncovered edge cases, red-before-green violations, code outside the spec, and the reviewer's findings. Write "None" only if there really are none.
   - Test results: the full output from step 1.
   - Edge Cases: the code reviewer's edge-case table, one row per `EC-` ID in the spec. Then run `specforge trace --edges`. If it reports a missing or invalid row, fix the table and run it again until it passes. List every untested edge case under Gaps too.
   - Holdout: if `.spec/holdout.md` lists hidden ACs (`HAC-1`, …), run `holdout_command` from `.spec/config.json` and write the Holdout table from its output: each hidden AC, its holdout test, and pass, fail or missing. If `holdout_command` isn't set, mark every hidden AC missing and say under Gaps that it needs setting (for example `"holdout_command": "node --test .spec/holdout/*.test.js"`). Without hidden ACs, write "No hidden ACs." Do not change code because of a holdout result: report it.
   - Source Checks: run `specforge trace --rollup` after the coverage table is written, and put its output under Source Checks as is. It rolls each source check (C1, C2, …) up from its ACs: pass, fail, missing or out of scope.

5. Commit `.spec/review.md` with a `docs:` prefix. Then open the pull request: `git push -u origin <branch>` followed by `gh pr create`. Use the review summary as the body. If any AC is failing or missing, or the suite fails, add `--draft`. If `gh` is not available or there is no remote, say so and give the user the commands to run.

6. Report to the user: the PR link, a table of ACs with their status, the Holdout results, the Source Checks table, and the gaps.

## Rules

- Never merge. Do not run `gh pr merge` or `git merge`, and do not enable auto-merge. Merging is the user's decision.
- Do not fix code in this phase. Report gaps and let the user decide whether to go back to `/specforge:build`.
- Never edit .spec/state.json.
