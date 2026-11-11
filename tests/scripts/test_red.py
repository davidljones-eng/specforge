import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

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


def test_red_all_without_a_test_filter_has_nothing_to_check(tmp_path):
    proj = plan_project(tmp_path, {}, {})
    code, message = red.check_all(proj)
    assert code == 0 and "test_filter" in message


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
