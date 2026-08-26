"""Custom tools for the Strands PoC.

Uses Strands's native ``@tool`` decorator to register 7 custom tools
that mirror the Claude SDK era names (``Read`` / ``Glob`` / ``Grep`` /
``Write`` / ``Edit``) plus two index-oriented helpers (``file_tree``,
``outline``). The workspace sandbox is **not** baked into these tools —
``WorkspaceSandboxHook`` (see ``security.py``) handles it at the SDK
level via ``BeforeToolCallEvent.cancel_tool``, which is Strands's
official "veto a tool call" pattern.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import re
from pathlib import Path
from typing import Any

from strands import tool


# --------------------------------------------------------------------- #
# ReadTool — offset / limit / max_bytes / binary detection
# --------------------------------------------------------------------- #
def _resolve_target(workspace: Path, file_path: str) -> Path:
    p = Path(file_path)
    return p if p.is_absolute() else (workspace / p)


def _looks_binary(data: bytes, *, sample: int = 8192) -> bool:
    """Detect binary by NUL bytes in the first ``sample`` bytes."""
    if not data:
        return False
    chunk = data[:sample]
    return b"\x00" in chunk


def make_read_tool(workspace: Path):
    @tool(name="read", description=(
        "Read a file. Path is relative to the workspace unless absolute. "
        "Optional: offset (1-based start line), limit (max lines), "
        "max_bytes (cap on bytes returned). Binary files return a sentinel "
        "'[binary, N bytes]' without raising."
    ))
    def read_tool(
        file_path: str,
        offset: int = 0,
        limit: int = 0,
        max_bytes: int = 0,
    ) -> str:
        target = _resolve_target(workspace, file_path)
        try:
            raw = target.read_bytes()
        except FileNotFoundError:
            return f"[ERROR] file not found: {file_path}"
        except OSError as e:
            return f"[ERROR] {e}"

        if _looks_binary(raw):
            return f"[binary, {len(raw)} bytes] (use outline for structure)"

        text = raw.decode("utf-8", errors="replace")

        # Slice by line range if offset/limit given.
        if offset or limit:
            lines = text.splitlines(keepends=False)
            start = max(0, (offset or 1) - 1) if offset else 0
            end = start + limit if limit else len(lines)
            text = "\n".join(lines[start:end])

        # Truncate by byte count last (with a hint so the agent knows).
        if max_bytes and len(text.encode("utf-8")) > max_bytes:
            encoded = text.encode("utf-8")[:max_bytes]
            text = encoded.decode("utf-8", errors="ignore")
            text += f"\n[...truncated at {max_bytes} bytes; original {len(raw)} bytes]"

        return text
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
# GrepTool — context lines + output_mode
# --------------------------------------------------------------------- #
def make_grep_tool(workspace: Path):
    @tool(name="grep", description=(
        "Search file contents with a Python regex. Returns "
        "'path:lineno: line' rows by default. Optional context=N adds N "
        "lines before/after each match (each context row is prefixed "
        "with a single '-' so the model can tell them apart from primary "
        "hits). output_mode='files_with_matches' returns just file paths; "
        "output_mode='count' returns '<path>:<N>'. Capped at 200 hits."
    ))
    def grep_tool(
        pattern: str,
        path: str = "",
        glob_filter: str = "*",
        context: int = 0,
        output_mode: str = "content",
    ) -> str:
        if not pattern:
            return "[ERROR] pattern is required"
        if output_mode not in ("content", "files_with_matches", "count"):
            return f"[ERROR] invalid output_mode: {output_mode!r}"
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"[ERROR] invalid regex: {e}"

        base_dir = (
            workspace / path
            if path and not Path(path).is_absolute()
            else (Path(path) if path else workspace)
        )

        matched_paths: set[str] = set()
        rows: list[str] = []

        for p in sorted(base_dir.rglob(glob_filter)):
            if not p.is_file() or not fnmatch.fnmatch(p.name, glob_filter):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                rel = p.relative_to(workspace).as_posix()
            except ValueError:
                rel = str(p)

            lines = text.splitlines()
            file_match_count = 0

            if output_mode == "files_with_matches":
                if any(rx.search(line) for line in lines):
                    matched_paths.add(rel)
                continue

            if output_mode == "count":
                file_match_count = sum(1 for line in lines if rx.search(line))
                if file_match_count:
                    rows.append(f"{rel}:{file_match_count}")
                continue

            # content mode (default), with optional context
            for lineno, line in enumerate(lines, start=1):
                if rx.search(line):
                    rows.append(f"{rel}:{lineno}: {line}")
                    file_match_count += 1
                    if context > 0:
                        lo = max(0, lineno - 1 - context)
                        hi = min(len(lines), lineno - 1 + context + 1)
                        for delta, ctx_line in enumerate(lines[lo:hi], start=lo - lineno + 1):
                            if delta == 0:
                                continue  # the match line already printed
                            rows.append(f"{rel}:{lineno + delta}- {ctx_line}")
                    if len(rows) >= 200:
                        break
            if len(rows) >= 200:
                break

        if output_mode == "files_with_matches":
            if not matched_paths:
                return f"[no matches for /{pattern}/ under {path or '.'}]"
            return "\n".join(sorted(matched_paths))

        if output_mode == "count":
            if not rows:
                return f"[no matches for /{pattern}/ under {path or '.'}]"
            return "\n".join(rows)

        if not rows:
            return f"[no matches for /{pattern}/ under {path or '.'}]"
        return "\n".join(rows[:200])
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
        target = _resolve_target(workspace, file_path)
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
        target = _resolve_target(workspace, file_path)
        src = target.read_text(encoding="utf-8")
        replaced = src.replace(old_string, new_string)
        if replaced == src:
            return f"[no match for old_string in {file_path}]"
        target.write_text(replaced, encoding="utf-8")
        return f"Edited {file_path} ({len(new_string)} chars)"
    return edit_tool


# --------------------------------------------------------------------- #
# FileTreeTool — JSON tree, cheap pre-read orientation
# --------------------------------------------------------------------- #
def build_file_tree_entries(root: Path, max_depth: int) -> list[dict[str, Any]]:
    """Canonical walker — used by ``make_file_tree_tool`` and ``ProjectIndex``."""
    entries: list[dict[str, Any]] = []

    def _walk(p: Path, depth: int, prefix: str) -> None:
        if depth < 0:
            return
        try:
            children = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except OSError as e:
            entries.append({"path": prefix, "type": "dir", "size": None, "error": str(e)})
            return
        for child in children:
            rel = f"{prefix}/{child.name}" if prefix else child.name
            if child.is_dir():
                entries.append({"path": rel, "type": "dir", "size": None})
                _walk(child, depth - 1, rel)
            else:
                try:
                    sz = child.stat().st_size
                except OSError:
                    sz = None
                entries.append({"path": rel, "type": "file", "size": sz})

    _walk(root, max_depth, "")
    return entries


def make_file_tree_tool(workspace: Path):
    @tool(name="file_tree", description=(
        "List files and directories under the workspace as a JSON list. "
        "Each entry has 'path' (relative POSIX), 'type' ('file'|'dir'), "
        "and 'size' (bytes; null for dirs). max_depth limits recursion "
        "(default 4). Cheap: does NOT read file contents."
    ))
    def file_tree_tool(max_depth: int = 4, path: str = "") -> str:
        base = _resolve_target(workspace, path) if path else workspace
        if not base.exists():
            return f"[ERROR] path not found: {path or '.'}"
        entries = build_file_tree_entries(base, max_depth)
        return json.dumps(entries, ensure_ascii=False, indent=2)
    return file_tree_tool


# --------------------------------------------------------------------- #
# OutlineTool — AST-based def extraction for Python
# --------------------------------------------------------------------- #
def make_outline_tool(workspace: Path):
    @tool(name="outline", description=(
        "Extract the structural outline of a Python file: module "
        "docstring, top-level classes (with method names) and functions. "
        "Returns a compact markdown-like summary. For non-Python files, "
        "returns the first 30 lines."
    ))
    def outline_tool(file_path: str) -> str:
        target = _resolve_target(workspace, file_path)
        try:
            text = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return f"[ERROR] file not found: {file_path}"
        except OSError as e:
            return f"[ERROR] {e}"

        if not file_path.endswith(".py"):
            lines = text.splitlines()[:30]
            return "\n".join(f"L{i + 1}: {line}" for i, line in enumerate(lines))

        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            return f"[ERROR] python syntax error: {e}"

        out: list[str] = []
        # Module docstring
        if (tree.body and isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant)
                and isinstance(tree.body[0].value.value, str)):
            out.append(f'""" {tree.body[0].value.value.strip()} """')

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                bases = ", ".join(ast.unparse(b) for b in node.bases)
                out.append(f"class {node.name}({bases}):")
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        deco = ""
                        if item.decorator_list:
                            deco = "  # decorated"
                        out.append(f"  def {item.name}(...){deco}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append(f"def {node.name}(...)")
            elif isinstance(node, ast.Assign):
                # Show module-level constant names only (avoids dumping huge values).
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        out.append(f"{t.id} = ...")

        return "\n".join(out) if out else "[no top-level defs]"
    return outline_tool


# --------------------------------------------------------------------- #
# Public factory
# --------------------------------------------------------------------- #
_FACTORIES = {
    "read": make_read_tool,
    "glob": make_glob_tool,
    "grep": make_grep_tool,
    "write": make_write_tool,
    "edit": make_edit_tool,
    "file_tree": make_file_tree_tool,
    "outline": make_outline_tool,
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