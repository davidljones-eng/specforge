import json
import shlex
import sys
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import config
import red
import state
import test_gate

PLAN = """## Tasks
- [x] T1 (M1, AC-1): first
- [ ] T2 (M1, AC-2, AC-3): second, as in AC-1
- [ ] T3 (M2): setup
"""

TESTS = 'test("AC-1: a", () => {});\ntest("AC-2: b", () => {});\ntest("AC-3: c", () => {});\n'


def _git(proj, *args):
    return subprocess.run(["git", *args], cwd=proj, check=True, capture_output=True, text=True).stdout


def head(proj):
    return _git(proj, "rev-parse", "HEAD").strip()


def set_config(proj, **cfg):
    (proj / ".spec" / "config.json").write_text(json.dumps(cfg))


@pytest.fixture
def proj(tmp_path, tmp_path_factory, monkeypatch):
    """T2 (AC-2, AC-3) is current and its tests are committed at HEAD."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    state.create(tmp_path)
    state.approve(tmp_path, "spec")
    state.approve(tmp_path, "plan")
    (tmp_path / ".spec" / "plan.md").write_text(PLAN)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "all.test.js").write_text(TESTS)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text("FAIL\n")
    _git(tmp_path, "add", "tests", "src")
    _git(tmp_path, "commit", "-qm", "test: all")
    out = tmp_path_factory.mktemp("o")
    monkeypatch.setenv("OUT", str(out / "got.txt"))
    monkeypatch.setenv("WHERE", str(out / "where.txt"))
    set_config(tmp_path, test_filter="printf '%s' {ac_regex} > \"$OUT\"; pwd -P > \"$WHERE\"; exit 1")
    return tmp_path


def red_of(proj):
    return state.red_tasks(state.read(proj))


# ---------- recording RED (M4-AC2) ----------

def test_failing_tests_at_head_record_red_for_the_current_task(proj):
    code, message = red.run(proj)
    assert code == 0
    assert red_of(proj) == {"T2": head(proj)}
    assert "T2" in message and "RED" in message


def test_only_the_current_tasks_own_acs_are_run(proj):
    red.run(proj)
    got = Path(os.environ["OUT"]).read_text()
    assert "AC-2" in got and "AC-3" in got
    assert "AC-1" not in got  # done tasks, and ACs the task only mentions, are left out


def test_the_tests_run_on_head_not_on_the_files_on_disk(proj):
    set_config(proj, test_filter="grep -q FAIL src/a.js && exit 1; exit 0 #{ac_regex}")
    (proj / "src" / "a.js").write_text("PASS\n")  # uncommitted: not what HEAD has
    assert red.run(proj)[0] == 0
    assert red_of(proj) == {"T2": head(proj)}


def test_the_temporary_copy_is_removed(proj):
    red.run(proj)
    ran_in = Path(Path(os.environ["WHERE"]).read_text().strip())
    assert ran_in != proj.resolve()
    assert not ran_in.exists()


def test_red_already_recorded_is_kept(proj):
    state.record_red(proj, "T2", "abc1234")
    code, message = red.run(proj)
    assert code == 0 and "already" in message.lower()
    assert red_of(proj) == {"T2": "abc1234"}


# ---------- refusing (M4-AC3, M4-AC4) ----------

def test_tests_that_already_pass_record_nothing(proj):
    set_config(proj, test_filter="exit 0 #{ac_regex}")
    code, message = red.run(proj)
    assert code == 1 and red_of(proj) == {}
    low = message.lower()
    assert "already pass" in low and "test:" in message and "tick" in low


@pytest.mark.parametrize(
    "output,exit_code",
    [
        ("No tests found, exiting with code 1", 1),
        ("Tests:       2 skipped, 2 total", 0),
    ],
)
def test_a_run_that_ran_no_tests_records_nothing(proj, tmp_path_factory, output, exit_code):
    f = tmp_path_factory.mktemp("x") / "out.txt"
    f.write_text(output + "\n")
    set_config(proj, test_filter=f"cat {f}; exit {exit_code} #{{ac_regex}}")
    code, message = red.run(proj)
    assert code == 1 and red_of(proj) == {}
    assert "ran no tests" in message


def test_a_missing_runner_records_nothing(proj):
    set_config(proj, test_filter="no-such-runner-xyz {ac_regex}")
    code, message = red.run(proj)
    assert code == 1 and red_of(proj) == {}
    assert "not found" in message


def test_a_timeout_records_nothing(proj, monkeypatch):
    monkeypatch.setattr(test_gate, "TEST_TIMEOUT", 1)
    set_config(proj, test_filter="sleep 5 #{ac_regex}")
    code, message = red.run(proj)
    assert code == 1 and red_of(proj) == {}
    assert "timed out" in message


def test_a_test_only_on_disk_does_not_count(proj):
    (proj / "tests" / "all.test.js").write_text('test("AC-1: a", () => {});\ntest("AC-2: b", () => {});\n')
    _git(proj, "commit", "-qam", "test: drop AC-3")
    (proj / "tests" / "three.test.js").write_text('test("AC-3: c", () => {});\n')  # not committed
    code, message = red.run(proj)
    assert code == 1 and red_of(proj) == {}
    assert "AC-3" in message and "commit" in message.lower()


def test_red_needs_both_approvals(proj):
    state.update(proj, plan_approved=False)
    code, message = red.run(proj)
    assert code == 1 and "approve" in message.lower()


def test_red_needs_a_test_filter(proj):
    set_config(proj)
    code, message = red.run(proj)
    assert code == 1 and "test_filter" in message


def test_red_first_false_needs_no_red(proj):
    set_config(proj, test_filter="exit 1 #{ac_regex}", red_first=False)
    code, message = red.run(proj)
    assert code == 1 and "red_first" in message and red_of(proj) == {}


def test_red_needs_a_commit(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    state.create(tmp_path)
    state.approve(tmp_path, "spec")
    state.approve(tmp_path, "plan")
    (tmp_path / ".spec" / "plan.md").write_text(PLAN)
    set_config(tmp_path, test_filter="exit 1 #{ac_regex}")
    code, message = red.run(tmp_path)
    assert code == 1 and "commit" in message.lower()


# ---------- tasks that need no RED (M4-AC8) ----------

def test_a_current_task_without_ac_ids_needs_no_red(proj):
    (proj / ".spec" / "plan.md").write_text(PLAN.replace("[ ] T2", "[x] T2"))
    code, message = red.run(proj)
    assert code == 0 and "T3" in message and "no RED" in message
    assert red_of(proj) == {}


def test_no_open_task_needs_no_red(proj):
    (proj / ".spec" / "plan.md").write_text(PLAN.replace("[ ]", "[x]"))
    code, message = red.run(proj)
    assert code == 0 and red_of(proj) == {}


# ---------- command line ----------

def test_main_prints_the_message_and_returns_the_code(proj, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    assert red.main([]) == 0
    assert "T2" in capsys.readouterr().out
    set_config(proj, test_filter="exit 0 #{ac_regex}")
    state.update(proj, spec_approved=True)  # no-op; RED for T2 is already recorded
    assert red.main([]) == 0


def test_main_without_spec_dir_explains(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert red.main([]) == 1
    assert "/specforge:spec" in capsys.readouterr().out


# ---------- end to end with node ----------

NODE_TEST = """const test = require("node:test");
const assert = require("node:assert");
const { ok } = require("../src/a.js");
test("AC-2: works", () => assert.ok(ok));
test("AC-3: works too", () => assert.ok(ok));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("ok,expected", [(False, 0), (True, 1)])
def test_node_end_to_end(proj, ok, expected):
    (proj / "tests" / "all.test.js").write_text(NODE_TEST)
    (proj / "src" / "a.js").write_text(f"module.exports = {{ ok: {str(ok).lower()} }};\n")
    _git(proj, "commit", "-qam", "test: node")
    set_config(proj, test_filter="node --test --test-name-pattern {ac_regex}")
    code, _ = red.run(proj)
    assert code == expected
    assert red_of(proj) == ({"T2": head(proj)} if expected == 0 else {})


