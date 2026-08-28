"""QA user-prompt content contracts.

These tests pin the **separation of concerns** between user prompts and
the agent's system prompt:

  - user prompts in ``prompts/qa*.txt`` must contain ONLY the task
    content (QA number + fault description).
  - All analysis protocol, output-format, language, and persistence
    instructions live in the system prompt (``agent.py``) so they can
    evolve independently of any single QA file.

The contracts are intentionally coarse — we do not assert exact text,
just the presence of required fields and the absence of directives that
should have moved to the system prompt.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"


def _qa_prompt_files() -> list[Path]:
    """All qa-prefixed prompt files (one per QA scenario).

    Excludes history / backup siblings (e.g. ``..._long.txt``,
    ``..._old.txt``, ``..._backup.txt``) — those are snapshots, not
    contract-bearing files.
    """
    out: list[Path] = []
    for p in sorted(PROMPTS_DIR.glob("qa*.txt")):
        if any(tag in p.stem for tag in ("_long", "_old", "_backup")):
            continue
        out.append(p)
    return out


@pytest.fixture
def qa_prompt() -> Path:
    """QA001 is the canonical scenario; load its prompt text."""
    p = PROMPTS_DIR / "qa001_concurrent_edit_analysis.txt"
    if not p.exists():
        pytest.skip(f"{p.name} not present in this checkout")
    return p


# ============================================================ #
# Group 1: presence of the two task-content markers
# ============================================================ #
def test_qa_prompt_contains_qa_id_field(qa_prompt: Path):
    """The prompt must carry the QA identifier in [#QA编号:...] form."""
    text = qa_prompt.read_text(encoding="utf-8")
    assert re.search(r"\[#QA编号[:：][^\]]*\]", text), (
        "QA prompt must contain a [#QA编号:...] field"
    )


def test_qa_prompt_contains_fault_field(qa_prompt: Path):
    """The prompt must carry the fault description in [#故障信息:...] form."""
    text = qa_prompt.read_text(encoding="utf-8")
    assert re.search(r"\[#故障信息[:：][^\]]*\]", text), (
        "QA prompt must contain a [#故障信息:...] field"
    )


# ============================================================ #
# Group 2: directives that must have moved to the system prompt
# ============================================================ #
@pytest.mark.parametrize(
    "forbidden_directive",
    [
        "分析视角",   # lives in system_prompt as ## 直接原因 / ## 根本原因 / ## 修正方案
        "输出要求",   # lives in system_prompt as HTML/中文/闭合 等
        "保存",       # lives in system_prompt as "用 write 工具把完整 HTML 写到 workspace"
        "HTML",       # lives in system_prompt as the skeleton
    ],
)
def test_qa_prompt_has_no_output_directives(qa_prompt: Path, forbidden_directive: str):
    """User prompt must not repeat what the system prompt already enforces."""
    text = qa_prompt.read_text(encoding="utf-8")
    assert forbidden_directive not in text, (
        f"QA prompt must not contain {forbidden_directive!r}; "
        "this directive belongs in the system prompt"
    )


# ============================================================ #
# Group 3: every qa*.txt file in prompts/ satisfies the contract
# ============================================================ #
@pytest.mark.parametrize("path", _qa_prompt_files())
def test_every_qa_prompt_meets_contract(path: Path):
    """Bulk guard: any new qa*.txt file is held to the same minimal shape."""
    text = path.read_text(encoding="utf-8")
    assert re.search(r"\[#QA编号[:：][^\]]*\]", text), (
        f"{path.name} missing [#QA编号:...]"
    )
    assert re.search(r"\[#故障信息[:：][^\]]*\]", text), (
        f"{path.name} missing [#故障信息:...]"
    )
    for forbidden in ("分析视角", "输出要求", "HTML"):
        assert forbidden not in text, (
            f"{path.name} still contains {forbidden!r} "
            "(this directive belongs in the system prompt)"
        )
