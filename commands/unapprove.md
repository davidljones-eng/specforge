---
description: Withdraw your approval of the spec or the plan, without editing anything. Only you can run this.
argument-hint: spec|plan
disable-model-invocation: true
---

# /specforge:unapprove

Target from the user: $ARGUMENTS

This command records a human decision. The plugin's hook withdrew the approval, or refused to, when the user submitted the prompt, before you saw it. So you have nothing to record: only confirm what happened. Never run a script or edit `.spec/state.json` to change an approval.

## Steps

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state.py" get`. If there is no state or it is corrupt, say so.

2. **plan**: if `plan_approved` is `false`, confirm that the plan approval is withdrawn, the spec is still approved, and the phase is back to `plan`. Code edits and commits that change code are blocked until the user runs `/specforge:approve plan` again.

3. **spec**: if `spec_approved` is `false`, confirm that both approvals are withdrawn and the phase is back to `spec`. The spec version is unchanged. Test edits and code edits are blocked until the user runs `/specforge:approve spec` (and then `/specforge:approve plan`) again.

4. If the approval is still `true`, say it was **not recorded**. If the user did not type `/specforge:unapprove` as their prompt (for example they asked you to and you loaded this command), that is why: tell them: "Type `/specforge:unapprove <target>` yourself." Otherwise, the hook refuses a target that is not exactly `spec` or `plan`, and an approval that isn't there to withdraw, and it shows the user the reason. Do not change the approval any other way.

5. Stop. Do not continue building or planning in this turn, even work you offered earlier.
