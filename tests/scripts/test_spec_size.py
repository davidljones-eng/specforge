import json

import pytest

import spec_size

SOURCE = """Build it.

| ID | Given | When | Then |
|---|---|---|---|
| C1 | a | b | c |
| C2 | a | b | c |
| C3 | a | b | c |

Keep the C1-C3 check IDs traceable.
"""

SPEC = """# Spec: thing

## Scope
- Like AC-2, but see AC-9 in prose: not definitions.

## Acceptance Criteria
- AC-1 (C1): Given x, when y, then z.
- AC-2 [C1, C2]: Given x, when y, then z, as in AC-1.
- AC-3 (C2): Given x.
  - AC-3 again inside a nested bullet is not a new AC.
- **AC-4** (default state; precondition for C3): no tag, since the brackets are not a plain list.
- AC-5: no tag at all.
- AC-6 (C9): tagged with a check the source doesn't have.
"""


def project(tmp_path, spec=SPEC, source=SOURCE, config=None):
    (tmp_path / ".spec").mkdir()
    if spec is not None:
        (tmp_path / ".spec" / "spec.md").write_text(spec)
    if source is not None:
        (tmp_path / ".spec" / "source.md").write_text(source)
    if config is not None:
        (tmp_path / ".spec" / "config.json").write_text(json.dumps(config))
    return tmp_path


# ---------- counting (M8-AC1) ----------

def test_counts_the_acs_the_spec_defines(tmp_path):
    m = spec_size.measure(project(tmp_path))
    assert m["acs"] == ["AC-1", "AC-2", "AC-3", "AC-4", "AC-5", "AC-6"]


def test_counts_the_checks_the_source_names(tmp_path):
    assert spec_size.measure(project(tmp_path))["checks"] == ["C1", "C2", "C3"]


def test_counts_acs_per_check_from_their_tags(tmp_path):
    m = spec_size.measure(project(tmp_path))
    assert m["per_check"] == {"C1": 2, "C2": 2, "C3": 0, "C9": 1}
    assert m["untagged"] == ["AC-4", "AC-5"]


def test_the_line_shows_checks_acs_budget_and_breakdown(tmp_path):
    line = spec_size.summary_line(spec_size.measure(project(tmp_path)))
    assert line == (
        "3 source checks → 6 ACs (budget 15). "
        "C1: 2, C2: 2, C3: 0, C9 (not in the source): 1, no C-ID: 2. "
        "Uncovered checks: C3."
    )


@pytest.mark.parametrize("source", [None, "Just a description, no IDs.\n"])
def test_without_source_checks_the_line_shows_only_the_acs(tmp_path, source):
    line = spec_size.summary_line(spec_size.measure(project(tmp_path, source=source)))
    assert line == "6 ACs (budget 15)."


# ---------- the budget (M8-AC2, M8-AC4) ----------

def test_over_the_budget_says_by_how_much(tmp_path):
    m = spec_size.measure(project(tmp_path, config={"ac_budget": 4}))
    assert m["over"] == 2
    assert spec_size.summary_line(m).endswith("Over budget by 2.")


@pytest.mark.parametrize("budget", [6, 15])
def test_at_or_under_the_budget_says_nothing_about_it(tmp_path, budget):
    m = spec_size.measure(project(tmp_path, config={"ac_budget": budget}))
    assert m["over"] == 0 and "Over budget" not in spec_size.summary_line(m)


def test_a_null_budget_still_shows_the_counts(tmp_path):
    m = spec_size.measure(project(tmp_path, config={"ac_budget": None}))
    line = spec_size.summary_line(m)
    assert line.startswith("3 source checks → 6 ACs (no budget).")
    assert m["over"] == 0 and "Over budget" not in line


# ---------- command line ----------

def test_main_prints_the_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project(tmp_path, config={"ac_budget": 4})))
    assert spec_size.main([]) == 0
    assert capsys.readouterr().out.strip().endswith("Over budget by 2.")


def test_main_takes_a_project_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert spec_size.main(["--project-dir", str(project(tmp_path))]) == 0
    assert "6 ACs" in capsys.readouterr().out


def test_main_without_a_spec_explains(tmp_path, capsys):
    assert spec_size.main(["--project-dir", str(project(tmp_path, spec=None))]) == 1
    assert "spec.md" in capsys.readouterr().out


def test_main_with_an_invalid_config_explains(tmp_path, capsys):
    assert spec_size.main(["--project-dir", str(project(tmp_path, config={"ac_budget": 0}))]) == 1
    assert "ac_budget" in capsys.readouterr().out


# ---------- found on Track C v1's real spec ----------

def test_tags_that_mix_c_ids_with_other_ids_count_their_c_ids(tmp_path):
    # v1 tagged ACs like "[C2, E1]" and "[E4]"
    spec = "- AC-1 [C2, E1]: a\n- AC-2 [E4]: b\n- AC-3 (C1, E2): c\n"
    m = spec_size.measure(project(tmp_path, spec=spec))
    assert m["per_check"] == {"C1": 1, "C2": 1, "C3": 0}
    assert m["untagged"] == ["AC-2"]


def test_only_c_ids_in_an_id_table_are_source_checks(tmp_path):
    # v1's source mentions "`C7`" in prose, as a future gap, not as a check
    source = SOURCE + "\nA new gap would be recorded as `C7` plus a task.\n\n| ID | What |\n|---|---|\n| E1 | an artifact |\n"
    assert spec_size.measure(project(tmp_path, source=source))["checks"] == ["C1", "C2", "C3"]


# ---------- uncovered checks (M9-AC2) ----------

UNCOVERED_SPEC = """## Scope
- C3 is mentioned here, which doesn't count.

## Out of Scope
- C2 is left for later.

## Acceptance Criteria
- AC-1 [C1]: a
"""


def test_a_check_with_no_ac_and_no_out_of_scope_entry_is_uncovered(tmp_path):
    m = spec_size.measure(project(tmp_path, spec=UNCOVERED_SPEC))
    assert m["uncovered"] == ["C3"]
    line = spec_size.summary_line(m)
    assert "C2: 0 (out of scope)" in line and "C3: 0" in line
    assert "Uncovered checks: C3." in line


def test_every_check_covered_says_nothing_about_coverage(tmp_path):
    spec = SPEC + "- AC-7 [C3]: now covered.\n"
    m = spec_size.measure(project(tmp_path, spec=spec))
    assert m["uncovered"] == []
    assert "Uncovered" not in spec_size.summary_line(m)


def test_uncovered_comes_before_over_budget(tmp_path):
    line = spec_size.summary_line(spec_size.measure(project(tmp_path, config={"ac_budget": 4})))
    assert line.endswith("Uncovered checks: C3. Over budget by 2.")
