import json

import pytest

import state


@pytest.fixture
def proj(tmp_path):
    return tmp_path


def write_raw(proj, text):
    (proj / ".spec").mkdir(exist_ok=True)
    (proj / ".spec" / "state.json").write_text(text)


# ---------- create ----------

def test_create_writes_default_state(proj):
    result = state.create(proj)
    assert result == {
        "phase": "spec",
        "spec_approved": False,
        "plan_approved": False,
        "spec_version": 1,
        "build_mode": "checkpoint",
    }
    on_disk = json.loads((proj / ".spec" / "state.json").read_text())
    assert on_disk == result


def test_create_makes_spec_dir(proj):
    state.create(proj)
    assert (proj / ".spec").is_dir()


def test_create_refuses_to_overwrite_existing_state(proj):
    state.create(proj)
    state.approve(proj, "spec")
    with pytest.raises(FileExistsError):
        state.create(proj)
    assert state.read(proj)["spec_approved"] is True


def test_create_with_build_mode(proj):
    assert state.create(proj, build_mode="strict")["build_mode"] == "strict"


def test_create_rejects_invalid_build_mode(proj):
    with pytest.raises(ValueError):
        state.create(proj, build_mode="yolo")


# ---------- read ----------

def test_read_returns_saved_state(proj):
    state.create(proj)
    assert state.read(proj)["phase"] == "spec"


def test_read_missing_file_raises_missing(proj):
    with pytest.raises(state.StateMissingError):
        state.read(proj)


def test_exists_reflects_file_presence(proj):
    assert state.exists(proj) is False
    state.create(proj)
    assert state.exists(proj) is True


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "{not json",
        "[]",
        "null",
        '{"phase": "spec"}',  # missing keys
        json.dumps({**state.DEFAULT_STATE, "spec_approved": "yes"}),  # wrong type
        json.dumps({**state.DEFAULT_STATE, "phase": "deploy"}),  # bad phase
        json.dumps({**state.DEFAULT_STATE, "build_mode": "yolo"}),  # bad mode
        json.dumps({**state.DEFAULT_STATE, "spec_version": 0}),  # must be >= 1
        json.dumps({**state.DEFAULT_STATE, "spec_version": True}),  # bool is not int
        json.dumps({**state.DEFAULT_STATE, "plan_approved": True}),  # plan w/o spec
    ],
)
def test_read_corrupt_file_raises_corrupt(proj, raw):
    write_raw(proj, raw)
    with pytest.raises(state.StateCorruptError):
        state.read(proj)


def test_read_rejects_unknown_keys(proj):
    write_raw(proj, json.dumps({**state.DEFAULT_STATE, "extra": 1}))
    with pytest.raises(state.StateCorruptError):
        state.read(proj)


# ---------- update ----------

def test_update_changes_fields_and_persists(proj):
    state.create(proj)
    result = state.update(proj, phase="plan", build_mode="auto")
    assert result["phase"] == "plan" and result["build_mode"] == "auto"
    assert state.read(proj)["phase"] == "plan"


def test_update_leaves_other_fields_untouched(proj):
    state.create(proj)
    state.update(proj, phase="plan")
    assert state.read(proj)["spec_version"] == 1


def test_update_rejects_unknown_key(proj):
    state.create(proj)
    with pytest.raises(ValueError):
        state.update(proj, nonsense=1)


@pytest.mark.parametrize(
    "changes",
    [
        {"phase": "deploy"},
        {"build_mode": "yolo"},
        {"spec_approved": "yes"},
        {"spec_version": 0},
        {"spec_version": "2"},
    ],
)
def test_update_rejects_invalid_values_and_does_not_write(proj, changes):
    state.create(proj)
    with pytest.raises(ValueError):
        state.update(proj, **changes)
    assert state.read(proj) == state.DEFAULT_STATE


def test_update_missing_file_raises_missing(proj):
    with pytest.raises(state.StateMissingError):
        state.update(proj, phase="plan")


def test_update_corrupt_file_raises_corrupt_and_leaves_it(proj):
    write_raw(proj, "{broken")
    with pytest.raises(state.StateCorruptError):
        state.update(proj, phase="plan")
    assert (proj / ".spec" / "state.json").read_text() == "{broken"


