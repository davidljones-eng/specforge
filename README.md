# specforge

A Claude Code plugin for spec-driven development with human approval gates.

You describe a feature. Claude writes a spec with numbered acceptance criteria, you approve it, Claude plans and writes failing tests, you approve that, and only then does Claude write code. Hooks enforce the gates, so Claude cannot skip them.

## Install

Requirements: Claude Code 2.1.295 or later, `python3` and `git` on your PATH, and `gh` if you want `/specforge:review` to open pull requests.

Do the steps in order. Enter slash commands one at a time: type one, press Enter, then type the next. Pasting several together fails. Keep the path on a single line, because a path that wraps across lines is read wrongly.

1. **Add the marketplace.** Use the absolute path of your clone of this repository (for example `/Users/you/code/specforge`).

    Inside Claude Code:

    ```
    /plugin marketplace add /absolute/path/to/specforge
    ```

    Or from a shell:

    ```
    claude plugin marketplace add /absolute/path/to/specforge
    ```

2. **Install the plugin.** In a separate command:

    ```
    /plugin install specforge@specforge-marketplace
    ```

    Or from a shell:

    ```
    claude plugin install specforge@specforge-marketplace
    ```

3. **Check that it is installed.** From a shell, `claude plugin list` should show `specforge@specforge-marketplace` with `Status: enabled`.

4. **Reload.** In a session that was already open, run `/reload-plugins` (or restart Claude Code). Type `/specforge:` and you should see the seven commands: `spec`, `plan`, `build`, `review`, `status`, `approve` and `unapprove`.

To uninstall, run `claude plugin marketplace remove specforge-marketplace` in a shell. Because the marketplace is a local folder, edits you make to the plugin take effect on the next `/reload-plugins`.

## Usage step by step

Do steps 1 and 2 once per project. From step 3 on, this is the loop for every feature.

1. **Prepare a project.** Any folder under git works. For a Python example:

    ```
    mkdir my-project && cd my-project
    git init -b main
    python3 -m venv .venv && .venv/bin/pip install pytest
    printf '.venv/\n__pycache__/\n.pytest_cache/\n' > .gitignore
    git add -A && git commit -m "chore: init"
    ```

2. **Tell specforge how to run your tests.** Create `.spec/config.json`:

    ```json
    { "test_command": ".venv/bin/pytest -q" }
    ```

    You can skip this if `pytest`, `npm test` or `flutter test` already works from the project root. Specforge finds those on its own. If it cannot find a runner, commits are blocked with a message telling you to set `test_command`.

3. **Start Claude Code in the project folder** with `claude`, then run `/specforge:status`. Expect it to say there is no workflow yet.

4. **Describe the feature.** Run `/specforge:spec <what you want to build>`, for example `/specforge:spec REST API for todos: create, list, complete, delete. In memory, no auth.` Answer up to 5 questions. Expect `.spec/source.md` (your description and answers, saved verbatim), `.spec/requirement.md`, `.spec/spec.md` with numbered criteria (AC-1, AC-2, ...) and a root `CLAUDE.md`, a `spec-reviewer` verdict, and then a stop. Claude does not start planning.

5. **Review `.spec/spec.md`.** Edit it or ask Claude to change it. Changes before approval cost nothing.

6. **Approve the spec.** Run `/specforge:approve spec`. Expect `spec_approved` to become true in `.spec/state.json`. Until you do this, `/specforge:plan` refuses and edits to code folders and test folders are blocked.

7. **Plan.** Run `/specforge:plan`. Expect `.spec/plan.md` with tasks tagged by AC-ID, folders each with a short `CLAUDE.md`, and failing tests written from the acceptance criteria only. Source folders are still blocked at this point. Claude stops again.

8. **Review the plan and the tests**, then run `/specforge:approve plan`. Now the source folders are open and `/specforge:build` can run.

9. **Build.** Run `/specforge:build`, or choose how often it pauses with `/specforge:build --mode strict` (every task), `--mode checkpoint` (every milestone, the default) or `--mode auto` (only risky actions). Expect a `feature/...` branch and, per task, a `test:` commit, a `feat:` commit and, if needed, a `refactor:` commit. Answer its approval questions when it pauses. If the same test fails 3 times, it stops and reports.

