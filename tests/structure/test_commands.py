import re

import pytest

from conftest import parse_frontmatter

COMMANDS = ["spec", "plan", "build", "review", "status", "approve", "unapprove"]
PLUGIN_ROOT_REF = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+)")


def load(root, name):
    path = root / "commands" / f"{name}.md"
    assert path.is_file(), f"missing commands/{name}.md"
    meta, body = parse_frontmatter(path.read_text())
    return meta, body, body.lower()


# ---------- structure ----------

def test_commands_live_at_plugin_root(root):
    assert not (root / ".claude-plugin" / "commands").exists()


def test_exactly_the_expected_commands_exist(root):
    found = sorted(p.stem for p in (root / "commands").glob("*.md"))
    assert found == sorted(COMMANDS)


@pytest.mark.parametrize("name", COMMANDS)
def test_command_has_frontmatter_and_description(root, name):
    meta, body, _ = load(root, name)
    assert len(meta.get("description", "")) >= 20, "description required"
    assert len(body.strip()) >= 300, "command body looks empty"


@pytest.mark.parametrize("name", COMMANDS)
def test_command_scripts_and_templates_referenced_exist(root, name):
    _, body, _ = load(root, name)
    for ref in PLUGIN_ROOT_REF.findall(body):
        assert (root / ref.rstrip(".,;:)")).exists(), f"{name}.md references missing {ref}"


@pytest.mark.parametrize("name", ["plan", "build", "review", "status", "approve", "unapprove"])
def test_state_driven_commands_use_state_script(root, name):
    _, body, _ = load(root, name)
    assert "specforge state" in body


@pytest.mark.parametrize("name", ["plan", "build", "review"])
def test_phase_commands_record_their_phase(root, name):
    _, body, _ = load(root, name)
    assert f"set-phase {name}" in body


# ---------- /specforge:spec ----------

def test_spec_takes_a_description(root):
    meta, body, _ = load(root, "spec")
    assert "description" in meta["argument-hint"].lower()
    assert "$ARGUMENTS" in body


def test_spec_workflow_wording(root):
    _, body, low = load(root, "spec")
    assert "5 clarifying questions" in low or "five clarifying questions" in low
    for needle in ("requirement.md", "spec.md", "CLAUDE.md", "AC-1", "spec-reviewer"):
        assert needle in body, needle
    assert "specforge state init" in body
    assert "stop" in low and "/specforge:approve spec" in body


def test_spec_uses_the_templates(root):
    _, body, _ = load(root, "spec")
    for tpl in ("requirement.md", "spec.md", "root-CLAUDE.md"):
        assert f"skills/spec-writing/templates/{tpl}" in body



def test_spec_saves_the_description_verbatim_as_the_source(root):
    _, body, low = load(root, "spec")
    assert ".spec/source.md" in body
    assert "verbatim" in low
    # it must be saved before anything is derived from it
    assert body.index(".spec/source.md") < body.index("skills/spec-writing/templates/requirement.md")


def test_spec_reviewer_is_given_the_source(root):
    _, body, _ = load(root, "spec")
    review_step = next(l for l in body.splitlines() if "spec-reviewer" in l and "subagent" in l)
    assert ".spec/source.md" in review_step


def test_spec_can_turn_source_fidelity_off(root):
    # "source_fidelity": false gives the original spec phase: no source.md,
    # no copy-exactly rule, and the reviewer is not given a source
    _, body, _ = load(root, "spec")
    assert '"source_fidelity": false' in body
    assert body.index(".spec/config.json") < body.index("verbatim")
    save_step = next(l for l in body.splitlines() if "verbatim" in l and ".spec/source.md" in l)
    assert "source_fidelity" in save_step and "skip" in save_step.lower()
    review_step = next(l for l in body.splitlines() if "spec-reviewer" in l and "subagent" in l)
    assert "source_fidelity" in review_step

# ---------- /specforge:plan ----------

