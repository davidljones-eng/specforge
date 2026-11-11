#!/usr/bin/env python3
"""Copy source documents into .spec/source.md, unchanged (M9).

    import_source.py [--project-dir DIR] FILE [FILE ...]

Used by `/specforge:spec --from <file>`, so a long SPEC+ or SPEC Lite document
reaches the spec phase exactly as written instead of being retyped by the
model. Each file goes under a heading naming it, in the order given. Relative
paths are read from the project folder. If source.md already exists, the files
are added under a dated heading and the earlier text is kept. Nothing is
written unless every file can be read as text.
"""
import argparse
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import config


def _read(project, name):
    path = Path(name)
    if not path.is_absolute():
        path = project / path
    if not path.is_file():
        raise ValueError(f"{name} is not a file")
    try:
        return path.read_bytes().decode("utf-8")
    except OSError as e:
        raise ValueError(f"{name} could not be read ({e.strerror})") from None
    except UnicodeDecodeError:
        raise ValueError(f"{name} is not a UTF-8 text file") from None


def _section(name, text):
    if not text.endswith("\n"):
        text += "\n"
    return f"## From `{name}`\n\n{text}"


def import_files(project, names) -> Path:
    """Write the files into project/.spec/source.md. Raises ValueError with a reason."""
    project = Path(project)
    spec_dir = project / ".spec"
    if not spec_dir.is_dir():
        raise ValueError("this project has no .spec/ folder. Run /specforge:spec first")
    if not names:
        raise ValueError("give at least one file to import")
    try:
        cfg = config.load(project)
    except config.ConfigError as e:
        raise ValueError(f".spec/config.json is invalid ({e})") from None
    if not cfg["source_fidelity"]:
        raise ValueError(
            '.spec/config.json sets "source_fidelity": false, so no source.md is kept. '
            "Read the files directly instead"
        )

    sections = "\n".join(_section(name, _read(project, name)) for name in names)
    target = spec_dir / "source.md"
    if target.is_file():
        earlier = target.read_bytes().decode("utf-8", errors="replace")
        if not earlier.endswith("\n"):
            earlier += "\n"
        text = f"{earlier}\n## {date.today().isoformat()}: imported again\n\n{sections}"
    else:
        text = sections

    fd, tmp = tempfile.mkstemp(dir=spec_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="import_source.py")
    parser.add_argument("--project-dir")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    project = Path(args.project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    try:
        import_files(project, args.files)
    except ValueError as e:
        print(f"specforge: nothing imported. {e}.")
        return 1
    print(f"specforge: copied {len(args.files)} file(s) into .spec/source.md, unchanged: "
          + ", ".join(args.files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