# ---------- M15-AC4: messages ----------

def test_a_test_outside_the_test_folders_is_named(proj):
    (proj / "tests" / "all.test.js").write_text('test("AC-1: a", () => {});\ntest("AC-2: b", () => {});\n')
    (proj / "test").mkdir()
    (proj / "test" / "three.test.js").write_text('test("AC-3: c", () => {});\n')
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "test: AC-3 in test/")
    code, message = red.run(proj)
    assert code == 1 and red_of(proj) == {}
    assert "AC-3" in message and "tests/" in message and "test/three.test.js" in message
    assert "test_dirs" in message


@pytest.mark.parametrize("setup", ["none", "missing", "passing"])
def test_messages_name_specforge_red_not_the_script(proj, setup):
    if setup == "none":
        set_config(proj)
    elif setup == "missing":
        (proj / "tests" / "all.test.js").write_text('test("AC-1: a", () => {});\n')
        _git(proj, "commit", "-qam", "test: drop")
    else:
        set_config(proj, test_filter="exit 0 #{ac_regex}")
    code, message = red.run(proj)
    assert code == 1 and "red.py" not in message
    if setup != "none":
        assert "specforge red" in message


# ---------- M16-AC3: a test file that failed to load ----------

from test_test_gate import NODE_SPEC_LOAD, PYTEST_COLLECT  # noqa: E402


