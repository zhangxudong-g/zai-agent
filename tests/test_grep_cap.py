"""Grep tool — 200-hit cap contract (regression for code-review I4).

The grep tool caps returned rows at 200. The cap used to be checked
in two places (inner line loop + outer file loop), which made the
control flow confusing. This test pins the OBSERVABLE contract
(\u2264 200 rows) without caring where the break lives internally.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def test_grep_caps_at_200_hits_across_files(tmp_path: Path):
    """3 files \u00d7 100 matches = 300 potential hits \u2014 cap must kick in."""
    from strands_poc.tools import make_grep_tool

    for i in range(3):
        (tmp_path / f"f{i}.txt").write_text(
            "\n".join(f"match line {j}" for j in range(100)),
            encoding="utf-8",
        )

    grep = make_grep_tool(tmp_path)
    result = grep("match")

    rows = [r for r in result.splitlines() if r and not r.startswith("[")]
    assert len(rows) <= 200, f"grep must cap at 200 hits; got {len(rows)}"


def test_grep_returns_uncapped_when_under_limit(tmp_path: Path):
    """Under the cap, grep must return every match row."""
    from strands_poc.tools import make_grep_tool

    (tmp_path / "a.txt").write_text(
        "\n".join(f"hit {i}" for i in range(50)),
        encoding="utf-8",
    )

    grep = make_grep_tool(tmp_path)
    result = grep("hit")
    rows = [r for r in result.splitlines() if r and not r.startswith("[")]
    assert len(rows) == 50, f"under-cap grep must return all hits; got {len(rows)}"
