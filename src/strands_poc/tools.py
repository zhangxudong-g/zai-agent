"""Custom tools for the Strands PoC.

Uses Strands's native ``@tool`` decorator to register 5 custom tools
that mirror the Claude SDK era names (``Read`` / ``Glob`` / ``Grep`` /
``Write`` / ``Edit``). The workspace sandbox is **not** baked into
these tools — ``WorkspaceSandboxHook`` (see ``security.py``) handles
it at the SDK level via ``BeforeToolCallEvent.cancel_tool``, which is
Strands's official "veto a tool call" pattern.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from strands import tool


# --------------------------------------------------------------------- #
# ReadTool
# --------------------------------------------------------------------- #
def make_read_tool(workspace: Path):
    @tool(name="read", description=(
        "Read the contents of a file. Path is relative to the workspace "
        "unless absolute. Returns UTF-8 text or an error string."
    ))
    def read_tool(file_path: str) -> str:
        target = workspace / file_path if not Path(file_path).is_absolute() else Path(file_path)
        try:
            return target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return f"[ERROR] file not found: {file_path}"
        except OSError as e:
            return f"[ERROR] {e}"
    return read_tool


# --------------------------------------------------------------------- #
# GlobTool
# --------------------------------------------------------------------- #
def make_glob_tool(workspace: Path):
    @tool(name="glob", description=(
        "List files under the workspace whose path matches the glob "
        "pattern (e.g. 'src/**/*.py'). Returns paths separated by newlines."
    ))
    def glob_tool(pattern: str) -> str:
        results: list[str] = []
        for path in workspace.rglob(pattern):
            if path.is_file():
                try:
                    rel = path.relative_to(workspace).as_posix()
                except ValueError:
                    rel = str(path)
                results.append(rel)
        if not results:
            return f"[no matches for {pattern!r}]"
        return "\n".join(sorted(results))
    return glob_tool


# --------------------------------------------------------------------- #
# GrepTool
# --------------------------------------------------------------------- #
def make_grep_tool(workspace: Path):
    @tool(name="grep", description=(
        "Search file contents with a Python regex. Returns "
        "'path:lineno: line' rows, capped at 200 hits."
    ))
    def grep_tool(pattern: str, path: str = "", glob_filter: str = "*") -> str:
        if not pattern:
            return "[ERROR] pattern is required"
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"[ERROR] invalid regex: {e}"

        base_dir = workspace / path if path and not Path(path).is_absolute() else (Path(path) if path else workspace)

        hits: list[str] = []
        for p in base_dir.rglob(glob_filter):
            if not p.is_file():
                continue
            if not fnmatch.fnmatch(p.name, glob_filter):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    try:
                        rel = p.relative_to(workspace).as_posix()
                    except ValueError:
                        rel = str(p)
                    hits.append(f"{rel}:{lineno}: {line}")
        if not hits:
            return f"[no matches for /{pattern}/ under {path or '.'}]"
        return "\n".join(hits[:200])
    return grep_tool


# --------------------------------------------------------------------- #
# WriteTool  (sandbox check is done by WorkspaceSandboxHook, not here)
# --------------------------------------------------------------------- #
def make_write_tool(workspace: Path):
    @tool(name="write", description=(
        "Write UTF-8 content to a file. Path is relative to the workspace "
        "unless absolute. Workspace enforcement is done by a hook."
    ))
    def write_tool(file_path: str, content: str) -> str:
        target = workspace / file_path if not Path(file_path).is_absolute() else Path(file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {file_path}"
    return write_tool


# --------------------------------------------------------------------- #
# EditTool  (sandbox check is done by WorkspaceSandboxHook, not here)
# --------------------------------------------------------------------- #
def make_edit_tool(workspace: Path):
    @tool(name="edit", description=(
        "Edit a file via find/replace. Returns 'No match' if old_string "
        "is not found. Workspace enforcement is done by a hook."
    ))
    def edit_tool(file_path: str, old_string: str, new_string: str) -> str:
        target = workspace / file_path if not Path(file_path).is_absolute() else Path(file_path)
        src = target.read_text(encoding="utf-8")
        replaced = src.replace(old_string, new_string)
        if replaced == src:
            return f"[no match for old_string in {file_path}]"
        target.write_text(replaced, encoding="utf-8")
        return f"Edited {file_path} ({len(new_string)} chars)"
    return edit_tool


# --------------------------------------------------------------------- #
# Public factory
# --------------------------------------------------------------------- #
_FACTORIES = {
    "read": make_read_tool,
    "glob": make_glob_tool,
    "grep": make_grep_tool,
    "write": make_write_tool,
    "edit": make_edit_tool,
}


def build_tools(config) -> list:
    """Build the tool list based on ``config.allowed_tools``.

    Returns the list of Strands ``Tool`` instances ready to be passed
    to ``Agent(tools=[...])``.
    """
    workspace = config.agent_workspace
    out: list = []
    for name in config.allowed_tools:
        factory = _FACTORIES.get(name.lower())
        if factory is not None:
            out.append(factory(workspace))
    return out