def test_plan_refuses_when_spec_not_approved(root):
    _, body, low = load(root, "plan")
    assert "refuse" in low
    assert "spec_approved" in body
    assert "/specforge:approve spec" in body


def test_plan_workflow_wording(root):
    _, body, low = load(root, "plan")
    for needle in ("plan.md", "AC-", "test-writer", "CLAUDE.md"):
        assert needle in body, needle
    assert "10 lines" in low
    assert "failing" in low
    assert "skills/spec-writing/templates/plan.md" in body
    assert "skills/spec-writing/templates/folder-CLAUDE.md" in body
    assert "stop" in low and "/specforge:approve plan" in body


# ---------- /specforge:build ----------

def test_build_accepts_mode_flag(root):
    meta, body, _ = load(root, "build")
    hint = meta["argument-hint"]
    assert "--mode" in hint
    for mode in ("strict", "checkpoint", "auto"):
        assert mode in hint
    assert "$ARGUMENTS" in body


def test_build_refuses_unless_both_approved(root):
    _, body, low = load(root, "build")
    assert "refuse" in low
    assert "spec_approved" in body and "plan_approved" in body
    assert "/specforge:approve spec" in body and "/specforge:approve plan" in body


def test_build_workflow_wording(root):
    _, body, low = load(root, "build")
    assert "branch" in low
    for step in ("red", "green", "refactor"):
        assert step in low
    for prefix in ("test:", "feat:", "refactor:"):
        assert prefix in body
    assert "set-mode" in body
    assert "3 times" in low or "3 failures" in low or "three" in low
    assert "never edit .spec/state.json" in low


def test_build_describes_all_three_modes(root):
    _, _, low = load(root, "build")
    assert "strict" in low and "every task" in low
    assert "checkpoint" in low and "milestone" in low
    assert "auto" in low and "risky" in low


# ---------- /specforge:review ----------

def test_review_wording(root):
    _, body, low = load(root, "review")
    assert "code-reviewer" in body
    assert "review.md" in body and "skills/spec-writing/templates/review.md" in body
    assert "gh pr create" in body
    assert "never merge" in low
    assert "fresh" in low


def test_review_maps_every_ac_and_reports_test_results(root):
    _, _, low = load(root, "review")
    assert "every ac" in low
    assert "full test" in low


def test_review_checks_red_before_green_in_git_history(root):
    _, body, low = load(root, "review")
    assert "git log" in body
    assert "test:" in body and "feat:" in body
    assert "before" in low


# ---------- /specforge:status ----------

def test_status_reports_everything(root):
    _, body, low = load(root, "status")
    for needle in ("phase", "approv", "spec version", "pending"):
        assert needle in low, needle
    assert "specforge state get" in body


def test_status_is_read_only(root):
    _, body, low = load(root, "status")
    assert "read-only" in low or "do not modify" in low


# ---------- /specforge:approve ----------

def test_approve_cannot_be_invoked_by_the_model(root):
    meta, _, _ = load(root, "approve")
    assert meta.get("disable-model-invocation") == "true"


def test_approve_takes_spec_or_plan(root):
    meta, body, _ = load(root, "approve")
    assert "spec|plan" in meta["argument-hint"]
    assert "$ARGUMENTS" in body
    assert "specforge state" in body and "approve" in body


def test_approve_does_not_record_the_approval_itself(root):
    # M5-AC4: the UserPromptSubmit hook records it
    import re
    _, body, low = load(root, "approve")
    assert not re.search(r"state\.py\"?\s+approve", body)
    assert "hook" in low and "never" in low


def test_approve_says_when_the_approval_was_not_recorded(root):
    _, _, low = load(root, "approve")
    assert "not recorded" in low
    # if the model loaded the command itself, the user has to type it
    assert "type" in low and "yourself" in low
    assert "specforge state get" in load(root, "approve")[1]


def test_approve_validates_target_and_prerequisites(root):
    _, _, low = load(root, "approve")
    assert "spec.md" in low and "plan.md" in low
    assert "only" in low and "spec" in low and "plan" in low
    assert "refuse" in low
    assert "next" in low  # tells the user what to run next


