---
description: Start a feature. Asks clarifying questions, writes requirement.md, spec.md and CLAUDE.md, runs spec-reviewer, then stops for your approval.
argument-hint: [--from <file> ...] <feature description>
---

# /specforge:spec

Feature description from the user: $ARGUMENTS

**Importing files.** If the arguments contain `--from <file>`, once or more, those files are the source: for example a SPEC+ document and the SPEC Lite it was derived from. Everything else in the arguments is extra description, and it may be empty. Read the files before asking questions.

You are starting the **spec** phase of a specforge workflow. Work from the project root. Do not write any code in this phase.

Read files with the Read tool, not `cat` or other shell commands, and use only the shell commands this file names (`specforge`, git, and `specforge test` to run the tests, which runs what the commit gate runs, not `npm test`, `node --test`, `pytest` or another way to run them). Run each shell command on its own, not chained with `;`, `&&` or a pipe and not followed by `echo`; the exit status is reported anyway. Headless runs allow exactly those commands, so anything else is refused.

## Steps

1. If the feature description above is empty and there is no `--from`, ask the user what they want to build and wait.

2. Check for existing state: run `specforge state get`.
   - If it reports no state, run `specforge state init`.
   - If state exists and the spec is already approved, tell the user that editing `.spec/spec.md` will bump the spec version and reset both approvals, and ask whether to continue. Stop if they say no.
   - If state is corrupt, report the error and stop. Do not repair it yourself.

3. Ask up to 5 clarifying questions, and only ones whose answers change the spec. Put them in one message. Skip this if the description is already unambiguous. Wait for the answers.

4. Read `.spec/config.json` if it exists. Source fidelity is on unless it sets `"source_fidelity": false`.

   Save the source first (skip this step if `source_fidelity` is false). Write `.spec/source.md` with the feature description above **verbatim**, exactly as the user gave it, followed by a `## Answers` section holding their answers to your questions, also verbatim. Do not summarize, reword, reformat or fix it. If `.spec/source.md` already exists from an earlier run, append the new description and answers under a dated heading instead of overwriting it. With `--from`, do not type the files out: run `specforge import <file> [<file> ...]`, which copies them unchanged under headings naming them. If it reports an error, stop and show it. Then add any extra description and the `## Answers` section yourself. (If `source_fidelity` is false, nothing is imported; read the files directly.)

5. Load the `spec-writing` skill, then write these files from its templates:
   - `.spec/requirement.md` from `${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/templates/requirement.md`
   - `.spec/spec.md` from `${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/templates/spec.md`. Every acceptance criterion gets a stable ID (AC-1, AC-2, ...) and is observable and testable. Fill Goal, Scope, Out of Scope, Acceptance Criteria and Edge Cases. Number edge cases `EC-1`, `EC-2`, …, one claim per bullet (split "exactly 2000 is accepted, and an emoji counts as 2" into two), each citing the ACs that cover it, or "review only" for a manual check. If the source is a SPEC+ document (S, P, E, C and I sections), follow the mapping in the skill's **Importing SPEC+** section. Unless `source_fidelity` is false, copy every quoted string (messages, labels, placeholders) and every number or limit from `.spec/source.md` exactly; do not paraphrase them. Also carry every constraint from the source (stack, dependencies, platform, storage, anything it says must or must not be used) into Scope or Out of Scope, even when `requirement.md` lists it too.
   - `.spec/holdout.md` from `${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/templates/holdout.md`, only if the source tags checks `[hidden]`. Their ACs go here as `HAC-1`, `HAC-2`, … tagged `[Cn]`, and never in `spec.md`: the build must not see them. Keep their exact text out of `spec.md`, `plan.md` and the root `CLAUDE.md`.
   - `CLAUDE.md` in the project root from `${CLAUDE_PLUGIN_ROOT}/skills/spec-writing/templates/root-CLAUDE.md`. Fill its Constraints section with the source's constraints and non-functional (E4) items, word for word, or write "None given." If a `CLAUDE.md` already exists, append the specforge section instead of overwriting it.

6. Run `specforge spec-size` and keep the line it prints. Then run the `specforge:spec-reviewer` subagent on `.spec/spec.md`, `.spec/requirement.md`, `.spec/holdout.md` (if it exists) and `.spec/source.md` (leave out `.spec/source.md` if `source_fidelity` is false), and give it the `specforge spec-size` line. Fix every blocking issue it reports that is a real problem, and re-run it once if you made substantial changes. Do not merge, drop or bundle ACs, or cut the feature, to get under the AC budget: that is the user's call, so pass the size suggestion on to them. If you changed the ACs, run `specforge spec-size` again.

7. **Stop.** Summarize the spec (goal, the latest `specforge spec-size` line word for word, open questions, and the reviewer's size suggestion if it made one) and tell the user to review `.spec/spec.md`, and `.spec/holdout.md` if it exists: `/specforge:approve spec` approves both. If there are hidden ACs, say that the build will have to run in a fresh session. Tell them that when they are happy, they should run `/specforge:approve spec`, then `/specforge:plan`. Do not start planning and do not approve anything yourself.