10. **Know what gets blocked.** While the suite fails, any commit that is not a `test:` commit is blocked and Claude is shown the test output. Direct edits to `.spec/state.json` are always blocked. Claude will read the reason and fix the problem, so you rarely have to step in.

11. **Check progress at any time** with `/specforge:status`. It shows the phase, approvals, spec version and what is pending.

12. **Change your mind.** `/specforge:unapprove plan` withdraws the plan approval: the spec stays approved and the phase goes back to `plan`. `/specforge:unapprove spec` withdraws both. Nothing is edited and the spec version stays the same. Expect code edits, and commits that change code, to be blocked until you approve again.

13. **Review.** When the build is done, run `/specforge:review`. Expect a fresh `code-reviewer` pass and `.spec/review.md` mapping every AC to its code, its test and a status, plus gaps and the full test output. Then it pushes the branch and opens a pull request. That needs a git remote and `gh`; without them Claude gives you the commands instead. It never merges.

14. **Merge the pull request yourself** if you are happy with it.

If you change `.spec/spec.md` after approving it, the spec version goes up and both approvals reset. Review the change, run `/specforge:approve spec` again, and re-run `/specforge:plan` if the plan needs to change. To start over completely, delete the `.spec` folder.

## Workflow

```
 /specforge:spec <description>
        |  questions -> requirement.md, spec.md (AC-1, AC-2, ...), CLAUDE.md
        |  spec-reviewer checks it
        v
   [ YOU: /specforge:approve spec ]          spec_approved = true
        v
 /specforge:plan
        |  plan.md (tasks -> AC-IDs), folders + short CLAUDE.md files,
        |  test-writer creates FAILING tests from the acceptance criteria
        v
   [ YOU: /specforge:approve plan ]          plan_approved = true
        v
 /specforge:build [--mode strict|checkpoint|auto]
        |  feature branch; per task: RED (test:) -> GREEN (feat:) -> REFACTOR (refactor:)
        |  pauses by mode; stops after 3 failures of the same test
        v
 /specforge:review
        |  fresh code-reviewer, review.md (every AC -> code -> test),
        |  full test results, then a pull request (never merged)
        v
      done            /specforge:status shows where you are at any time
```

If `.spec/spec.md` changes after approval, the spec version goes up and both approvals reset. You approve again before work continues.

## Commands

| Command | What it does |
|---|---|
| `/specforge:spec [--from <file> ...] <description>` | Asks up to 5 clarifying questions, saves your description verbatim to `source.md` (with `--from`, `scripts/import_source.py` copies the files there unchanged, such as a SPEC+ document and its SPEC Lite), writes `requirement.md`, `spec.md` and `CLAUDE.md`, runs `spec-reviewer` (which checks the spec against `source.md`), then stops. |
| `/specforge:plan` | Refuses unless the spec is approved. Writes `plan.md`, folders with short `CLAUDE.md` files, and failing tests. The traceability table carries each AC's source tags (C1, …), which `scripts/trace_check.py --plan` checks against the spec. Stops. |
| `/specforge:build [--mode strict\|checkpoint\|auto]` | Refuses unless spec and plan are approved. Builds on a feature branch with red, green, refactor per task. |
| `/specforge:review` | Fresh `code-reviewer`, writes `review.md`, opens a PR. Never merges. `review.md` rolls each source check up from its ACs (pass, fail, missing or out of scope), computed by `scripts/trace_check.py --rollup`. It also marks every numbered edge case (EC-1, …) tested, untested or review only, and `trace_check.py --edges` checks none is skipped. |
| `/specforge:status` | Read-only: phase, approvals, spec version, build mode, pending items. |
| `/specforge:unapprove spec\|plan` | Withdraws your approval, without editing anything. `plan` keeps the spec approved; `spec` withdraws both. Only you can run it, through the same hook as `approve`. |
| `/specforge:approve spec\|plan` | Records your approval. Only you can run it: a hook records the approval when you submit the command, and the model has no way to record one. |

Build modes: `strict` pauses after every task, `checkpoint` (default) after each milestone, `auto` only before risky actions such as deleting files, changing dependencies, migrations, external calls or force pushes.

## Gates

State lives in `.spec/state.json` in your project:

```json
{ "phase": "spec", "spec_approved": false, "plan_approved": false,
  "spec_version": 1, "build_mode": "checkpoint" }
```

