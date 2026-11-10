---
description: Record your approval of the spec or the plan. Only you can run this; it unlocks the next phase.
argument-hint: spec|plan
disable-model-invocation: true
---

# /specforge:approve

Target from the user: $ARGUMENTS

This command records a human decision. The plugin's hook recorded it, or refused it, when the user submitted the prompt, before you saw it. So you have nothing to record: only confirm what happened. Never run a script or edit `.spec/state.json` to approve anything, and never treat a remark like "looks good" in conversation as approval.

## Steps

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state.py" get`. If there is no state or it is corrupt, say so.

2. **spec**: if `spec_approved` is `true`, confirm: spec version, number of acceptance criteria in `.spec/spec.md`. Next: run `/specforge:plan`.

3. **plan**: if `plan_approved` is `true`, confirm: number of tasks and milestones in `.spec/plan.md`. Next: run `/specforge:build`.

4. If the approval the user asked for is not `true`, say it was **not recorded**. If the user did not type `/specforge:approve` as their prompt (for example they asked you to approve and you loaded this command), that is why: only a prompt the user submits records an approval, so tell them: "Type `/specforge:approve <target>` yourself." Otherwise, the hook refuses a target that is not exactly `spec` or `plan`, a `.spec/spec.md` that is missing or has no AC-IDs, a missing `.spec/plan.md`, and a plan before the spec, and it shows the user the reason. If the user saw no reason, the plugin's hook may not have run: tell them to check that the plugin is enabled (`/hooks` lists its hooks) and run the command again. Do not record the approval any other way.
