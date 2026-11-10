#!/usr/bin/env python3
"""How big the spec has grown against its source (M8).

Prints one line for the spec phase's summary and for spec-reviewer, e.g.

    6 source checks → 21 ACs (budget 15). C1: 2, C2: 3, ... no C-ID: 1. Over budget by 6.

A source check with no AC tagged with it, and no mention in the spec's Out of
Scope section, is listed as uncovered (M9). The source checks are the C-IDs in the first column of .spec/source.md's ID
tables (SPEC+ style). An AC's tags are the list right after its ID, as in
"- AC-3 (C1):" or "- AC-4 [C1, E2]:"; only its C-IDs count here. These rules
match tools/check_coverage.py in the Spec-Engineering repo. The counts are
exact; whether ACs restate each other is left to the reviewer.
"""
import argparse
import os
import re
import sys
from pathlib import Path

import config

# "- AC-3 [C1, E2]: ...", "* **AC-3** [C1]: ..." or "- AC-3 (C1, E2): ..."
_AC_BULLET = re.compile(
    r"^\s*[-*]\s+\**(?P<id>AC-\d+)\**\s*"
    r"(?:\[(?P<tags>[^\]]*)\]|\((?P<ptags>[^)]*)\))?\s*:"
)
_PLAIN_ID = re.compile(r"^[A-Za-z]+-?\d+$")
_C_ID = re.compile(r"^C\d+$")
_ID_TABLE_HEADER = re.compile(r"^\|\s*id\s*\|", re.IGNORECASE)
_SEPARATOR_ROW = re.compile(r"^\|[\s\-:|]+\|$")
_TABLE_ROW = re.compile(r"^\|(.+)\|$")


def _table_ids(text):
    """First-column values of the data rows of every table headed "ID"."""
    ids, in_table = [], False
    for raw in text.splitlines():
        line = raw.strip()
        if _ID_TABLE_HEADER.match(line):
            in_table = True
            continue
        if not in_table or _SEPARATOR_ROW.match(line):
            continue
        row = _TABLE_ROW.match(line)
        if not row:
            in_table = False
            continue
        cell = row.group(1).split("|")[0].strip().strip("`* ")
        if cell:
            ids.append(cell)
    return ids


def _ac_tags(text):
    """AC-ID -> its tags, in document order. A [bracketed] list is taken as
    written; a (parenthesized) one counts only if every entry is a plain ID."""
    acs = {}
    for line in text.splitlines():
        m = _AC_BULLET.match(line)
        if not m or m.group("id") in acs:
            continue
        tags = [t.strip() for t in (m.group("tags") or "").split(",") if t.strip()]
        if m.group("ptags") is not None:
            ptags = [t.strip() for t in m.group("ptags").split(",")]
            tags = ptags if all(_PLAIN_ID.match(t) for t in ptags) else []
        acs[m.group("id")] = tags
    return acs


def _out_of_scope_ids(text):
    """C-IDs mentioned in the spec's Out of Scope section."""
    ids, level = set(), None
    for line in text.splitlines():
        heading = re.match(r"^(#+)\s*(.*?)\s*$", line)
        if heading:
            if level is not None and len(heading.group(1)) <= level:
                break
            if heading.group(2).lower() == "out of scope":
                level = len(heading.group(1))
            continue
        if level is not None:
            ids.update(re.findall(r"\bC\d+\b", line))
    return ids


def _number(cid):
    return int(cid[1:])


def measure(project_dir) -> dict:
    """Counts for the spec in `project_dir`. Raises FileNotFoundError or config.ConfigError."""
    spec_dir = Path(project_dir) / ".spec"
    cfg = config.load(project_dir)
    spec = (spec_dir / "spec.md").read_text(errors="replace")
    source_path = spec_dir / "source.md"
    source = source_path.read_text(errors="replace") if source_path.is_file() else ""

    tags = {ac: [t for t in ts if _C_ID.match(t)] for ac, ts in _ac_tags(spec).items()}
    acs = list(tags)

    checks = sorted({i for i in _table_ids(source) if _C_ID.match(i)}, key=_number)
    per_check = {c: 0 for c in checks}
    for cids in tags.values():
        for c in dict.fromkeys(cids):
            per_check[c] = per_check.get(c, 0) + 1
    per_check = dict(sorted(per_check.items(), key=lambda kv: (kv[0] not in checks, _number(kv[0]))))

    out_of_scope = _out_of_scope_ids(spec)
    budget = cfg["ac_budget"]
    return {
        "acs": acs,
        "checks": checks,
        "per_check": per_check,
        "untagged": [a for a in acs if not tags[a]],
        "out_of_scope": [c for c in checks if per_check[c] == 0 and c in out_of_scope],
        "uncovered": [c for c in checks if per_check[c] == 0 and c not in out_of_scope],
        "budget": budget,
        "over": max(0, len(acs) - budget) if budget is not None else 0,
    }


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def summary_line(m) -> str:
    budget = f"budget {m['budget']}" if m["budget"] is not None else "no budget"
    acs = f"{_plural(len(m['acs']), 'AC')} ({budget})."
    if m["checks"]:
        parts = []
        for c, n in m["per_check"].items():
            if c not in m["checks"]:
                parts.append(f"{c} (not in the source): {n}")
            elif c in m["out_of_scope"]:
                parts.append(f"{c}: {n} (out of scope)")
            else:
                parts.append(f"{c}: {n}")
        if m["untagged"]:
            parts.append(f"no C-ID: {len(m['untagged'])}")
        line = f"{_plural(len(m['checks']), 'source check')} → {acs} {', '.join(parts)}."
    else:
        line = acs
    if m["uncovered"]:
        line += f" Uncovered checks: {', '.join(m['uncovered'])}."
    if m["over"]:
        line += f" Over budget by {m['over']}."
    return line


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="spec_size.py")
    parser.add_argument("--project-dir")
    args = parser.parse_args(argv)
    project = args.project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        m = measure(project)
    except FileNotFoundError:
        print("specforge: no .spec/spec.md to measure yet. Run /specforge:spec first.")
        return 1
    except config.ConfigError as e:
        print(f"specforge: .spec/config.json is invalid ({e}).")
        return 1
    print(summary_line(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
