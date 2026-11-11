import pytest

import trace_check as trace

SOURCE = """| ID | Given | When | Then |
|---|---|---|---|
| C1 | a | b | c |
| C2 | a | b | c |
| C3 | a | b | c |
| C4 | a | b | c |
"""

SPEC = """## Out of Scope
- C4 is left for a later version.

## Acceptance Criteria
- AC-1 [C1]: a
- AC-2 (C1, C2): b
- AC-3 [C2, E1]: c
- AC-4: no tags
- AC-5 [C3]: d
"""

PLAN = """## Traceability
| AC | Source | Tasks | Test file |
|----|--------|-------|-----------|
| AC-1 | C1 | T1 | tests/a.test.js |
| AC-2 | C1, C2 | T2 | tests/a.test.js |
| AC-3 | [C2, E1] | T3 | tests/b.test.js |
| AC-4 | — | T4 | tests/b.test.js |
| AC-5 | `C3` | T5 | tests/b.test.js |
"""

REVIEW = """## Acceptance Criteria Coverage
| AC | Code | Test | Status |
|----|------|------|--------|
| AC-1 | `src/a.js:1` | `tests/a.test.js::AC-1: x` | pass |
| AC-2 | `src/a.js:2` | `tests/a.test.js::AC-2: y` | pass |
| AC-3 | `src/a.js:3` | `tests/b.test.js::AC-3: z` | fail |
| AC-4 | `src/a.js:4` | `tests/b.test.js::AC-4: w` | pass |
| AC-5 | — | — | missing |
"""


def project(tmp_path, spec=SPEC, source=SOURCE, plan=PLAN, review=REVIEW):
    d = tmp_path / ".spec"
    d.mkdir()
    for name, text in (("spec.md", spec), ("source.md", source), ("plan.md", plan), ("review.md", review)):
        if text is not None:
            (d / name).write_text(text)
    return tmp_path


# ---------- the plan's Source column (M10-AC1) ----------

def test_a_matching_plan_passes(tmp_path):
    code, message = trace.check_plan(project(tmp_path))
    assert code == 0 and "5 ACs" in message


@pytest.mark.parametrize(
    "old,new,expected",
    [
        ("| AC-2 | C1, C2 |", "| AC-2 | C1 |", "AC-2"),           # a tag missing
        ("| AC-4 | — |", "| AC-4 | C3 |", "AC-4"),               # a tag the spec doesn't have
        ("| AC-5 | `C3` | T5 | tests/b.test.js |\n", "", "AC-5"),  # a row missing
    ],
)
def test_a_wrong_or_missing_row_is_reported(tmp_path, old, new, expected):
    code, message = trace.check_plan(project(tmp_path, plan=PLAN.replace(old, new)))
    assert code == 1 and expected in message


def test_a_row_for_an_ac_the_spec_lacks_is_reported(tmp_path):
    plan = PLAN + "| AC-9 | C1 | T9 | tests/c.test.js |\n"
    code, message = trace.check_plan(project(tmp_path, plan=plan))
    assert code == 1 and "AC-9" in message


def test_a_plan_without_a_source_column_is_reported(tmp_path):
    plan = "## Traceability\n| AC | Tasks | Test file |\n|---|---|---|\n| AC-1 | T1 | x |\n"
    code, message = trace.check_plan(project(tmp_path, plan=plan))
    assert code == 1 and "Source" in message


@pytest.mark.parametrize("missing", ["spec", "plan"])
def test_a_missing_file_is_reported(tmp_path, missing):
    code, message = trace.check_plan(project(tmp_path, **{missing: None}))
    assert code == 1 and f"{missing}.md" in message


# ---------- the review's roll-up (M10-AC2) ----------

def test_the_rollup_has_one_row_per_source_check(tmp_path):
    code, table = trace.rollup(project(tmp_path))
    assert code == 0
    rows = table.splitlines()[2:]  # after the header and separator rows
    assert rows == [
        "| C1 | AC-1, AC-2 | pass |",
        "| C2 | AC-2, AC-3 | fail |",
        "| C3 | AC-5 | missing |",
        "| C4 | — | out of scope |",
    ]
    assert table.splitlines()[0] == "| Check | ACs | Status |"


def test_a_check_with_no_ac_and_not_out_of_scope_is_missing(tmp_path):
    spec = SPEC.replace("- C4 is left for a later version.\n", "- Nothing.\n")
    rows = trace.rollup(project(tmp_path, spec=spec))[1]
    assert "| C4 | — | missing |" in rows


def test_an_ac_absent_from_the_review_counts_as_missing(tmp_path):
    review = REVIEW.replace("| AC-1 | `src/a.js:1` | `tests/a.test.js::AC-1: x` | pass |\n", "")
    assert "| C1 | AC-1, AC-2 | missing |" in trace.rollup(project(tmp_path, review=review))[1]


