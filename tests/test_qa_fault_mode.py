"""QA-fault mode tests (TDD RED→GREEN).

The agent gains a ``mode`` switch with two values:

- ``"analysis"``  — original 4-stage code-analysis protocol (REGRESSION GUARD)
- ``"qa_fault"``  — 5-stage fault analysis protocol with HTML body output

These tests pin both contracts so the two modes cannot drift. No
Ollama required; we call ``Agent._build_system_prompt(mode)`` and the
``Agent._tools`` list directly.

Key behavior under test:

  1. qa_fault prompt enumerates the 3-view output (直接原因 / 根本原因 / 修正方案)
  2. qa_fault prompt ships an HTML skeleton the model can complete
  3. qa_fault prompt has before/after change template
  4. qa_fault mode adds ``write`` + ``edit`` to the tool list
  5. analysis mode keeps its 4-section output (REGRESSION GUARD)
  6. analysis mode does NOT add ``write`` / ``edit`` to its tool list
  7. default mode is qa_fault (current customer scenario is QA001)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the package importable when pytest is invoked from project root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ============================================================ #
# Fixtures
# ============================================================ #
@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Tiny Java-like tree so file_tree/outline can be exercised if needed."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ChildService.java").write_text(
        "public class ChildService {\n"
        "    public void updateChild(Child c) { /* no lock */ }\n"
        "}\n",
        encoding="utf-8",
    )
    return tmp_path


def _make_agent(workspace: Path, mode: str | None = None):
    """Build an Agent instance WITHOUT calling __init__ (no Ollama needed).

    Populates the attrs that ``_build_system_prompt`` / ``_tools`` read so we
    can assert on the mode-dependent branches in isolation.
    """
    from strands_poc.agent import Agent
    from strands_poc.config import Config
    from strands_poc.tools import build_tools

    agent = object.__new__(Agent)
    agent.config = Config(
        ollama_base_url="http://localhost:11434",
        ollama_model="qwen3:7b",
        agent_workspace=workspace,
        session_log_dir=tmp_path_factory_session(workspace),
        allowed_tools=["read", "glob", "grep", "file_tree", "outline"],
    )
    agent.logger = None  # not exercised by _build_system_prompt
    agent._use_community_tools = False
    # The mode attribute is what we're testing — but Agent must accept it.
    if mode is not None:
        agent._mode = mode
    # Pre-build the tool list as __init__ would, so _tools assertions work.
    agent._tools = build_tools(agent.config)
    return agent


def tmp_path_factory_session(workspace: Path) -> Path:
    """Return a sibling of ``workspace`` used as SessionLogger log dir."""
    return workspace.parent / "sessions"


# ============================================================ #
# Group 1: qa_fault prompt — three views + HTML skeleton + change template
# ============================================================ #
def test_qa_fault_prompt_has_three_views(workspace: Path):
    """qa_fault prompt must list the 3 ordered views the model must produce."""
    agent = _make_agent(workspace, mode="qa_fault")
    prompt = agent._build_system_prompt(mode="qa_fault")
    for view in ("直接原因", "根本原因", "修正方案"):
        assert view in prompt, f"missing view {view!r} in qa_fault prompt"


def test_qa_fault_prompt_has_html_skeleton(workspace: Path):
    """qa_fault prompt must ship a complete HTML skeleton (doctype + body + html close)."""
    agent = _make_agent(workspace, mode="qa_fault")
    prompt = agent._build_system_prompt(mode="qa_fault")
    assert "<!DOCTYPE html>" in prompt, "qa_fault prompt must start with DOCTYPE"
    assert "</body>" in prompt, "qa_fault prompt must include </body> close"
    assert "</html>" in prompt, "qa_fault prompt must include </html> close"


def test_qa_fault_prompt_has_before_after_change_template(workspace: Path):
    """qa_fault prompt must instruct the model to emit change-before / change-after / reason."""
    agent = _make_agent(workspace, mode="qa_fault")
    prompt = agent._build_system_prompt(mode="qa_fault")
    for marker in ("变更前", "变更后", "变更理由"):
        assert marker in prompt, f"missing change-section marker {marker!r}"


def test_qa_fault_prompt_has_diagnose_stage(workspace: Path):
    """qa_fault prompt must add a Diagnose stage between Scope and Outline."""
    agent = _make_agent(workspace, mode="qa_fault")
    prompt = agent._build_system_prompt(mode="qa_fault")
    assert "Diagnose" in prompt or "诊断" in prompt, (
        "qa_fault prompt must include a Diagnose stage"
    )


# ============================================================ #
# Group 2: qa_fault mode — write/edit tools auto-added
# ============================================================ #
def test_qa_fault_mode_adds_write_and_edit_tools(workspace: Path):
    """In qa_fault mode the agent must be able to write the HTML report file."""
    agent = _make_agent(workspace, mode="qa_fault")
    agent._apply_mode_tool_policy()  # the Agent method we're testing
    names = [t.tool_name for t in agent._tools]
    assert "write" in names, f"qa_fault mode must add 'write' tool; got {names}"
    assert "edit" in names, f"qa_fault mode must add 'edit' tool; got {names}"


def test_analysis_mode_does_not_add_write_or_edit(workspace: Path):
    """analysis mode must NOT enable write/edit (regression guard)."""
    agent = _make_agent(workspace, mode="analysis")
    agent._apply_mode_tool_policy()
    names = [t.tool_name for t in agent._tools]
    assert "write" not in names, (
        f"analysis mode must keep write disabled; got {names}"
    )
    assert "edit" not in names, (
        f"analysis mode must keep edit disabled; got {names}"
    )


def test_analysis_mode_strips_write_edit_even_when_allowed(workspace: Path):
    """analysis mode must override ``allowed_tools=write,edit`` in config.

    The ``Agent`` docstring promises analysis mode is "read-only by
    default." If the user configures ``ALLOWED_TOOLS=read,write,edit``
    in ``.env``, analysis mode must still enforce read-only by stripping
    write/edit from the tool list — otherwise the docstring contract
    is silently violated.
    """
    from strands_poc.agent import Agent
    from strands_poc.config import Config
    from strands_poc.tools import build_tools

    agent = object.__new__(Agent)
    agent.config = Config(
        ollama_base_url="http://localhost:11434",
        ollama_model="qwen3:7b",
        agent_workspace=workspace,
        session_log_dir=workspace.parent / "sessions",
        # User opted write/edit INTO allowed_tools via env config.
        allowed_tools=["read", "write", "edit"],
    )
    agent._use_community_tools = False
    agent._mode = "analysis"
    agent._tools = build_tools(agent.config)

    agent._apply_mode_tool_policy()

    names = [_tool_name_proxy(t) for t in agent._tools]
    assert "write" not in names, (
        f"analysis mode must STRIP write/edit even if configured; got {names}"
    )
    assert "edit" not in names, (
        f"analysis mode must STRIP write/edit even if configured; got {names}"
    )
    # read must remain available.
    assert "read" in names, f"read must survive analysis-mode stripping; got {names}"


def _tool_name_proxy(t):
    """Compat: decorated tools have .tool_name; raw callables have __name__."""
    return getattr(t, "tool_name", None) or getattr(t, "__name__", repr(t))


# ============================================================ #
# Group 3: regression guards — analysis mode unchanged
# ============================================================ #
def test_analysis_mode_prompt_keeps_four_sections(workspace: Path):
    """analysis prompt must still contain the original 4-section template."""
    agent = _make_agent(workspace, mode="analysis")
    prompt = agent._build_system_prompt(mode="analysis")
    for section in ("## 范围", "## 证据", "## 结论", "## 不确定性"):
        assert section in prompt, f"analysis prompt missing section {section!r}"


def test_analysis_mode_prompt_keeps_four_stages(workspace: Path):
    """analysis prompt must still enumerate 4 ordered stages."""
    agent = _make_agent(workspace, mode="analysis")
    prompt = agent._build_system_prompt(mode="analysis")
    for stage in ("阶段1", "阶段2", "阶段3", "阶段4"):
        assert stage in prompt, f"analysis prompt missing stage marker {stage!r}"


# ============================================================ #
# Group 4: default mode is qa_fault (current customer scenario)
# ============================================================ #
def test_default_mode_is_qa_fault():
    """When no mode is supplied, the agent must default to qa_fault."""
    from strands_poc.agent import Agent

    sig = Agent.__init__.__code__.co_varnames
    # varnames after the first (self) include positional params; kwargs follow.
    param_index = sig.index("mode") if "mode" in sig else None
    assert param_index is not None, "Agent.__init__ must accept a `mode` parameter"
    # Default is at position (param_index - len(sig) + len(defaults))
    # Easier: inspect signature via inspect.
    import inspect

    params = inspect.signature(Agent.__init__).parameters
    assert "mode" in params, "Agent.__init__ must declare a `mode` parameter"
    assert params["mode"].default == "qa_fault", (
        f"default mode must be 'qa_fault'; got {params['mode'].default!r}"
    )


# ============================================================ #
# Group 5: _apply_mode_tool_policy is robust to non-decorated tools
# ============================================================ #
def test_apply_mode_tool_policy_does_not_crash_on_raw_callables(workspace: Path):
    """Community tools are loaded as raw callables (no `.tool_name` attr).

    _apply_mode_tool_policy used to crash with AttributeError because it
    did ``{t.tool_name for t in self._tools}``. After the fix, the
    helper must accept both shapes (decorator-wrapped ``.tool_name`` and
    raw ``.__name__``) and must NOT silently append custom write/edit
    tools into a community-tool list (which would mix two ecosystems).
    """
    from strands_poc.agent import Agent

    def fake_calculator(x: int) -> int:  # raw callable, no .tool_name
        return x * 2

    def fake_current_time() -> str:  # raw callable, no .tool_name
        return "12:00"

    agent = object.__new__(Agent)
    agent.config = None  # not exercised when community tools are in use
    agent._use_community_tools = True
    agent._mode = "qa_fault"
    agent._tools = [fake_calculator, fake_current_time]

    # Must not raise AttributeError.
    agent._apply_mode_tool_policy()

    # Must not silently inject custom write/edit tools into a community set.
    names = [
        getattr(t, "tool_name", getattr(t, "__name__", repr(t)))
        for t in agent._tools
    ]
    assert "write" not in names, (
        f"community-tool mode must not inject custom write/edit; got {names}"
    )
    assert "edit" not in names, (
        f"community-tool mode must not inject custom write/edit; got {names}"
    )
    # Original community tools must still be present (not clobbered).
    assert fake_calculator in agent._tools
    assert fake_current_time in agent._tools