@pytest.mark.parametrize("output,exit_code", [(NODE_SPEC_LOAD, 1), (PYTEST_COLLECT, 2)])
def test_a_test_file_that_failed_to_load_records_nothing(proj, tmp_path_factory, output, exit_code):
    # pytest exits 2 on a collection error, which red used to take as RED
    out = tmp_path_factory.mktemp("load") / "out.txt"
    out.write_text(output)
    set_config(proj, test_filter=f"cat '{out}'; exit {exit_code} #{{ac_regex}}")
    code, message = red.run(proj)
    assert code == 1 and red_of(proj) == {}
    assert "failed to load" in message and "inside each test" in message


def test_the_temporary_folder_is_left_out_of_the_message(proj, tmp_path_factory):
    out = tmp_path_factory.mktemp("load") / "out.txt"
    set_config(proj, test_filter=(
        f"printf \"Error [ERR_MODULE_NOT_FOUND]: Cannot find module '%s/src/b.js'\\n"
        f"✖ tests/b.test.js (1ms)\\n\" \"$PWD\"; exit 1 #{{ac_regex}}"))
    code, message = red.run(proj)
    assert code == 1 and "Cannot find module 'src/b.js'" in message
    assert "specforge-red-" not in message


# ---------- M16-AC2: specforge red --all, at plan time ----------

ALL_PLAN = """## Tasks
- [ ] T1 (M1, AC-1): slugify
- [ ] T2 (M2, AC-2): truncate
- [ ] T3 (M2): docs
"""

LAYOUTS = {
    "one file, top-level import": {"tests/s.test.js": """import { test } from 'node:test';
import assert from 'node:assert/strict';
import { slugify, truncate } from '../src/s.js';
test('AC-1 slug', () => assert.equal(slugify('A B'), 'a-b'));
test('AC-2 trunc', () => assert.equal(truncate('abcdef', 4), 'abc…'));
"""},
    "two files, top-level imports": {
        "tests/slug.test.js": """import { test } from 'node:test';
import assert from 'node:assert/strict';
import { slugify } from '../src/slug.js';
test('AC-1 slug', () => assert.equal(slugify('A B'), 'a-b'));
""",
        "tests/trunc.test.js": """import { test } from 'node:test';
import assert from 'node:assert/strict';
import { truncate } from '../src/trunc.js';
test('AC-2 trunc', () => assert.equal(truncate('abcdef', 4), 'abc…'));
"""},
    "loaded inside each test": {"tests/s.test.js": """import { test } from 'node:test';
import assert from 'node:assert/strict';
test('AC-1 slug', async () => {
  const { slugify } = await import('../src/s.js');
  assert.equal(slugify('A B'), 'a-b');
});
test('AC-2 trunc', async () => {
  const { truncate } = await import('../src/s.js');
  assert.equal(truncate('abcdef', 4), 'abc…');
});
"""},
}


def plan_project(tmp_path, files, cfg=None):
    (tmp_path / ".spec").mkdir()
    state.create(tmp_path)
    state.approve(tmp_path, "spec")  # the plan phase: the plan isn't approved yet
    (tmp_path / ".spec" / "plan.md").write_text(ALL_PLAN)
    (tmp_path / "package.json").write_text('{ "type": "module" }\n')
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    set_config(tmp_path, **(cfg if cfg is not None else
               {"test_filter": "node --test --test-name-pattern {ac_regex} tests/*.test.js"}))
    return tmp_path


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("layout,ok", [("one file, top-level import", False),
                                       ("two files, top-level imports", False),
                                       ("loaded inside each test", True)])
def test_red_all_passes_only_when_every_tasks_tests_load_and_fail(tmp_path, layout, ok):
    proj = plan_project(tmp_path, LAYOUTS[layout])
    code, message = red.check_all(proj)
    assert code == (0 if ok else 1), message
    assert state.red_tasks(state.read(proj)) == {}  # records nothing
    if ok:
        assert "T1" in message and "T2" in message
    else:
        assert "failed to load" in message and "tests/" in message and "T1" in message
        assert "inside each test" in message


