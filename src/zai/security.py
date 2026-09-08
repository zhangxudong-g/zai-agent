"""WorkspaceSandboxHook — Strands ``HookProvider`` enforcing the
workspace sandbox for ALL file-I/O tool calls.

Strands's official "veto a tool call" pattern:

    event.cancel_tool = "reason"

This is the **second** of two defense layers (see ``tools.py``'s
``_resolve_within_sandbox`` for the tool-layer one). Both layers
check the same thing — path containment under workspace — so if one
layer is bypassed (e.g., a tool that's been monkey-patched), the
other still catches it.

Coverage:
  - read / glob / grep / file_tree / outline (read-class tools)
  - write / edit (write-class tools)

The hook is path-only. **Execution isolation** (preventing the agent
from running shell commands outside workspace) is the job of the SDK
``Sandbox`` (see ``sandbox.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry


class WorkspaceSandboxHook(HookProvider):
    """Block file-I/O tool calls whose target path is outside workspace.

    The tools themselves ALSO enforce the sandbox (defense in depth) via
    ``tools._resolve_within_sandbox``. The hook here is the authoritative
    layer that runs *before* the tool executes and can veto with a
    single-line ``event.cancel_tool``.
    """

    # Tools whose ``input`` carries a path field we must inspect.
    # ``glob`` is anchored at workspace by design and has no path arg,
    # so it's intentionally not in this map.
    _PATH_FIELDS: ClassVar[dict[str, str]] = {
        "read": "file_path",
        "grep": "path",  # grep base directory (optional)
        "file_tree": "path",  # file_tree base directory (optional)
        "outline": "file_path",
        "write": "file_path",
        "edit": "file_path",
    }

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
        raw_name = (tool_use.get("name") if hasattr(tool_use, "get") else "") or ""
        # Case-insensitive lookup — defends against tools registered with
        # non-conventional capitalisation (e.g., a community tool named
        # ``Read`` would otherwise bypass this hook entirely).
        tool_name = raw_name.lower()

        path_field = self._PATH_FIELDS.get(tool_name)
        if path_field is None:
            return  # Not a path-bearing tool we care about (e.g., glob).

        tool_input = tool_use.get("input") if hasattr(tool_use, "get") else {}
        if not isinstance(tool_input, dict):
            tool_input = {}

        # Some fields are optional (grep / file_tree ``path``); an empty
        # string means "use workspace root", which is safe.
        raw_path = str(tool_input.get(path_field) or "")
        if not raw_path:
            return

        allowed, reason = self._is_safe(raw_path)
        if not allowed:
            # Strands's official veto pattern — single-line cancellation.
            event.cancel_tool = f"Refusing {raw_name}: {reason}"

    def _is_safe(self, raw_path: str) -> tuple[bool, str]:
        """Mirror of claude-agent's _is_safe_write_path logic (extended to read tools)."""
        if not raw_path:
            return False, "empty path"
        try:
            candidate = Path(raw_path)
            if candidate.is_absolute():
                candidate = candidate.resolve()
            else:
                candidate = (self._workspace / candidate).resolve()
            candidate.relative_to(self._workspace)
            return True, ""
        except ValueError:
            return False, f"path outside workspace ({raw_path!r})"
        except OSError as e:
            return False, f"path resolution failed: {e}"
