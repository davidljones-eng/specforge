---
description: Start a feature. Asks clarifying questions, writes requirement.md, spec.md and CLAUDE.md, runs spec-reviewer, then stops for your approval.
argument-hint: [--from <file> ...] <feature description>
---

# /specforge:spec

Feature description from the user: $ARGUMENTS

**Importing files.** If the arguments contain `--from <file>`, once or more, those files are the source: for example a SPEC+ document and the SPEC Lite it was derived from. Everything else in the arguments is extra description, and it may be empty. Read the files before asking questions.

You are starting the **spec** phase of a specforge workflow. Work from the project root. Do not write any code in this phase.

## Steps

1. If the feature description above is empty and there is no `--from`, ask the user what they want to build and wait.

2. Check for existing state: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state.py" get`.
   - If it reports no state, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state.py" init`.
   - If state exists and the spec is already approved, tell the user that editing `.spec/spec.md` will bump the spec version and reset both approvals, and ask whether to continue. Stop if they say no.
   - If state is corrupt, report the error and stop. Do not repair it yourself.

3. Ask up to 5 clarifying questions, and only ones whose answers change the spec. Put them in one message. Skip this if the description is already unambiguous. Wait for the answers.

4. Read `.spec/config.json` if it exists. Source fidelity is on unless it sets `"source_fidelity": false`.

   Save the source first (skip this step if `source_fidelity` is false). Write `.spec/source.md` with the feature description above **verbatim**, exactly as the user gave it, followed by a `## Answers` section holding their answers to your questions, also verbatim. Do not summarize, reword, reformat or fix it. If `.spec/source.md` already exists from an earlier run, append the new description and answers under a dated heading instead of overwriting it. With `--from`, do not type the files out: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/import_source.py" <file> [<file> ...]`, which copies them unchanged under headings naming them. If it reports an error, stop and show it. Then add any extra description and the `## Answers` section yourself. (If `source_fidelity` is false, nothing is imported; read the files directly.)

5. Load the `spec-writing` skill, then write these files from its templates:
   - `.spec/requirement.md` from `${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/templates/requirement.md`
   - `.spec/spec.md` from `${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/templates/spec.md`. Every acceptance criterion gets a stable ID (AC-1, AC-2, ...) and is observable and testable. Fill Goal, Scope, Out of Scope, Acceptance Criteria and Edge Cases. If the source is a SPEC+ document (S, P, E, C and I sections), follow the mapping in the skill's **Importing SPEC+** section. Unless `source_fidelity` is false, copy every quoted string (messages, labels, placeholders) and every number or limit from `.spec/source.md` exactly; do not paraphrase them. Also carry every constraint from the source (stack, dependencies, platform, storage, anything it says must or must not be used) into Scope or Out of Scope, even when `requirement.md` lists it too.
   - `CLAUDE.md` in the project root from `${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/templates/root-CLAUDE.md`. Fill its Constraints section with the source's constraints and non-functional (E4) items, word for word, or write "None given." If a `CLAUDE.md` already exists, append the specforge section instead of overwriting it.

6. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/spec_size.py"` and keep the line it prints. Then run the `specforge:spec-reviewer` subagent on `.spec/spec.md`, `.spec/requirement.md` and `.spec/source.md` (leave out `.spec/source.md` if `source_fidelity` is false), and give it the `spec_size.py` line. Fix every blocking issue it reports that is a real problem, and re-run it once if you made substantial changes. Do not merge, drop or bundle ACs, or cut the feature, to get under the AC budget: that is the user's call, so pass the size suggestion on to them. If you changed the ACs, run `spec_size.py` again.

7. **Stop.** Summarize the spec (goal, the latest `spec_size.py` line word for word, open questions, and the reviewer's size suggestion if it made one) and tell the user to review `.spec/spec.md`. Tell them that when they are happy, they should run `/specforge:approve spec`, then `/specforge:plan`. Do not start planning and do not approve anything yourself.