def test_red_all_runs_each_task_on_the_working_tree_without_a_commit(tmp_path, tmp_path_factory):
    got = tmp_path_factory.mktemp("o") / "got.txt"
    proj = plan_project(tmp_path, {}, {"test_filter": f"printf '%s\\n' {{ac_regex}} >> '{got}'; exit 1"})
    for ac in ("AC-1", "AC-2"):
        (proj / "tests").mkdir(exist_ok=True)
        (proj / "tests" / f"{ac}.test.js").write_text(f'test("{ac}: x", () => {{}});\n')
    code, message = red.check_all(proj)
    assert code == 0, message
    assert got.read_text().splitlines() == [test_gate.ac_regex(["AC-1"]), test_gate.ac_regex(["AC-2"])]


def test_red_all_reports_tests_that_already_pass_or_ran_nothing(tmp_path):
    proj = plan_project(tmp_path, {"tests/a.test.js": 'test("AC-1: x", () => {});\ntest("AC-2: y", () => {});\n'},
                       {"test_filter": "exit 0 #{ac_regex}"})
    code, message = red.check_all(proj)
    assert code == 1 and "already pass" in message


# ---------- M19: without test_filter, red --all says the build will stop ----------

LOADS_INSIDE = LAYOUTS["loaded inside each test"]["tests/s.test.js"]


def unfiltered(tmp_path, files, npm_test="node --test", plan=ALL_PLAN, cfg=None):
    """A plan-phase project with no test_filter, whose suite runs as `npm test`."""
    proj = plan_project(tmp_path, files, cfg or {})
    (proj / ".spec" / "plan.md").write_text(plan)
    scripts = {"test": npm_test} if npm_test else {}
    (proj / "package.json").write_text(json.dumps({"type": "module", "scripts": scripts}))
    return proj


@pytest.mark.parametrize("plan", [
    "- [ ] T1 (M1, AC-1, AC-2): both\n- [ ] T2 (M2): docs\n",
    "- [x] T1 (M1, AC-1): done\n- [ ] T2 (M1, AC-2): last\n",
    "- [ ] T1 (M1, AC-1): a\n- [ ] T2 (M1, AC-1): a again\n",
])
def test_red_all_without_a_filter_passes_when_the_first_task_covers_every_ac(tmp_path, plan):
    # M19-AC1: the full suite can pass at the first open task's feat: commit
    proj = unfiltered(tmp_path, {}, plan=plan)
    code, message = red.check_all(proj)
    assert code == 0, message
    assert "will stop" not in message and "Suggested" not in message


@pytest.mark.parametrize("plan,first,later", [
    (ALL_PLAN, "T1", "T2"),
    ("- [ ] T1 (M1): setup\n- [ ] T2 (M1, AC-1): a\n- [ ] T3 (M2, AC-2): b\n", "T1", "T2, T3"),
    ("- [x] T1 (M1, AC-1): done\n- [ ] T2 (M1, AC-2): b\n- [ ] T3 (M2, AC-3): c\n", "T2", "T3"),
])
def test_red_all_without_a_filter_says_the_build_will_stop_at_the_first_task(tmp_path, plan, first, later):
    # M19-AC1/AC2: an open task after the first has an AC the first doesn't
    proj = unfiltered(tmp_path, {}, npm_test=None, plan=plan)
    code, message = red.check_all(proj)
    assert code == 0, message  # M19-AC2: setting it is the user's call
    assert f"the build will stop at {first}" in message
    assert f"until {later}" in message
    assert "/specforge:approve plan" in message
    assert state.red_tasks(state.read(proj)) == {}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("files,expected", [
    ({"tests/s.test.js": LOADS_INSIDE}, "node --test --test-name-pattern {ac_regex} tests/*.test.js"),
    # not a name node --test looks for, so the bare form would run nothing (M19 spike)
    ({"tests/s.js": LOADS_INSIDE}, "node --test --test-name-pattern {ac_regex} tests/*.js"),
    ({"tests/unit/a.test.js": LOADS_INSIDE.split("test('AC-2")[0],
      "tests/b.test.js": "import { test } from 'node:test';\n" + "test('AC-2" + LOADS_INSIDE.split("test('AC-2")[1]},
     "node --test --test-name-pattern {ac_regex} tests/*.test.js tests/unit/*.test.js"),
])
def test_red_all_suggests_a_node_filter_that_names_the_test_files(tmp_path, files, expected):
    # M19-AC2: checked by running it, never a folder (Node 24 runs it as a failing file)
    proj = unfiltered(tmp_path, files)
    code, message = red.check_all(proj)
    assert code == 0, message
    assert f'"test_filter": {json.dumps(expected)}' in message
    assert "checked" in message.lower()
    assert "tests/\"" not in message and "tests/ " not in message
    assert not (proj / ".spec" / "config.json").read_text().strip("{} \n")  # changes nothing


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_red_all_suggests_only_a_filter_that_passes_the_check(tmp_path):
    # M19-AC2: a top-level import can't load before the code exists, so no filter passes
    proj = unfiltered(tmp_path, LAYOUTS["one file, top-level import"])
    code, message = red.check_all(proj)
    assert code == 0, message
    assert "the build will stop at T1" in message
    assert '"test_filter":' not in message
    assert "Tried" in message and "--test-name-pattern {ac_regex} tests/*.test.js" in message
    assert "failed to load" in message