Hooks read it and block with a clear reason on stderr:

| Gate | Rule |
|---|---|
| Edits to source folders (`src`, `app`, `lib`, at any depth) | Blocked until `spec_approved` and `plan_approved` are both true. |
| Edits to test folders (`tests`, at any depth) | Blocked until `spec_approved` is true. This lets the plan phase write failing tests. |
| `CLAUDE.md` files inside those folders | Treated like tests: allowed once the spec is approved. |
| Edits to `.spec/state.json` | Always blocked. Approvals go through `/specforge:approve`. Shell commands that write to it are blocked too, on a best-effort basis. |
| Approvals | Recorded only by a `UserPromptSubmit` hook (`scripts/approve_hook.py`), which Claude Code runs only on prompts you submit. When your prompt is `/specforge:approve spec` or `plan`, the hook checks it in code: the spec must exist and have AC-IDs, and the plan must exist, with the spec already approved. Then it records the approval, or refuses the prompt and shows you why. A prompt that only mentions approving changes nothing. `state.py` has no `approve` command, and shell commands that would approve (`state.py approve`, `import state`, or a nested `claude -p "/specforge:approve …"`) are blocked. So is the model loading `/specforge:approve` through the Skill tool, which `disable-model-invocation` doesn't stop when you ask Claude to run it. |
| `git commit` | Once both approvals are in, blocked while the test suite fails. The suite runs on **exactly what the commit would contain** (staged files, plus any `git add` in the same command, plus tracked changes for `commit -a`; for `git commit <paths>`, the named tracked files as they are on disk, as git does), in a temporary copy, so unstaged or untracked files on disk cannot make a commit pass. Commits whose message starts with `test:` are let through, so failing tests (RED) can be committed. |
| Spec changes | Editing `.spec/spec.md` after approval bumps `spec_version` and resets both approvals. |
| Hidden checks (holdout) | When the source tags checks `[hidden]`, their ACs go in `.spec/holdout.md` (`HAC-1`, …), which you approve with the spec, and a `holdout-writer` subagent writes their tests to `.spec/holdout/` (no one else can write there). Hooks keep them from the build **by session**: a session that ran `/specforge:spec`, `/plan` or `/review` has seen them, so it can't write or commit code. Start a fresh one with `/clear` to build. Any other session can't Read, Grep or Glob into `holdout.md`, `holdout/`, `source.md`, `requirement.md` or the `--from` files, and gets a best-effort Bash block. With no hidden checks, none of this applies. |
| Withdrawn approvals | `/specforge:unapprove spec\|plan` withdraws an approval through the approval hook. Without both approvals the commit gate doesn't run the tests, but it still blocks any commit that changes code (with or without `test:`), so withdrawing an approval, or a spec edit that resets them mid-build, stops code from landing. Commits of tests, docs and `.spec/` go through, so the plan phase can commit failing tests. |
| Red first (with `test_filter` set) | A task's code waits until its tests have been seen failing. Edits to source folders, and commits that change code, are blocked while the current task (the first unticked one in `plan.md`) lists AC-IDs and has no RED recorded. `scripts/red.py` records RED: it runs that task's tests on a temporary copy of `HEAD` and records the task in `state.json` (`"red": {"T4": "<commit>"}`) only if they fail. It refuses when they pass, when no test ran, or when a test isn't committed. Tasks with no AC-IDs need no RED. Approving the plan clears the records. `"red_first": false` turns this off. |

Everything else is allowed, including everything under `.spec/`. A project with no `.spec/` folder is left alone. If the state or config file is unreadable, or a hook script fails, the hook blocks rather than guessing.

## Configuration

Optional `.spec/config.json`:

```json
{
  "code_dirs": ["src", "app", "lib", "backend"],
  "test_dirs": ["tests", "spec_tests"],
  "test_command": "make test",
  "commit_gate_scope": "commit",
  "test_env_links": [".venv", "node_modules"],
  "test_filter": "pytest -q -k {pytest_k}",
  "source_fidelity": true,
  "red_first": true,
  "ac_budget": 15,
  "holdout_command": "node --test .spec/holdout/*.test.js"
}
```