def test_update_cannot_set_plan_approved_without_spec_approved(proj):
    state.create(proj)
    with pytest.raises(ValueError):
        state.update(proj, plan_approved=True)


def test_write_leaves_no_temp_files(proj):
    state.create(proj)
    state.update(proj, phase="plan")
    assert [p.name for p in (proj / ".spec").iterdir()] == ["state.json"]


# ---------- approve ----------

def test_approve_spec(proj):
    state.create(proj)
    assert state.approve(proj, "spec")["spec_approved"] is True


def test_approve_plan_requires_spec_approval(proj):
    state.create(proj)
    with pytest.raises(ValueError, match="spec"):
        state.approve(proj, "plan")
    assert state.read(proj)["plan_approved"] is False


def test_approve_plan_after_spec(proj):
    state.create(proj)
    state.approve(proj, "spec")
    result = state.approve(proj, "plan")
    assert result["spec_approved"] and result["plan_approved"]


def test_approve_unknown_target(proj):
    state.create(proj)
    with pytest.raises(ValueError):
        state.approve(proj, "build")


# ---------- spec version bump resets approvals ----------

def test_bump_increments_version_and_resets_both_approvals(proj):
    state.create(proj)
    state.approve(proj, "spec")
    state.approve(proj, "plan")
    result = state.bump_spec_version(proj)
    assert result["spec_version"] == 2
    assert result["spec_approved"] is False
    assert result["plan_approved"] is False


def test_bump_keeps_phase_and_build_mode(proj):
    state.create(proj, build_mode="strict")
    state.update(proj, phase="build")
    result = state.bump_spec_version(proj)
    assert result["phase"] == "build" and result["build_mode"] == "strict"


def test_bump_twice(proj):
    state.create(proj)
    state.bump_spec_version(proj)
    assert state.bump_spec_version(proj)["spec_version"] == 3


def test_bump_persists(proj):
    state.create(proj)
    state.approve(proj, "spec")
    state.bump_spec_version(proj)
    saved = state.read(proj)
    assert saved["spec_version"] == 2 and saved["spec_approved"] is False


# ---------- CLI ----------

def run(proj, *argv):
    return state.main(["--project-dir", str(proj), *argv])


def test_cli_init_then_get(proj, capsys):
    assert run(proj, "init") == 0
    capsys.readouterr()
    assert run(proj, "get") == 0
    assert json.loads(capsys.readouterr().out) == state.DEFAULT_STATE


def test_cli_init_with_mode(proj):
    assert run(proj, "init", "--mode", "auto") == 0
    assert state.read(proj)["build_mode"] == "auto"


@pytest.mark.parametrize("target", ["spec", "plan"])
def test_cli_has_no_approve_command(proj, capsys, target):
    # M5-AC2: only the approval hook records approvals
    run(proj, "init")
    with pytest.raises(SystemExit) as exit_info:
        run(proj, "approve", target)
    assert exit_info.value.code != 0
    saved = state.read(proj)
    assert not saved["spec_approved"] and not saved["plan_approved"]


def test_cli_bump(proj):
    run(proj, "init")
    state.approve(proj, "spec")
    assert run(proj, "bump") == 0
    assert state.read(proj)["spec_version"] == 2


def test_cli_set_phase_and_mode(proj):
    run(proj, "init")
    assert run(proj, "set-phase", "plan") == 0
    assert run(proj, "set-mode", "strict") == 0
    saved = state.read(proj)
    assert saved["phase"] == "plan" and saved["build_mode"] == "strict"


def test_cli_missing_state_reports_reason(proj, capsys):
    assert run(proj, "get") == 1
    assert "/specforge:spec" in capsys.readouterr().err


def test_cli_corrupt_state_reports_reason(proj, capsys):
    write_raw(proj, "{broken")
    assert run(proj, "get") == 1
    assert "corrupt" in capsys.readouterr().err.lower()


def test_cli_invalid_phase_is_an_error_not_a_crash(proj, capsys):
    run(proj, "init")
    capsys.readouterr()
    assert run(proj, "set-phase", "deploy") == 1
    assert capsys.readouterr().err


def test_cli_init_twice_fails(proj, capsys):
    run(proj, "init")
    capsys.readouterr()
    assert run(proj, "init") == 1
    assert "exists" in capsys.readouterr().err.lower()