def test_build_explains_per_task_test_scope(root):
    _, body, low = load(root, "build")
    assert "test_filter" in body
    assert "current task" in low
    # tick the task only after its feat/refactor commits, so the gate's scope is right
    assert "after" in low and "tick" in low


def test_build_records_red_with_the_red_script(root):
    _, body, low = load(root, "build")
    assert "specforge red" in body
    red, green = body.index("**RED**"), body.index("**GREEN**")
    assert red < body.index("specforge red") < green  # part of the RED step
    assert "already pass" in low  # what to do when red.py refuses


# ---------- /specforge:unapprove (M6) ----------

def test_unapprove_cannot_be_invoked_by_the_model(root):
    meta, _, _ = load(root, "unapprove")
    assert meta.get("disable-model-invocation") == "true"
    assert "spec|plan" in meta["argument-hint"]


def test_unapprove_does_not_record_anything_itself(root):
    import re
    _, body, low = load(root, "unapprove")
    assert "$ARGUMENTS" in body
    assert not re.search(r"state\.py\"?\s+(un)?approve", body)
    assert "hook" in low and "never" in low


def test_unapprove_explains_what_each_target_resets(root):
    _, body, low = load(root, "unapprove")
    assert "plan_approved" in body and "spec_approved" in body
    assert "not recorded" in low and "yourself" in low
    assert "/specforge:approve" in body  # how to approve again


def test_status_mentions_unapprove(root):
    _, body, _ = load(root, "status")
    assert "/specforge:unapprove" in body


@pytest.mark.parametrize("name", ["approve", "unapprove"])
def test_approval_commands_end_the_turn(root, name):
    # Seen in a real session: after /specforge:approve plan, the model went on to
    # make a commit it had offered earlier. Approving isn't an instruction to work.
    _, body, _ = load(root, name)
    last = body.strip().splitlines()[-1]
    assert last.split(". ", 1)[-1].startswith("Stop.")


def test_spec_writer_carries_source_constraints_into_the_spec(root):
    # M7-AC4: the constraint goes in spec.md, not only in requirement.md
    _, body, _ = load(root, "spec")
    step = next(l for l in body.splitlines() if "templates/spec.md" in l)
    low = step.lower()
    assert "constraint" in low and "requirement.md" in low
    assert "scope" in low and "out of scope" in low


# ---------- AC budget (M8) ----------

def test_spec_measures_the_spec_before_the_review(root):
    _, body, _ = load(root, "spec")
    script = "specforge spec-size"
    assert script in body
    review = body.index("specforge:spec-reviewer")
    assert body.index("templates/spec.md") < body.index(script) < review
    step = next(l for l in body.splitlines() if "specforge:spec-reviewer" in l)
    assert "specforge spec-size" in step  # the reviewer gets the line


def test_spec_shows_the_size_line_in_its_summary(root):
    _, body, _ = load(root, "spec")
    stop = next(l for l in body.splitlines() if l.lstrip().startswith("7. **Stop.**"))
    assert "specforge spec-size" in stop and "suggestion" in stop.lower()


def test_spec_writer_does_not_cut_acs_to_meet_the_budget(root):
    # M8-AC3: merging ACs or splitting the feature is the user's call
    _, body, low = load(root, "spec")
    step = next(l for l in body.splitlines() if "specforge:spec-reviewer" in l).lower()
    assert "budget" in step and "merge" in step and "user" in step


# ---------- importing SPEC+ (M9) ----------

def test_spec_takes_from_files(root):
    meta, body, _ = load(root, "spec")
    assert "--from <file>" in meta["argument-hint"]
    assert "--from" in body


def test_spec_copies_from_files_with_the_import_script(root):
    # M9-AC1: a script copies them, not the model
    _, body, _ = load(root, "spec")
    step = next(l for l in body.splitlines() if "verbatim" in l and ".spec/source.md" in l)
    assert "specforge import" in step
    assert "source_fidelity" in step