def test_status_words_are_read_loosely(tmp_path):
    review = REVIEW.replace("| fail |", "| ❌ Fail (timeout) |").replace("| missing |", "| PASS |")
    rows = trace.rollup(project(tmp_path, review=review))[1]
    assert "| C2 | AC-2, AC-3 | fail |" in rows and "| C3 | AC-5 | pass |" in rows


def test_a_pipe_inside_a_code_cell_does_not_shift_the_status(tmp_path):
    review = REVIEW.replace("`src/a.js:1`", "`a \\| b`")
    assert "| C1 | AC-1, AC-2 | pass |" in trace.rollup(project(tmp_path, review=review))[1]


def test_no_source_checks_says_so(tmp_path):
    code, message = trace.rollup(project(tmp_path, source=None))
    assert code == 0 and "No source checks" in message


def test_a_review_without_a_status_column_is_reported(tmp_path):
    review = "| AC | Code |\n|---|---|\n| AC-1 | x |\n"
    code, message = trace.rollup(project(tmp_path, review=review))
    assert code == 1 and "Status" in message


# ---------- command line ----------

def test_main_plan_and_rollup(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project(tmp_path)))
    assert trace.main(["--plan"]) == 0
    assert trace.main(["--rollup"]) == 0
    assert "| C4 | — | out of scope |" in capsys.readouterr().out


def test_main_needs_a_mode(tmp_path):
    with pytest.raises(SystemExit):
        trace.main([])


# ---------- hidden ACs in the roll-up (M11-AC4) ----------

HOLDOUT_MD = "## Hidden Acceptance Criteria\n- HAC-1 [C3]: a\n- HAC-2 [C4]: b\n"
REVIEW_WITH_HOLDOUT = REVIEW.replace("| AC-5 | — | — | missing |", "| AC-5 | `src/a.js:5` | `t::AC-5` | pass |") + """
## Holdout
| HAC | Test | Status |
|-----|------|--------|
| HAC-1 | `.spec/holdout/h.test.js::HAC-1: a` | fail |
"""


def with_holdout(tmp_path, review=REVIEW_WITH_HOLDOUT):
    proj = project(tmp_path, review=review)
    (proj / ".spec" / "holdout.md").write_text(HOLDOUT_MD)
    return proj


def test_hidden_acs_roll_up_with_their_checks(tmp_path):
    rows = trace.rollup(with_holdout(tmp_path))[1].splitlines()[2:]
    assert "| C3 | AC-5, HAC-1 | fail |" in rows       # the hidden test failed
    assert "| C4 | HAC-2 | missing |" in rows           # no row in the Holdout table


def test_a_hidden_ac_that_passes_counts(tmp_path):
    review = REVIEW_WITH_HOLDOUT.replace("| fail |\n", "| pass |\n")
    assert "| C3 | AC-5, HAC-1 | pass |" in trace.rollup(with_holdout(tmp_path, review))[1]


def test_without_a_holdout_table_hidden_acs_are_missing(tmp_path):
    review = REVIEW.replace("| AC-5 | — | — | missing |", "| AC-5 | x | y | pass |")
    assert "| C3 | AC-5, HAC-1 | missing |" in trace.rollup(with_holdout(tmp_path, review))[1]


# ---------- edge cases (M12) ----------

EDGE_SPEC = SPEC + """
## Edge Cases
- EC-1: an empty message is ignored (AC-1).
- **EC-2**: an emoji counts as 2 characters (AC-2).
- EC-3: the page shows text, never HTML (review only).
"""

EDGE_REVIEW = REVIEW + """
## Edge Cases
| EC | Edge case | Test | Status |
|----|-----------|------|--------|
| EC-1 | empty message ignored | `tests/a.test.js::AC-1: empty` | tested |
| EC-2 | emoji counts as 2 | — | untested |
| EC-3 | text, never HTML | — | review only |
"""


def test_every_edge_case_with_a_status_passes(tmp_path):
    code, message = trace.check_edges(project(tmp_path, spec=EDGE_SPEC, review=EDGE_REVIEW))
    assert code == 0 and "3 edge cases" in message and "1 untested" in message


@pytest.mark.parametrize(
    "old,new,expected",
    [
        ("| EC-2 | emoji counts as 2 | — | untested |\n", "", "EC-2"),           # a row missing
        ("| review only |", "| looks fine |", "EC-3"),                          # not a status
        ("| EC-1 | empty", "| EC-9 | empty", "EC-9"),                            # a row for no edge case
    ],
)
def test_a_missing_or_bad_row_is_reported(tmp_path, old, new, expected):
    code, message = trace.check_edges(project(tmp_path, spec=EDGE_SPEC, review=EDGE_REVIEW.replace(old, new)))
    assert code == 1 and expected in message


