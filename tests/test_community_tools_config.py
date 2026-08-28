"""Tests for strands_poc.community_tools module.

Pins two contracts that were silently violated:
  1. ``CommunityToolsConfig`` construction must not mutate ``os.environ``
     as a side effect — the env var flip must happen at tool-load time.
  2. The module must not import ``dataclass`` twice (a leftover that
     obscures the real module structure).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def isolated_bypass(monkeypatch: pytest.MonkeyPatch):
    """Capture and restore BYPASS_TOOL_CONSENT around the test."""
    monkeypatch.delenv("BYPASS_TOOL_CONSENT", raising=False)
    yield


def test_constructing_config_does_not_set_bypass_env(isolated_bypass):
    """Constructing a CommunityToolsConfig must not flip BYPASS_TOOL_CONSENT.

    Before the fix, ``__post_init__`` set ``BYPASS_TOOL_CONSENT=true``
    as a side effect of constructing the config object. That meant any
    test, demo, or downstream import that built a config mutated global
    process state — which leaked across unrelated code paths.
    """
    from strands_poc.community_tools import CommunityToolsConfig

    # Construction must NOT touch the env.
    CommunityToolsConfig()
    assert os.environ.get("BYPASS_TOOL_CONSENT") is None, (
        "CommunityToolsConfig.__init__ must not mutate os.environ; "
        "the bypass flag should be applied at tool-load time."
    )


def test_constructing_config_with_bypass_false_does_not_set_env(isolated_bypass):
    """Even with bypass_consent=True the config alone must not set the env.

    The env mutation belongs to the load step, not the config object's
    ``__post_init__``. This test makes the contract explicit.
    """
    from strands_poc.community_tools import CommunityToolsConfig

    cfg = CommunityToolsConfig(bypass_consent=True)
    _ = cfg  # silence unused warning
    assert os.environ.get("BYPASS_TOOL_CONSENT") is None


def test_module_does_not_duplicate_dataclass_import():
    """The module must not import ``dataclass`` twice at module scope.

    Defensive check: catches the kind of leftover-import that appears
    when a dataclass is added later without removing the earlier
    import. The module imports ``from dataclasses import dataclass,
    field`` once at the top — a second bare ``from dataclasses import
    dataclass`` further down is dead noise that confuses readers.
    """
    import strands_poc.community_tools as ct
    import inspect

    src = inspect.getsource(ct)
    # Count top-level imports of dataclass (exclude re-exports).
    import_count = sum(
        1 for line in src.splitlines()
        if line.startswith("from dataclasses import")
    )
    assert import_count == 1, (
        f"community_tools.py must import dataclasses exactly once at "
        f"module scope; found {import_count} imports"
    )
