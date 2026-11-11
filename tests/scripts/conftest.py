import json

import pytest

import state


@pytest.fixture(autouse=True)
def _approvals_without_a_config_fingerprint(request, monkeypatch):
    """Most tests write .spec/config.json after approving the plan, to test other
    gates. M17 refuses a config changed since approval, unless the state has no
    fingerprint, as one written before M17 has. So, here, approving records none,
    unless the test is marked config_lock (the M17 tests)."""
    if request.node.get_closest_marker("config_lock"):
        return
    approve = state.approve

    def approve_without_fingerprint(project_dir, target):
        data = approve(project_dir, target)
        if "approved_config" in data:
            del data["approved_config"]
            state.state_path(project_dir).write_text(json.dumps(data, indent=2) + "\n")
        return data

    monkeypatch.setattr(state, "approve", approve_without_fingerprint)
