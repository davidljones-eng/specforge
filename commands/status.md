---
description: Show the specforge phase, approvals, spec version and pending items for this project. Read-only.
---

# /specforge:status

This command is read-only. Do not modify any file, run any `set-*`, `approve`, `unapprove` or `bump` command, or change git state.

## Steps

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/version_check.py"`. If it prints a line, show that line first, word for word, whatever else happens below. It prints nothing when Claude Code is new enough.

2. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state.py" get`.
   - If it reports that no state exists, tell the user this project has no specforge workflow yet and to run `/specforge:spec <description>`. Stop.
   - If the state is corrupt, show the error and stop.

3. Show a compact summary:
   - **Phase**: spec, plan, build or review.
   - **Approvals**: spec approved yes/no, plan approved yes/no.
   - **Spec version**: the number, and whether approvals were reset by a spec change (version above 1).
   - **Build mode**: strict, checkpoint or auto.

4. List pending items, only the ones that apply:
   - Spec not approved: review `.spec/spec.md`, then `/specforge:approve spec`.
   - Plan missing: run `/specforge:plan`. Plan not approved: review `.spec/plan.md`, then `/specforge:approve plan`.
   - Unchecked tasks in `.spec/plan.md` (count them, and name the next one).
   - Acceptance criteria in `.spec/spec.md` with no matching test (search the test folders for each AC-ID).
   - `.spec/review.md` missing once all tasks are done: run `/specforge:review`.
   - If an approval is in place, mention that the user can withdraw it with `/specforge:unapprove plan` (or `spec`) without editing anything.

5. End with a single line naming the next command the user should run.