def test_a_review_without_an_edge_case_table_is_reported(tmp_path):
    code, message = trace.check_edges(project(tmp_path, spec=EDGE_SPEC))
    assert code == 1 and "Edge Cases" in message


def test_a_spec_without_ec_ids_has_nothing_to_check(tmp_path):
    code, message = trace.check_edges(project(tmp_path, review=EDGE_REVIEW))
    assert code == 0 and "no EC-IDs" in message


def test_main_edges(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project(tmp_path, spec=EDGE_SPEC, review=EDGE_REVIEW)))
    assert trace.main(["--edges"]) == 0


# ---------- the tests sit where the hooks look (M15-AC2) ----------

TWO_ACS = "## Acceptance Criteria\n- AC-1: a\n- AC-2: b\n"


def make_project(tmp_path, files, config=None):
    p = project(tmp_path, spec=TWO_ACS, source=None, plan=None, review=None)
    if config is not None:
        (p / ".spec" / "config.json").write_text(config)
    for rel, text in files.items():
        (p / rel).parent.mkdir(parents=True, exist_ok=True)
        (p / rel).write_text(text)
    return p


BOTH = 'test("AC-1: a", () => {});\ntest("AC-2: b", () => {});\n'


def test_every_ac_with_a_test_in_the_test_folders_passes(tmp_path):
    code, message = trace.check_tests(make_project(tmp_path, {"tests/a.test.js": BOTH}))
    assert code == 0 and "2 ACs" in message and "tests/" in message


def test_tests_in_another_folder_fail_and_are_named(tmp_path):
    # the M15 spike's baseline: the plan put its tests in test/
    code, message = trace.check_tests(make_project(tmp_path, {"test/strtools.test.js": BOTH}))
    assert code == 1
    assert "test/strtools.test.js" in message and "AC-1" in message and "AC-2" in message
    assert "test_dirs" in message and "config.json" in message


def test_an_ac_with_no_test_anywhere_fails(tmp_path):
    code, message = trace.check_tests(make_project(tmp_path, {"tests/a.test.js": 'test("AC-1: a", () => {});\n'}))
    assert code == 1 and "AC-2" in message and "AC-1" not in message.split("\n", 1)[1]


def test_a_copy_outside_the_test_folders_fails_even_when_one_is_inside(tmp_path):
    code, message = trace.check_tests(make_project(
        tmp_path, {"tests/a.test.js": BOTH, "src/a.test.js": 'test("AC-1: again", () => {});\n'}))
    assert code == 1 and "src/a.test.js" in message


def test_the_configured_test_folders_are_used(tmp_path):
    p = make_project(tmp_path, {"test/strtools.test.js": BOTH}, config='{"test_dirs": ["test"]}')
    code, message = trace.check_tests(p)
    assert code == 0 and "test/" in message


def test_pytest_style_names_count(tmp_path):
    code, _ = trace.check_tests(make_project(
        tmp_path, {"tests/test_x.py": "def test_ac1_a():\n    pass\n\ndef test_ac2_b():\n    pass\n"}))
    assert code == 0


def test_other_test_files_outside_the_folders_are_only_noted(tmp_path):
    p = make_project(tmp_path, {"tests/a.test.js": BOTH, "e2e/smoke.spec.ts": "it('loads', () => {})\n",
                                 "node_modules/x/y.test.js": 'test("AC-1: vendored", () => {});\n'})
    code, message = trace.check_tests(p)
    assert code == 0 and "e2e/smoke.spec.ts" in message and "node_modules" not in message


def test_hidden_tests_in_the_spec_folder_are_not_mentioned(tmp_path):
    p = make_project(tmp_path, {"tests/a.test.js": BOTH, ".spec/holdout/h.test.js": 'test("AC-1: h", () => {});\n'})
    code, message = trace.check_tests(p)
    assert code == 0 and "holdout" not in message


def test_main_tests(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(make_project(tmp_path, {"test/a.test.js": BOTH})))
    assert trace.main(["--tests"]) == 1
    assert "test/a.test.js" in capsys.readouterr().out


# ---------- folders the hooks don't gate (M15-AC3) ----------

def test_a_folder_outside_code_and_test_dirs_is_a_warning_not_a_failure(tmp_path):
    plan = PLAN + "\n## Folder Structure\n- `src/` - code\n- `tests/unit/` - tests\n- `public/` - the page\n- `index.html` - entry\n"
    code, message = trace.check_plan(project(tmp_path, plan=plan))
    assert code == 0
    assert "public/" in message and "index.html" in message and "code_dirs" in message
    assert "src/" not in message.split("public/")[0].split("5 ACs")[-1]


def test_folders_inside_code_and_test_dirs_give_no_warning(tmp_path):
    plan = PLAN + "\n## Folder Structure\n- `src/lib/` - code\n- `tests/` - tests\n"
    code, message = trace.check_plan(project(tmp_path, plan=plan))
    assert code == 0 and "code_dirs" not in message