def test_spec_points_to_the_spec_plus_mapping(root):
    # M9-AC4
    _, body, _ = load(root, "spec")
    assert "Importing SPEC+" in body


def test_spec_fills_the_root_claude_md_constraints(root):
    # M9-AC3
    _, body, _ = load(root, "spec")
    step = next(l for l in body.splitlines() if "templates/root-CLAUDE.md" in l)
    assert "Constraints" in step and "E4" in step


# ---------- source IDs (M10) ----------

def test_plan_checks_the_traceability_table_before_stopping(root):
    _, body, _ = load(root, "plan")
    script = "specforge trace --plan"
    assert script in body
    stop = body.index("**Stop.**")
    assert body.index(script) < stop
    step = next(l for l in body.splitlines() if "traceability table" in l)
    assert "Source" in step


def test_review_puts_the_rollup_in_review_md(root):
    _, body, _ = load(root, "review")
    script = "specforge trace --rollup"
    assert script in body
    step = next(l for l in body.splitlines() if "--rollup" in l)
    assert "Source Checks" in step


# ---------- holdout (M11) ----------

def test_spec_puts_hidden_checks_in_the_holdout_file(root):
    _, body, _ = load(root, "spec")
    assert "[hidden]" in body and ".spec/holdout.md" in body and "HAC-" in body
    assert "templates/holdout.md" in body


def test_plan_has_the_holdout_writer_write_hidden_tests(root):
    _, body, low = load(root, "plan")
    assert "specforge:holdout-writer" in body and ".spec/holdout/" in body
    stop = body[body.index("**Stop.**"):]
    assert "/clear" in stop  # the build needs a fresh session


def test_build_says_to_start_fresh_when_the_conversation_saw_hidden_checks(root):
    _, body, _ = load(root, "build")
    assert "/clear" in body and "hidden" in body


def test_review_runs_the_holdout_tests_before_the_rollup(root):
    # M11-AC4
    _, body, _ = load(root, "review")
    assert "holdout_command" in body and ".spec/holdout.md" in body
    step = next(l for l in body.splitlines() if "holdout_command" in l)
    assert "Holdout" in step
    assert body.index("holdout_command") < body.index("--rollup")


# ---------- edge cases (M12) ----------

def test_spec_writer_numbers_edge_cases_one_claim_each(root):
    _, body, low = load(root, "spec")
    assert "EC-1" in body and "one claim" in low


def test_review_checks_the_edge_case_table_before_committing(root):
    _, body, _ = load(root, "review")
    script = "specforge trace --edges"
    assert script in body
    assert body.index(script) < body.index("Commit `.spec/review.md`")


# ---------- one entry point (M13) ----------

@pytest.mark.parametrize("name", COMMANDS)
def test_commands_call_scripts_only_through_the_specforge_entry_point(root, name):
    _, body, _ = load(root, name)
    assert "CLAUDE_PLUGIN_ROOT}/scripts/" not in body
    assert "python3" not in body


@pytest.mark.parametrize("name", ["spec", "plan", "build", "review"])
def test_phase_commands_keep_to_the_tools_a_headless_run_allows(root, name):
    # M13: a dontAsk run refused `cat` on templates and .spec files, and `node -e`
    # chained after a commit; the model recovered each time, but needn't try
    _, body, low = load(root, name)
    assert "read tool" in low and "cat" in body
    assert "only the shell commands" in low
    # the second run's one refusal: `node --test 2>&1; echo "EXIT=$?"`
    assert "on its own" in low and "echo" in body
    # the third run's one refusal: `npm test`, when the allowed runner was test_command.
    # M18-AC2: the tests run through `specforge test`, which needs no rule of its own
    rule = next(l for l in body.splitlines() if "only the shell commands" in l)
    assert "`specforge test`" in rule
    assert "exactly as written" not in rule


# ---------- M14: pausing and continuing a build ----------

