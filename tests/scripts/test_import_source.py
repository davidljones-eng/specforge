import json
from datetime import date

import pytest

import import_source

PLUS = "# Stage 2 — SPEC+\n\n## C — ID'd Checks\n\n| ID | Given |\n|---|---|\n| C1 | x |\n\ttab  and  spaces kept \n"
LITE = "## What it does\n- An error bubble (\"Something went wrong, please try again\")\n"


@pytest.fixture
def proj(tmp_path):
    (tmp_path / ".spec").mkdir()
    (tmp_path / "02-spec-plus.md").write_text(PLUS)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "01-lite.md").write_text(LITE)
    return tmp_path


def source(proj):
    return (proj / ".spec" / "source.md").read_text()


def run(proj, *files):
    return import_source.main(["--project-dir", str(proj), *files])


# ---------- copying (M9-AC1) ----------

def test_a_file_is_copied_byte_for_byte_under_a_heading_naming_it(proj, capsys):
    assert run(proj, "02-spec-plus.md") == 0
    text = source(proj)
    assert "## From `02-spec-plus.md`" in text
    assert PLUS in text  # unchanged, whitespace and all
    assert text.index("## From `02-spec-plus.md`") < text.index(PLUS)
    assert "02-spec-plus.md" in capsys.readouterr().out


def test_several_files_are_copied_in_the_order_given(proj):
    assert run(proj, "02-spec-plus.md", "docs/01-lite.md") == 0
    text = source(proj)
    assert PLUS in text and LITE in text
    assert text.index("## From `02-spec-plus.md`") < text.index("## From `docs/01-lite.md`")
    assert text.index(PLUS) < text.index(LITE)


def test_an_absolute_path_works(proj):
    assert run(proj, str(proj / "docs" / "01-lite.md")) == 0
    assert LITE in source(proj)


def test_a_file_without_a_final_newline_does_not_run_into_the_next(proj):
    (proj / "a.md").write_text("no newline at the end")
    assert run(proj, "a.md", "docs/01-lite.md") == 0
    assert "no newline at the end\n" in source(proj)


def test_an_earlier_source_is_kept_and_the_new_files_are_added_under_a_dated_heading(proj):
    (proj / ".spec" / "source.md").write_text("earlier description\n\n## Answers\n\ndefaults\n")
    assert run(proj, "docs/01-lite.md") == 0
    text = source(proj)
    assert text.startswith("earlier description\n\n## Answers\n\ndefaults\n")
    dated = f"## {date.today().isoformat()}: imported again"
    assert dated in text and text.index(dated) < text.index(LITE)


# ---------- refusing ----------

@pytest.mark.parametrize("files", [["missing.md"], ["02-spec-plus.md", "missing.md"]])
def test_a_missing_file_stops_and_writes_nothing(proj, capsys, files):
    assert run(proj, *files) == 1
    assert "missing.md" in capsys.readouterr().out
    assert not (proj / ".spec" / "source.md").exists()


def test_a_folder_is_refused(proj, capsys):
    assert run(proj, "docs") == 1
    assert not (proj / ".spec" / "source.md").exists()


def test_a_file_that_is_not_text_is_refused(proj, capsys):
    (proj / "blob.bin").write_bytes(b"\xff\xfe\x00\x81")
    assert run(proj, "blob.bin") == 1
    assert "blob.bin" in capsys.readouterr().out
    assert not (proj / ".spec" / "source.md").exists()


def test_no_files_is_an_error(proj, capsys):
    assert run(proj) == 1


def test_no_spec_folder_is_refused(tmp_path, capsys):
    (tmp_path / "a.md").write_text("x")
    assert run(tmp_path, "a.md") == 1
    assert "/specforge:spec" in capsys.readouterr().out


def test_source_fidelity_off_copies_nothing(proj, capsys):
    (proj / ".spec" / "config.json").write_text(json.dumps({"source_fidelity": False}))
    assert run(proj, "02-spec-plus.md") == 1
    assert "source_fidelity" in capsys.readouterr().out
    assert not (proj / ".spec" / "source.md").exists()


def test_main_uses_claude_project_dir(proj, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    monkeypatch.chdir(proj / "docs")
    assert import_source.main(["02-spec-plus.md"]) == 0  # relative to the project
    assert PLUS in source(proj)