- `code_dirs`: folder names (not paths) that count as source code. Default `src`, `app`, `lib`.
- `test_dirs`: folder names that count as tests. Default `tests`. A path inside both, such as `app/tests/x.py`, counts as a test.
- `test_command`: how to run the suite. If unset, specforge looks for `pytest`, then `npm test`, then `flutter test`. If it finds none, or the runner is not installed, commits are blocked with a message telling you to set it.
- `commit_gate_scope`: `commit` (default) runs the commit gate on a temporary copy of exactly what is being committed. `worktree` runs it in the project folder instead, which is faster for slow suites but lets uncommitted files on disk make a commit pass.
- `test_env_links`: untracked paths the tests need, such as a virtualenv or installed packages. Each one that exists is linked into the temporary copy. Default `.venv`, `venv`, `node_modules`. Paths must stay inside the project.
- `test_filter`: optional. A command that runs only some tests, so each task can go red, then green, on its own even though the plan phase wrote tests for every task up front. When set, the commit gate runs it for the ACs of the ticked tasks in `plan.md` plus the current (first unticked) task, and runs the full `test_command` once every task is ticked. Placeholders, inserted already shell-quoted, so don't put quotes around them (a config that does is rejected): `{ac_regex}`, a regex such as `^(?:AC-1|AC-2)\b` for runners that filter by test name (`node --test --test-name-pattern {ac_regex}`, `npx jest -t {ac_regex}`), and `{pytest_k}`, an expression such as `test_ac1_ or test_ac2_` for pytest (`pytest -q -k {pytest_k}`). A test belongs to the AC its name **starts with**, so `AC-9: sends as in AC-2` is an AC-9 test and doesn't run in AC-2's scope. Jest, Vitest and Mocha match the filter against the full name, including `describe` names, so with them either keep AC tests at the top level or start the `describe` name with the AC-ID. Before running, the gate checks that every AC in scope has a test whose name starts with it (documentation files such as `tests/CLAUDE.md` don't count). That check reads the files, so it can't tell whether the runner actually ran those tests: `node --test`, Jest, Vitest and Mocha all exit 0 when the filter matches nothing. So after a filtered run passes, the gate reads the runner's summary, and blocks the commit if it shows no test ran. This works for `node --test` with its default, `spec` or `tap` reporter, Jest, Vitest and Mocha. pytest exits 5 when `-k` selects nothing, which already blocks. For other runners or reporters, only the file check applies. The current task joins the scope only when the commit changes code. A commit that changes only docs or `.spec/` files is checked against the done tasks, so the spec and plan can be committed before the current task's tests exist.
- `source_fidelity`: `true` (default) makes `/specforge:spec` save your description and answers word for word to `.spec/source.md`, copy its quoted strings and numbers into the spec exactly, carry its constraints (stack, dependencies, storage) into the spec's Scope or Out of Scope, and have `spec-reviewer` treat any change to them, or a constraint kept only in `requirement.md`, as blocking. `false` gives the original spec phase: no `source.md` and no fidelity check. Set it before running `/specforge:spec`.
- `red_first`: `true` (default) blocks a task's code until `red.py` has seen its tests fail, but only when `test_filter` is set (see [Gates](#gates)). A task's AC-IDs are the ones in the parentheses after its ID in `plan.md` (`T13 (M4, AC-16): …`). `false` keeps the per-task test scope without the red-first check.
- `ac_budget`: `15` (default). When the spec has more ACs than this, `spec-reviewer` suggests splitting the feature or merging ACs that only restate others. It's a suggestion for you, never a blocking issue, and Claude doesn't cut ACs on its own to meet it. `/specforge:spec`'s summary always shows the size, from `scripts/spec_size.py`: for example `6 source checks → 21 ACs (budget 15). C1: 4, C2: 1, … Over budget by 6.` `null` turns the suggestion off. The line also names any source check with no AC and no Out of Scope entry (`Uncovered checks: C4.`), which the reviewer treats as blocking.
- `holdout_command`: `null` (default). How `/specforge:review` runs the holdout tests in `.spec/holdout/` when the spec has hidden ACs, for example `node --test .spec/holdout/*.test.js`. Name the files: Node 24 won't take a folder. The results go in `review.md`'s Holdout table and into the Source Checks roll-up.

## Example session

A todo API, illustrative:

```
> /specforge:spec REST API for todos: create, list, complete, delete
Claude: A few questions: 1) Storage: in memory or SQLite? 2) Auth needed? ...
> In memory, no auth.
Claude: Wrote .spec/requirement.md, .spec/spec.md (6 acceptance criteria) and CLAUDE.md.
        spec-reviewer: READY FOR APPROVAL. Review .spec/spec.md, then run /specforge:approve spec.

> /specforge:plan
Claude: The spec is not approved. Review .spec/spec.md, then run /specforge:approve spec.
> /specforge:approve spec
Claude: Spec approved (version 1, 6 acceptance criteria). Next: /specforge:plan.

> /specforge:plan
Claude: Wrote .spec/plan.md: 2 milestones, 8 tasks. Created src/ and tests/ with CLAUDE.md files.
        test-writer created 9 failing tests (test_ac1_..., test_ac2_..., ...).
> /specforge:approve plan
Claude: Plan approved. Next: /specforge:build.
> /specforge:unapprove plan
Claude: Plan approval withdrawn; the spec is still approved and the phase is back to plan.
> Split T4 into two tasks.
Claude: Updated .spec/plan.md: 9 tasks. Review it, then run /specforge:approve plan.
> /specforge:approve plan
Claude: Plan approved. Next: /specforge:build.

> /specforge:build --mode checkpoint
Claude: Branch feature/todo-api. T1 RED: test fails as expected. test: commit.
        T1 GREEN: full suite passes. feat: commit. ...
        Milestone 1 complete. Approve to continue?
> yes
Claude: ... all 8 tasks done, 9 tests passing. Run /specforge:review.

> /specforge:status
Phase: build. Spec approved: yes. Plan approved: yes. Spec version: 1.
Pending: no tasks left. Next: /specforge:review.

> /specforge:review
Claude: review.md written: AC-1..AC-6 all pass, no gaps.
        PR opened: https://github.com/you/todo-api/pull/1 (not merged).
```

## Headless / CI

Each phase can run headless with `claude -p "/specforge:<phase>"`. The commands call the plugin's scripts through one entry point, `specforge`, which Claude Code puts on the PATH from the plugin's `bin/` folder. So a single exact rule, `Bash(specforge:*)`, allows them, with no general shell access. `specforge` has no way to approve or unapprove; those stay with the person who runs `/specforge:approve`.

Run each phase with `--permission-mode dontAsk`, so anything not listed is refused instead of prompted for, and with these tools (Node example; for pytest use `"Bash(pytest:*)"` or `"Bash(.venv/bin/pytest:*)"` in place of `"Bash(node --test:*)"`):

```
BASE='Read Write Edit Glob Grep Agent Skill Bash(specforge:*)'
GIT='Bash(git checkout:*) Bash(git switch:*) Bash(git add:*) Bash(git commit:*) Bash(git status:*) Bash(git log:*) Bash(git diff:*) Bash(git branch:*) Bash(git rev-parse:*)'

claude -p "/specforge:spec <description>" --permission-mode dontAsk --allowedTools $BASE
claude -p "/specforge:approve spec"       --permission-mode dontAsk --allowedTools 'Read' 'Bash(specforge:*)'
claude -p "/specforge:plan"               --permission-mode dontAsk --allowedTools $BASE 'Bash(node --test:*)' 'Bash(mkdir:*)'
claude -p "/specforge:approve plan"       --permission-mode dontAsk --allowedTools 'Read' 'Bash(specforge:*)'
claude -p "/specforge:build --mode auto"  --permission-mode dontAsk --allowedTools $BASE $GIT 'Bash(node --test:*)'
claude -p "/specforge:review"             --permission-mode dontAsk --allowedTools $BASE $GIT 'Bash(node --test:*)' 'Bash(git push:*)' 'Bash(gh pr create:*)'
```

`claude -p --output-format json` lists any refused call under `permission_denials`. Whoever runs `claude -p "/specforge:approve …"` is approving, so keep those two lines for a person, or for a pipeline step a person has signed off. If the spec has hidden checks (M11), start each phase in a new session, as `claude -p` does by default, and don't resume the plan session for the build.

## Troubleshooting

