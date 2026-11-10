"""Optional per-project settings in <project>/.spec/config.json."""
import json
from pathlib import Path

DEFAULT_CONFIG = {
    "code_dirs": ["src", "app", "lib"],
    "test_dirs": ["tests"],
    "test_command": None,
    # The spec phase saves the description verbatim to .spec/source.md and
    # spec-reviewer checks the spec against it. false gives the original spec
    # phase. The command and agent prompts read this from config.json.
    "source_fidelity": True,
    # More ACs than this makes spec-reviewer suggest splitting the feature or
    # merging ACs (M8). null turns the suggestion off; spec_size.py still counts.
    "ac_budget": 15,
}


class ConfigError(Exception):
    """config.json exists but is not valid."""


def _check_dirs(key, value):
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{key} must be a non-empty list of folder names")
    for name in value:
        if not isinstance(name, str) or not name or "/" in name or "\\" in name:
            raise ConfigError(f"{key} entries must be plain folder names, got {name!r}")


def load(project_dir) -> dict:
    cfg = {k: list(v) if isinstance(v, list) else v for k, v in DEFAULT_CONFIG.items()}
    path = Path(project_dir) / ".spec" / "config.json"
    if not path.is_file():
        return cfg
    try:
        data = json.loads(path.read_text())
    except ValueError as e:
        raise ConfigError(str(e)) from None
    if not isinstance(data, dict):
        raise ConfigError("config must be a JSON object")
    unknown = set(data) - set(DEFAULT_CONFIG)
    if unknown:
        raise ConfigError(f"unknown config keys: {sorted(unknown)}")
    cfg.update(data)
    _check_dirs("code_dirs", cfg["code_dirs"])
    _check_dirs("test_dirs", cfg["test_dirs"])
    cmd = cfg["test_command"]
    if cmd is not None and (not isinstance(cmd, str) or not cmd.strip()):
        raise ConfigError("test_command must be a non-empty string")
    if not isinstance(cfg["source_fidelity"], bool):
        raise ConfigError("source_fidelity must be true or false")
    budget = cfg["ac_budget"]
    if budget is not None and (isinstance(budget, bool) or not isinstance(budget, int) or budget < 1):
        raise ConfigError("ac_budget must be a positive whole number, or null to turn it off")
    return cfg