def _paragraph(body, needle):
    """The blank-line-separated block of `body` that contains `needle`."""
    block = next((b for b in body.split("\n\n") if needle in b), None)
    assert block is not None, f"no paragraph mentions {needle!r}"
    return block


def test_build_commits_its_ticks_before_it_stops(root):
    # M14-AC1: one spike run left the last tick uncommitted, believing the gate
    # would block a commit of plan.md alone; it doesn't, and a new session or a
    # fresh clone needs the ticks in git
    _, body, _ = load(root, "build")
    rule = _paragraph(body, "Before you stop").lower()
    assert "chore:" in rule and ".spec/plan.md" in rule and "tick" in rule
    for reason in ("pause", "fail", "end of the build"):
        assert reason in rule
    assert "allows" in rule  # says the hook lets it through, so it isn't avoided


def test_build_pause_says_how_to_continue(root):
    # M14-AC1: the spike's pauses said "Reply to approve" or "run /specforge:build
    # again" at random; both work, headless included
    _, body, _ = load(root, "build")
    pause = _paragraph(body, "**Pause**")
    low = pause.lower()
    assert "reply" in low and "/specforge:build` again" in pause
    assert "new session" in low or "new one" in low
    assert 'claude -p "/specforge:build"' in pause
    assert "end your turn" in low


def test_build_continues_from_the_first_unticked_task(root):
    # M14-AC2: a new session took being run again as approval only by inference
    _, body, _ = load(root, "build")
    resume = _paragraph(body, "already ticked")
    low = resume.lower()
    assert "first unticked task" in low
    assert "go-ahead" in low and "next milestone" in low
    assert "don't redo" in low or "do not redo" in low


def test_build_keeps_work_an_earlier_session_left_uncommitted(root):
    # M14-AC2: never discard it; the spike's run reviewed, tested and committed it
    _, body, _ = load(root, "build")
    rule = _paragraph(body, "uncommitted")
    low = rule.lower()
    assert "git status" in rule and "never discard" in low
    for discard in ("git checkout --", "git restore", "git stash", "git reset"):
        assert discard in rule


def test_build_has_no_resume_flag(root):
    # M14-AC2: being run again is the resume; a flag would clash with claude --resume
    meta, body, _ = load(root, "build")
    assert "--resume" not in meta["argument-hint"] and "--resume" not in body


# ---------- M15: the plan phase uses the folders the hooks know ----------

def test_plan_reads_the_configured_folders_before_writing_the_plan(root):
    # M15-AC1: one of two baseline plans put its tests in test/, which the hooks don't see
    _, body, _ = load(root, "plan")
    assert "specforge config" in body
    assert body.index("specforge config") < body.index("templates/plan.md")
    assert "test_dirs" in body and "code_dirs" in body


def test_plan_keeps_tests_out_of_code_folders(root):
    # M15-AC1: src/a.test.js counts as code, so the plan phase can't even write it
    _, body, low = load(root, "plan")
    assert "next to the code" in low and "src/a.test.js" in body


def test_plan_never_edits_the_config_itself(root):
    _, _, low = load(root, "plan")
    assert "never edit `.spec/config.json`" in low


def test_plan_gives_test_writer_the_test_folders(root):
    _, body, _ = load(root, "plan")
    step = _paragraph(body, "specforge:test-writer")
    assert "test_dirs" in step


def test_plan_checks_where_the_tests_are_before_stopping(root):
    # M15-AC2
    _, body, _ = load(root, "plan")
    assert "specforge trace --tests" in body
    assert body.index("specforge trace --tests") > body.index("specforge:test-writer")
    stop = _paragraph(body, "**Stop.**")
    assert "trace --tests" in stop


# ---------- M16: tests that load before the code exists ----------

def test_plan_runs_red_all_after_the_tests_are_written(root):
    _, body, _ = load(root, "plan")
    assert "specforge red --all" in body
    assert body.index("specforge red --all") > body.index("specforge trace --tests")
    step = _paragraph(body, "specforge red --all").lower()
    assert "test_filter" in step and "inside each test" in step
    assert "red --all" in _paragraph(body, "**Stop.**")