def test_red_all_suggests_a_pytest_filter_from_test_command(tmp_path):
    # M19-AC2: built from test_command, and checked with the real runner
    runner = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider"
    proj = unfiltered(tmp_path, {"tests/test_s.py": (
        "def test_ac1_slug():\n    from s import slugify\n    assert slugify('A B') == 'a-b'\n\n"
        "def test_ac2_trunc():\n    from s import truncate\n    assert truncate('abcdef', 4) == 'abc…'\n"
    )}, npm_test=None, cfg={"test_command": runner})
    code, message = red.check_all(proj)
    assert code == 0, message
    assert f'"test_filter": {json.dumps(runner + " -k {pytest_k}")}' in message


@pytest.mark.parametrize("command,package_test,expected", [
    ("pytest -q", None, "pytest -q -k {pytest_k}"),
    ("python3 -m pytest", None, "python3 -m pytest -k {pytest_k}"),
    ("node --test tests/*.test.js", None, "node --test --test-name-pattern {ac_regex} tests/*.test.js"),
    ("npm test", "jest --runInBand", "npx jest --runInBand -t {ac_regex}"),
    ("npm test", "vitest", "npx vitest run -t {ac_regex}"),
    ("npm test", "vitest run --reporter dot", "npx vitest run --reporter dot -t {ac_regex}"),
    ("npm test", "mocha tests/", "npx mocha tests/ --grep {ac_regex}"),
    ("npx jest", None, "npx jest -t {ac_regex}"),
    ("flutter test", None, "flutter test --name {ac_regex}"),
])
def test_filter_candidates_fit_the_runner(tmp_path, command, package_test, expected):
    # M19-AC2: one filter per runner the gate knows, built from the command it runs
    proj = unfiltered(tmp_path, {}, npm_test=package_test,
                      cfg=None if command == "npm test" else {"test_command": command})
    candidates, problem = red.filter_candidates(proj, config.load(proj), which=lambda n: n)
    assert problem is None
    assert expected in candidates
    for candidate in candidates:
        assert config._placeholder_in_quotes(candidate) is False
        assert any(ph in candidate for ph in config.FILTER_PLACEHOLDERS)


@pytest.mark.parametrize("cfg,npm_test,expected", [
    ({"test_command": "make test"}, None, "make test"),
    ({}, None, "no test command found"),
])
def test_red_all_says_why_it_has_no_suggestion(tmp_path, cfg, npm_test, expected):
    proj = unfiltered(tmp_path, {}, npm_test=npm_test, cfg=cfg)
    code, message = red.check_all(proj)
    assert code == 0, message
    assert "the build will stop at T1" in message and expected in message
    assert '"test_filter":' not in message
    assert "specforge red --all" in message  # how to check one the user writes


def test_red_all_names_a_task_with_no_test(tmp_path):
    proj = plan_project(tmp_path, {"tests/a.test.js": 'test("AC-1: x", () => {});\n'},
                       {"test_filter": "exit 1 #{ac_regex}"})
    code, message = red.check_all(proj)
    assert code == 1 and "T2" in message and "AC-2" in message


def test_main_all(tmp_path, monkeypatch, capsys):
    proj = plan_project(tmp_path, {}, {})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    assert red.main(["--all"]) == 0
    assert "test_filter" in capsys.readouterr().out
