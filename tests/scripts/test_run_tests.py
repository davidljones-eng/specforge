"""M18: `specforge test` runs the tests the way the commit gate does."""
import json
import subprocess

import pytest

import run_tests
import test_gate


class FakeRun:
    """Stands in for subprocess.run and records each call."""

    def __init__(self, returncode=0, raises=None):
        self.calls, self.returncode, self.raises = [], returncode, raises

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(command, self.returncode)


def on_path(name):
    return f"/usr/bin/{name}"


def write_config(proj, cfg):
    (proj / ".spec").mkdir(exist_ok=True)
    (proj / ".spec" / "config.json").write_text(json.dumps(cfg))


def call(proj, *argv, run=None, which=on_path):
    run = run or FakeRun()
    code = run_tests.main(["--project-dir", str(proj), *argv], run=run, which=which)
    return code, run


@pytest.fixture(autouse=True)
def no_project_env(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


def test_runs_test_command_when_it_is_set(tmp_path, capsys):
    # M18-AC1
    write_config(tmp_path, {"test_command": "make test"})
    code, run = call(tmp_path)
    assert code == 0
    assert [c for c, _ in run.calls] == ["make test"]
    assert capsys.readouterr().out.startswith("specforge test: make test")


@pytest.mark.parametrize("files, expected", [
    ({"conftest.py": ""}, "pytest -q"),
    ({"package.json": json.dumps({"scripts": {"test": "node --test"}})}, "npm test"),
    ({"pubspec.yaml": "name: x\n"}, "flutter test"),
])
def test_without_test_command_runs_the_runner_the_gate_detects(tmp_path, files, expected):
    # M18-AC1: the same fallback as the commit gate, not a guess of the model's
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    code, run = call(tmp_path)
    assert code == 0
    assert run.calls[0][0] == expected == test_gate.resolve_command(tmp_path, {"test_command": None}, which=on_path)[0]


def test_runs_in_the_project_folder_with_the_gates_timeout(tmp_path):
    # M18-AC1
    write_config(tmp_path, {"test_command": "make test"})
    _, run = call(tmp_path)
    kwargs = run.calls[0][1]
    assert kwargs["shell"] is True
    assert str(kwargs["cwd"]) == str(tmp_path)
    assert kwargs["timeout"] == test_gate.TEST_TIMEOUT


@pytest.mark.parametrize("status", [1, 3, 127])
def test_exits_with_the_tests_exit_status(tmp_path, status):
    # M18-AC1
    write_config(tmp_path, {"test_command": "make test"})
    code, _ = call(tmp_path, run=FakeRun(returncode=status))
    assert code == status


def test_ac_ids_with_test_filter_run_the_filter_for_those_acs(tmp_path, capsys):
    # M18-AC1
    filt = "node --test --test-name-pattern {ac_regex} tests/*.test.js"
    write_config(tmp_path, {"test_command": "make test", "test_filter": filt})
    code, run = call(tmp_path, "AC-1", "AC-3")
    assert code == 0
    assert run.calls[0][0] == test_gate.filter_command(filt, ["AC-1", "AC-3"])
    assert capsys.readouterr().out.startswith(f"specforge test: {run.calls[0][0]}")


def test_ac_ids_without_test_filter_run_the_full_suite_and_say_so(tmp_path, capsys):
    # M18-AC1
    write_config(tmp_path, {"test_command": "make test"})
    code, run = call(tmp_path, "AC-1")
    assert code == 0
    assert run.calls[0][0] == "make test"
    assert "test_filter" in capsys.readouterr().out


@pytest.mark.parametrize("arg", ["rm", "AC-", "ac-1", "AC-1;ls", "T1"])
def test_only_ac_ids_are_accepted(tmp_path, arg):
    write_config(tmp_path, {"test_command": "make test", "test_filter": "x {ac_regex}"})
    code, run = call(tmp_path, arg)
    assert code == 2
    assert run.calls == []


def test_no_runner_found_tells_the_model_to_ask_the_user(tmp_path, capsys):
    # M18-AC1: the baseline runs guessed at a test_command instead
    code, run = call(tmp_path)
    out = capsys.readouterr().out
    assert code == 1 and run.calls == []
    assert "test_command" in out and "ask the user" in out.lower()
    assert "another way" in out


def test_runner_not_installed_tells_the_model_to_ask_the_user(tmp_path, capsys):
    # M18-AC1
    (tmp_path / "conftest.py").write_text("")
    code, run = call(tmp_path, which=lambda name: None)
    out = capsys.readouterr().out
    assert code == 1 and run.calls == []
    assert "'pytest' not found" in out and "ask the user" in out.lower()


def test_a_timeout_is_reported(tmp_path, capsys):
    write_config(tmp_path, {"test_command": "make test"})
    code, _ = call(tmp_path, run=FakeRun(raises=subprocess.TimeoutExpired("make test", 1)))
    assert code == 1
    assert f"{test_gate.TEST_TIMEOUT}s" in capsys.readouterr().out


def test_an_invalid_config_is_reported(tmp_path, capsys):
    (tmp_path / ".spec").mkdir()
    (tmp_path / ".spec" / "config.json").write_text("{ not json")
    code, run = call(tmp_path)
    assert code == 1 and run.calls == []
    assert "config.json is invalid" in capsys.readouterr().out


def test_the_project_folder_defaults_to_claude_project_dir(tmp_path, monkeypatch):
    write_config(tmp_path, {"test_command": "make test"})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    run = FakeRun()
    assert run_tests.main([], run=run, which=on_path) == 0
    assert str(run.calls[0][1]["cwd"]) == str(tmp_path)