def test_build_says_what_to_do_when_a_test_file_cannot_load(root):
    _, body, low = load(root, "build")
    rule = _paragraph(body, "failed to load").lower()
    assert "stub" in rule and "/specforge:unapprove plan" in rule


# ---------- M18: the tests run through `specforge test` ----------

def _step(body, marker):
    """The line of `body` that starts with `marker` (a step or a RED/GREEN bullet)."""
    line = next((l for l in body.splitlines() if l.strip().startswith(marker)), None)
    assert line is not None, f"no line starts with {marker!r}"
    return line


@pytest.mark.parametrize("name", ["spec", "plan", "build", "review"])
def test_phase_commands_never_name_test_command_as_something_to_run(root, name):
    # M18-AC2: with test_command unset, 3 of 3 v0.2.20 plan runs ran no tests
    _, body, low = load(root, name)
    assert "`test_command` from `.spec/config.json`" not in body
    assert "usual runner" not in low


def test_plan_runs_its_tests_with_specforge_test(root):
    # M18-AC2
    _, body, _ = load(root, "plan")
    assert "`specforge test`" in _step(body, "6.")


@pytest.mark.parametrize("marker", ["- **RED**", "- **GREEN**", "- **REFACTOR**"])
def test_build_runs_its_tests_with_specforge_test(root, marker):
    # M18-AC2
    _, body, _ = load(root, "build")
    assert "`specforge test" in _step(body, marker)


def test_build_runs_one_tasks_tests_by_ac_id(root):
    # M18-AC1/AC2: RED runs the task's own tests
    _, body, _ = load(root, "build")
    assert "specforge test <AC-ID" in _step(body, "- **RED**")


def test_review_runs_the_full_suite_with_specforge_test(root):
    # M18-AC2
    _, body, _ = load(root, "review")
    assert "`specforge test`" in _step(body, "1.")


# ---------- M17: the build never changes config.json ----------

def test_build_never_changes_config_json_and_asks_the_user(root):
    # M17-AC1: the hooks block it; the rule says why and what to do instead
    _, body, _ = load(root, "build")
    rules = body.split("## Rules", 1)[1] if "## Rules" in body else body
    bullet = next(l for l in rules.splitlines() if "config.json" in l and l.lstrip().startswith("-"))
    assert "never" in bullet.lower() and "ask the user" in bullet.lower()


# ---------- M19: the plan says, before approval, that the build will stop ----------

def test_plan_always_runs_red_all(root):
    # M19-AC3: without test_filter it's what says the build will stop
    _, body, _ = load(root, "plan")
    assert "If `.spec/config.json` sets `test_filter`, then run `specforge red --all`" not in body
    assert "Then run `specforge red --all`." in body
    step = _paragraph(body, "If `test_filter` isn't set, `specforge red --all`")
    assert "stop at the first task" in step and "suggest" in step.lower()


def test_plan_summary_repeats_the_warning_before_approval(root):
    # M19-AC3
    _, body, _ = load(root, "plan")
    stop = _paragraph(body, "**Stop.**")
    low = stop.lower()
    assert "will stop" in low and "suggested" in low
    assert stop.index("will stop") < stop.index("/specforge:approve plan")
    assert "before" in low and "approving the plan again" in low


def test_plan_adds_the_filter_only_when_the_user_asks(root):
    # M19-AC3: config.json is the user's, and fixed once the plan is approved (M17)
    _, body, _ = load(root, "plan")
    step = _paragraph(body, "If `test_filter` isn't set, `specforge red --all`").lower()
    assert "only if the user asks" in step and "run `specforge red --all` again" in step


def test_build_points_to_red_all_for_a_checked_filter(root):
    # M19: the build-time stop gives the same checked suggestion
    _, body, _ = load(root, "build")
    assert "specforge red --all" in _paragraph(body, "**Per-task scope.**")
