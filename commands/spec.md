---
description: Start a feature. Asks clarifying questions, writes requirement.md, spec.md and CLAUDE.md, runs spec-reviewer, then stops for your approval.
argument-hint: <feature description>
---

# /specforge:spec

Feature description from the user: $ARGUMENTS

You are starting the **spec** phase of a specforge workflow. Work from the project root. Do not write any code in this phase.

## Steps

1. If the feature description above is empty, ask the user what they want to build and wait.

2. Check for existing state: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state.py" get`.
   - If it reports no state, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state.py" init`.
   - If state exists and the spec is already approved, tell the user that editing `.spec/spec.md` will bump the spec version and reset both approvals, and ask whether to continue. Stop if they say no.
   - If state is corrupt, report the error and stop. Do not repair it yourself.

3. Ask up to 5 clarifying questions, and only ones whose answers change the spec. Put them in one message. Skip this if the description is already unambiguous. Wait for the answers.

4. Read `.spec/config.json` if it exists. Source fidelity is on unless it sets `"source_fidelity": false`.

   Save the source first (skip this step if `source_fidelity` is false). Write `.spec/source.md` with the feature description above **verbatim**, exactly as the user gave it, followed by a `## Answers` section holding their answers to your questions, also verbatim. Do not summarize, reword, reformat or fix it. If `.spec/source.md` already exists from an earlier run, append the new description and answers under a dated heading instead of overwriting it.

5. Load the `spec-writing` skill, then write these files from its templates:
   - `.spec/requirement.md` from `${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/templates/requirement.md`
   - `.spec/spec.md` from `${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/templates/spec.md`. Every acceptance criterion gets a stable ID (AC-1, AC-2, ...) and is observable and testable. Fill Goal, Scope, Out of Scope, Acceptance Criteria and Edge Cases. Unless `source_fidelity` is false, copy every quoted string (messages, labels, placeholders) and every number or limit from `.spec/source.md` exactly; do not paraphrase them. Also carry every constraint from the source (stack, dependencies, platform, storage, anything it says must or must not be used) into Scope or Out of Scope, even when `requirement.md` lists it too.
   - `CLAUDE.md` in the project root from `${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/templates/root-CLAUDE.md`. If a `CLAUDE.md` already exists, append the specforge section instead of overwriting it.

6. Run the `specforge:spec-reviewer` subagent on `.spec/spec.md`, `.spec/requirement.md` and `.spec/source.md` (leave out `.spec/source.md` if `source_fidelity` is false). Fix every issue it reports that is a real problem, and re-run it once if you made substantial changes.

7. **Stop.** Summarize the spec (goal, number of acceptance criteria, open questions) and tell the user to review `.spec/spec.md`. Tell them that when they are happy, they should run `/specforge:approve spec`, then `/specforge:plan`. Do not start planning and do not approve anything yourself.
