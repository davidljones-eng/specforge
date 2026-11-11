"""M11: hidden ACs and holdout tests, kept from the build by session."""
import io
import json
import subprocess

import pytest

import approve_hook
import gate_check
import holdout
import spec_size
import state
import test_gate
import trace_check

AWARE = "aware-session"
FRESH = "fresh-session"

SOURCE = """## From `02-spec-plus.md`

| ID | Given | When | Then |
|---|---|---|---|
| C1 | a | b | c |
| C2 [hidden] | x | y | z |
"""

SPEC = "## Out of Scope\n- none\n\n## Acceptance Criteria\n- AC-1 [C1]: a\n"
HOLDOUT = "# Holdout\n\n## Hidden Acceptance Criteria\n- HAC-1 [C2]: secret behavior\n- HAC-2 [C2]: another\n"


def _git(proj, *args):
    return subprocess.run(["git", *args], cwd=proj, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def proj(tmp_path):
    """Both approved, hidden checks in place, one session that has seen them."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    state.create(tmp_path)
    state.approve(tmp_path, "spec")
    state.approve(tmp_path, "plan")
    spec = tmp_path / ".spec"
    (spec / "source.md").write_text(SOURCE)
    (spec / "spec.md").write_text(SPEC)
    (spec / "requirement.md").write_text("- R1: secret behavior\n")
    (spec / "holdout.md").write_text(HOLDOUT)
    (spec / "holdout").mkdir()
    (spec / "holdout" / "hidden.test.js").write_text('test("HAC-1: secret", () => {});\n')
    (tmp_path / "02-spec-plus.md").write_text("C2 [hidden]: secret\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text("x\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.test.js").write_text('test("AC-1: a", () => {});\n')
    _git(tmp_path, "add", "src", "tests")
    _git(tmp_path, "commit", "-qm", "init")
    state.record_session(tmp_path, AWARE)
    return tmp_path


def ev(proj, tool, session=FRESH, agent=None, **tool_input):
    event = {"tool_name": tool, "tool_input": tool_input, "cwd": str(proj), "session_id": session}
    if agent:
        event["agent_type"] = agent
    return event


def reason(proj, event):
    return gate_check.evaluate(event, proj)


# ---------- recording which sessions have seen the hidden checks (M11-AC3) ----------

@pytest.mark.parametrize("prompt", ["/specforge:spec build a thing", "/specforge:plan", "/specforge:review"])
def test_spec_plan_and_review_prompts_record_the_session(proj, monkeypatch, prompt):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    event = {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "session_id": "s-new", "cwd": str(proj)}
    assert approve_hook.main(io.StringIO(json.dumps(event))) == 0
    assert holdout.session_aware(proj, "s-new")


@pytest.mark.parametrize("prompt", ["/specforge:build", "/specforge:status", "/specforge:specx", "please /specforge:plan", "hi"])
def test_other_prompts_do_not_record_the_session(proj, monkeypatch, capsys, prompt):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    event = {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "session_id": "s-new", "cwd": str(proj)}
    approve_hook.main(io.StringIO(json.dumps(event)))
    assert not holdout.session_aware(proj, "s-new")


def test_the_first_spec_prompt_in_a_new_project_creates_the_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    event = {"hook_event_name": "UserPromptSubmit", "prompt": "/specforge:spec x", "session_id": "s1", "cwd": str(tmp_path)}
    approve_hook.main(io.StringIO(json.dumps(event)))
    assert state.read(tmp_path)["spec_approved"] is False
    assert holdout.session_aware(tmp_path, "s1")


def test_a_session_that_cannot_be_recorded_blocks_the_prompt(proj, monkeypatch, capsys):
    (proj / ".spec" / "state.json").write_text("{broken")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    event = {"hook_event_name": "UserPromptSubmit", "prompt": "/specforge:plan", "session_id": "s1", "cwd": str(proj)}
    assert approve_hook.main(io.StringIO(json.dumps(event))) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"


def test_sessions_are_kept_in_the_state_and_validated(tmp_path):
    state.create(tmp_path)
    state.record_session(tmp_path, "a")
    state.record_session(tmp_path, "a")
    state.record_session(tmp_path, "b")
    assert state.read(tmp_path)["aware_sessions"] == ["a", "b"]
    (tmp_path / ".spec" / "state.json").write_text(json.dumps({**state.DEFAULT_STATE, "aware_sessions": [""]}))
    with pytest.raises(state.StateCorruptError):
        state.read(tmp_path)


# ---------- a session that has seen them can't write code ----------

def test_an_aware_session_cannot_edit_code(proj):
    r = reason(proj, ev(proj, "Write", AWARE, file_path=str(proj / "src" / "a.js")))
    assert r and "/clear" in r and "hidden" in r


def test_an_aware_session_can_still_write_tests_and_docs(proj):
    assert reason(proj, ev(proj, "Write", AWARE, file_path=str(proj / "tests" / "b.test.js"))) is None
    assert reason(proj, ev(proj, "Write", AWARE, file_path=str(proj / ".spec" / "plan.md"))) is None


def test_an_aware_session_cannot_commit_code(proj, monkeypatch, capsys):
    (proj / ".spec" / "config.json").write_text(json.dumps({"test_command": "exit 0"}))
    (proj / "src" / "a.js").write_text("y\n")
    _git(proj, "add", "src")
    event = ev(proj, "Bash", AWARE, command="git commit -m 'feat: a'")
    assert test_gate.evaluate(event, proj) and "/clear" in test_gate.evaluate(event, proj)
    event["session_id"] = FRESH
    assert test_gate.evaluate(event, proj) is None


def test_an_aware_session_can_commit_docs(proj):
    (proj / ".spec" / "config.json").write_text(json.dumps({"test_command": "exit 0"}))
    (proj / "README.md").write_text("x\n")
    _git(proj, "add", "README.md")
    assert test_gate.evaluate(ev(proj, "Bash", AWARE, command="git commit -m 'docs: x'"), proj) is None


# ---------- a fresh session can't reach the hidden material ----------

@pytest.mark.parametrize(
    "rel",
    [".spec/holdout.md", ".spec/holdout/hidden.test.js", ".spec/source.md", ".spec/requirement.md", "02-spec-plus.md"],
)
def test_a_fresh_session_cannot_read_hidden_material(proj, rel):
    r = reason(proj, ev(proj, "Read", file_path=str(proj / rel)))
    assert r and "hidden" in r and "spec.md" in r


@pytest.mark.parametrize("rel", [".spec/spec.md", ".spec/plan.md", "src/a.js", "tests/a.test.js"])
def test_a_fresh_session_reads_everything_else(proj, rel):
    assert reason(proj, ev(proj, "Read", file_path=str(proj / rel))) is None


def test_an_aware_session_can_read_hidden_material(proj):
    assert reason(proj, ev(proj, "Read", AWARE, file_path=str(proj / ".spec" / "holdout.md"))) is None


@pytest.mark.parametrize(
    "tool_input,blocked",
    [
        ({"pattern": "secret"}, True),                                  # whole project
        ({"pattern": "secret", "path": ".spec"}, True),
        ({"pattern": "secret", "glob": "*.md"}, True),                  # source.md is a .md
        ({"pattern": "secret", "glob": "*.js"}, True),                  # holdout/hidden.test.js
        ({"pattern": "secret", "type": "py"}, False),                   # no hidden .py file
        ({"pattern": "secret", "path": "src"}, False),
        ({"pattern": "secret", "path": "tests"}, False),
        ({"pattern": "secret", "glob": "src/**/*.js"}, False),
    ],
)
def test_a_fresh_session_cannot_grep_into_hidden_material(proj, tool_input, blocked):
    r = reason(proj, ev(proj, "Grep", **tool_input))
    assert (r is not None) is blocked
    if blocked:
        assert "src" in r  # says where to search instead


@pytest.mark.parametrize(
    "tool_input,blocked",
    [
        ({"pattern": "**/*.md"}, True),
        ({"pattern": ".spec/**"}, True),
        ({"pattern": "**/*.test.js"}, True),
        ({"pattern": "*.md"}, True),                 # 02-spec-plus.md sits at the root
        ({"pattern": "src/**/*.js"}, False),
        ({"pattern": "*.json"}, False),
        ({"pattern": "**/*.js", "path": "tests"}, False),
    ],
)
def test_a_fresh_session_cannot_glob_into_hidden_material(proj, tool_input, blocked):
    assert (reason(proj, ev(proj, "Glob", **tool_input)) is not None) is blocked


@pytest.mark.parametrize("rel", [".spec/source.md", ".spec/holdout.md", "02-spec-plus.md"])
def test_a_fresh_session_cannot_edit_hidden_material(proj, rel):
    assert reason(proj, ev(proj, "Edit", file_path=str(proj / rel)))


@pytest.mark.parametrize(
    "command",
    [
        "cat .spec/holdout.md",
        "cat .spec/source.md",
        "head 02-spec-plus.md",
        "ls .spec/holdout",
        "grep -rn secret .",
        "rg secret",
        "git grep secret",
        "cat .spec/*",
    ],
)
def test_a_fresh_session_bash_is_blocked_best_effort(proj, command):
    assert reason(proj, ev(proj, "Bash", command=command))


@pytest.mark.parametrize(
    "command", ["node --test", "grep -rn x src", "rg x tests", "cat .spec/spec.md", "git status", "git add -A"]
)
def test_a_fresh_session_bash_otherwise_works(proj, command):
    assert reason(proj, ev(proj, "Bash", command=command)) is None


# ---------- only the holdout writer writes holdout tests (M11-AC2) ----------

def test_only_the_holdout_writer_writes_holdout_tests(proj):
    path = str(proj / ".spec" / "holdout" / "more.test.js")
    assert reason(proj, ev(proj, "Write", AWARE, file_path=path))
    assert reason(proj, ev(proj, "Write", AWARE, agent="specforge:test-writer", file_path=path))
    assert reason(proj, ev(proj, "Write", AWARE, agent=holdout.WRITER, file_path=path)) is None


def test_the_holdout_writer_writes_even_before_holdout_tests_exist(proj):
    import shutil
    shutil.rmtree(proj / ".spec" / "holdout")
    path = str(proj / ".spec" / "holdout" / "h.test.js")
    assert reason(proj, ev(proj, "Write", AWARE, agent=holdout.WRITER, file_path=path)) is None
    assert reason(proj, ev(proj, "Write", AWARE, file_path=path))


# ---------- counts and traces (M11-AC1) ----------

def test_spec_size_counts_hidden_acs_and_their_checks_are_covered(proj):
    m = spec_size.measure(proj)
    assert m["hidden"] == ["HAC-1", "HAC-2"]
    assert m["uncovered"] == []
    line = spec_size.summary_line(m)
    assert line.startswith("2 source checks → 1 AC + 2 hidden (budget 15).")
    assert "C2: 2" in line


def test_the_budget_counts_hidden_acs_too(proj):
    (proj / ".spec" / "config.json").write_text(json.dumps({"ac_budget": 2}))
    assert spec_size.summary_line(spec_size.measure(proj)).endswith("Over budget by 1.")


def test_trace_check_reports_hidden_acs_in_the_plan(proj):
    (proj / ".spec" / "plan.md").write_text(
        "| AC | Source | Tasks | Test file |\n|---|---|---|---|\n| AC-1 | C1 | T1 | t |\n\nT2 covers HAC-1.\n"
    )
    code, message = trace_check.check_plan(proj)
    assert code == 1 and "HAC-1" in message


# ---------- no hidden checks: nothing changes (M11-AC5) ----------

@pytest.fixture
def plain(proj):
    import shutil
    (proj / ".spec" / "holdout.md").unlink()
    shutil.rmtree(proj / ".spec" / "holdout")
    return proj


def test_without_holdouts_an_aware_session_writes_code(plain):
    assert reason(plain, ev(plain, "Write", AWARE, file_path=str(plain / "src" / "a.js"))) is None


@pytest.mark.parametrize("rel", [".spec/source.md", ".spec/requirement.md", "02-spec-plus.md"])
def test_without_holdouts_a_fresh_session_reads_the_source(plain, rel):
    assert reason(plain, ev(plain, "Read", file_path=str(plain / rel))) is None
    assert reason(plain, ev(plain, "Grep", pattern="x")) is None
    assert reason(plain, ev(plain, "Bash", command="grep -rn x .")) is None


def test_an_empty_holdout_file_counts_as_no_holdouts(plain):
    (plain / ".spec" / "holdout.md").write_text("# Holdout\n\nNone.\n")
    assert not holdout.exists(plain)
    assert reason(plain, ev(plain, "Read", file_path=str(plain / ".spec" / "source.md"))) is None


def test_a_project_without_spec_dir_is_left_alone(tmp_path):
    assert gate_check.evaluate(ev(tmp_path, "Read", file_path=str(tmp_path / "x")), tmp_path) is None
