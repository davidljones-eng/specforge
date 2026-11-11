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
