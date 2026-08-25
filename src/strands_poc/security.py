"""WorkspaceSandboxHook — Strands ``HookProvider`` enforcing the
workspace sandbox for write-class tool calls.

Strands's official "veto a tool call" pattern:

    event.cancel_tool = "reason"

This is a one-line veto and is *cleaner than* rewriting tools or
registering heavy security analyzers. The agent receives the
cancellation reason as the tool result and must pick another approach
— matching the semantics of the current
``claude-agent/src/agent/agent.py:_is_safe_write_path`` PreToolUse block.
"""

from __future__ import annotations

from pathlib import Path

from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry


class WorkspaceSandboxHook(HookProvider):
    """Block write/edit tool calls whose target path is outside workspace.

    The write/edit tools themselves do NOT enforce the sandbox — that is
    delegated here so the policy is in one place and the tools stay
    unit-testable in isolation.
    """

    _WRITE_TOOLS = frozenset({"write", "edit"})

    def __init__(self, workspace: Path):
        self._workspace = workspace.resolve()

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool)

    def before_tool(self, event: BeforeToolCallEvent) -> None:
        # Strands's BeforeToolCallEvent carries a ``tool_use`` dict-like
        # with ``name`` and ``input`` fields. The exact attribute name
        # (``tool_use`` vs ``tool``) may vary across SDK versions; we
        # accept either to be defensive.
        tool_use = getattr(event, "tool_use", None) or getattr(event, "tool", None) or {}
        tool_name = (tool_use.get("name") if hasattr(tool_use, "get") else "") or ""
        if tool_name not in self._WRITE_TOOLS:
            return

        tool_input = tool_use.get("input") if hasattr(tool_use, "get") else {}
        if not isinstance(tool_input, dict):
            tool_input = {}
        file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
        if not file_path:
            return  # No path info; let the tool fail downstream.

        allowed, reason = self._is_safe(file_path)
        if not allowed:
            # Strands's official veto pattern — single-line cancellation.
            event.cancel_tool = f"Refusing {tool_name}: {reason}"

    def _is_safe(self, raw_path: str) -> tuple[bool, str]:
        """Mirror of claude-agent's _is_safe_write_path logic."""
        if not raw_path:
            return False, "empty path"
        try:
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = (self._workspace / candidate).resolve()
            else:
                candidate = candidate.resolve()
            candidate.relative_to(self._workspace)
            return True, ""
        except ValueError:
            return False, f"path outside workspace ({raw_path!r})"
        except OSError as e:
            return False, f"path resolution failed: {e}"