- **The `/specforge:` commands do not appear.** Run `/reload-plugins`, then open `/plugin` and check the Errors tab for a load error. From a shell, `claude plugin list` should show the plugin as enabled, and `claude plugin validate /absolute/path/to/specforge` should pass.
- **`/plugin marketplace add` fails.** Enter it on its own line with the whole path on one line, and use an absolute path. Pasting several commands at once, or a path that wrapped, is the usual cause.
- **Nothing is ever blocked.** Specforge only acts in projects that have a `.spec/` folder, which `/specforge:spec` creates. Also check that `python3` is on your PATH, because every hook runs through it, and that you reloaded or restarted after installing.
- **Everything is blocked, with "state.json is corrupt" or "missing".** Run `/specforge:status` to see the error. If `.spec/state.json` was damaged, delete the `.spec` folder and start again with `/specforge:spec`.
- **Commits are blocked with "test runner not found" or "no test command found".** Set `test_command` in `.spec/config.json` (see Configuration), and make sure the runner is installed in the environment Claude runs commands in.
- **A commit is blocked and you meant to commit failing tests.** Start the commit message with `test:`.
- **`/specforge:plan` or `/specforge:build` refuses.** It is telling you which approval is missing. Review the file it names and run the `/specforge:approve` command it gives.

## Limitations

- **TDD order is mostly instructions.** `/specforge:build` and the `test-writer` agent tell Claude to go red, green, refactor, and hooks block commits while tests fail. Without `test_filter`, nothing stops Claude from writing code and its test in one go. With it, red first (see [Gates](#gates)) holds each task's code until its tests have been seen failing. `/specforge:review` checks the git history and reports any task whose `test:` commit does not come before its `feat:` commit, but it reports, it does not block.
- **Holdout compartments are by session, and the shell block is best effort.** A build session can't open the hidden material with Read, Grep or Glob, but a shell command the pattern doesn't recognize could still reach it, and hidden text you paste into a build session isn't blocked. While hidden checks exist, a build session works from `spec.md` and `plan.md` only.
- **Red first checks that tests fail, not why.** `red.py` records RED when a task's tests fail at `HEAD`, so a test that fails from a typo still counts. Code written through Bash is caught when it's committed, not when it's written. Code outside `code_dirs` isn't covered, and a task whose tests pass before its code exists can only be ticked with no code change, which the review should question.
- **A `test:` commit can contain anything.** The prefix only skips the test run. The review step is where misuse shows up.
- **The commit gate reads the command from the project folder.** A `git add` in the same command as the commit is replayed into a temporary index, and paths named in `git commit <paths>` (with `-o`/`--only`, `-i`/`--include` or neither) are matched as git would from the project folder. A `cd` or `git -C <dir>` before them is not followed, and `--pathspec-from-file` is blocked, so stage first and commit separately when it matters. The real index is never changed by the gate.
- **The Bash guards for `state.json` and approvals are best effort.** Hooks cannot reliably parse every shell command. A model with general shell access could still write the file by a route the patterns do not recognize. The plugin's own route is closed: only the approval hook records approvals, and it runs only on prompts you submit. A headless `claude -p "/specforge:approve spec"` counts as you, since you ran it.
- **The commit gate runs the tests only after both approvals.** Running them earlier would deadlock the plan phase, which commits failing tests. Before that, it only blocks commits that change code. Outside a git repository there is nothing to check, so it doesn't.
- **Hooks need Claude Code 2.1.295 or later** for `onFailure: block`. On older versions a crashing hook script does not block.
- `python3` must be on the PATH, and the test runner must be installed in the environment Claude runs commands in.

## Development

The plugin is built test first. Scripts have behavior tests, and prompts (commands, agents, skill, templates) have structure tests.

```
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/pytest -q
claude plugin validate .
```

Layout:

```
.claude-plugin/   plugin.json, marketplace.json
commands/         spec, plan, build, review, status, approve, unapprove
agents/           spec-reviewer, test-writer, code-reviewer, holdout-writer
skills/           spec-writing (SKILL.md and templates)
hooks/hooks.json  gate wiring
bin/specforge     entry point the commands call (on the PATH); runs scripts/
scripts/          state.py, config.py, gate_check.py, approve_hook.py, test_gate.py, red.py, spec_size.py, import_source.py, trace_check.py, holdout.py
tests/            structure/ and scripts/
```
