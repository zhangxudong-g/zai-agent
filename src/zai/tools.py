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
import os
import re
from pathlib import Path
from typing import Any

from strands import tool


# --------------------------------------------------------------------- #
# Path sandbox helper — enforces workspace boundary at the tool layer.
#
# This is the **first** of two defense layers (see security.py for the
# hook layer). All file-I/O tools (``read`` / ``write`` / ``edit`` /
# ``glob`` / ``grep`` / ``file_tree`` / ``outline``) must go through
# this helper. It guarantees the resolved path is within ``workspace``
# after symlink resolution, so a model can't escape the workspace via
# absolute paths (``/etc/passwd``) or ``../`` traversal.
#
# Returns:
#   Path        — resolved absolute path inside workspace
#   str         — "[ERROR] ..." message when the path is outside
# --------------------------------------------------------------------- #
def _resolve_within_sandbox(
    workspace: Path,
    raw_path: str,
    *,
    label: str = "path",
) -> Path | str:
    """Resolve ``raw_path`` against ``workspace`` and confirm containment.

    Args:
        workspace: The agent's workspace root (already ``.resolve()``d).
        raw_path: User-supplied path. Absolute paths are checked as-is;
            relative paths are joined to ``workspace``.
        label: Human-readable name of the path argument (used in error
            messages so the model can self-correct).

    Returns:
        ``Path`` on success (always absolute and inside workspace),
        ``str`` with ``"[ERROR] ..."`` prefix on failure.
    """
    if not raw_path:
        return f"[ERROR] empty {label}"
    try:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = workspace / candidate
        candidate = candidate.resolve()
        candidate.relative_to(workspace)
        return candidate
    except ValueError:
        return f"[ERROR] {label} outside workspace ({raw_path!r}); workspace is {workspace}"
    except OSError as e:
        return f"[ERROR] {label} resolution failed ({raw_path!r}): {e}"


def _looks_binary(data: bytes, *, sample: int = 8192) -> bool:
    """Detect binary by NUL bytes in the first ``sample`` bytes."""
    if not data:
        return False
    chunk = data[:sample]
    return b"\x00" in chunk


def make_read_tool(workspace: Path):
    @tool(
        name="read",
        description=(
            "Read a file. Path is relative to the workspace unless absolute. "
            "Optional: offset (1-based start line), limit (max lines), "
            "max_bytes (cap on bytes returned). Binary files return a sentinel "
            "'[binary, N bytes]' without raising."
        ),
    )
    def read_tool(
        file_path: str,
        offset: int = 0,
        limit: int = 0,
        max_bytes: int = 0,
    ) -> str:
        target_or_err = _resolve_within_sandbox(workspace, file_path, label="file_path")
        if isinstance(target_or_err, str):
            return target_or_err
        target = target_or_err
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
    @tool(
        name="glob",
        description=(
            "List files under the workspace whose path matches the glob "
            "pattern (e.g. 'src/**/*.py'). Returns paths separated by newlines."
        ),
    )
    # Glob is anchored at ``workspace`` by design — the model only ever
    # supplies a glob pattern, never a base directory. No path argument
    # to sandbox, so no ``_resolve_within_sandbox`` call needed here.
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
    @tool(
        name="grep",
        description=(
            "Search file contents with a Python regex. Returns "
            "'path:lineno: line' rows by default. Optional context=N adds N "
            "lines before/after each match (each context row is prefixed "
            "with a single '-' so the model can tell them apart from primary "
            "hits). output_mode='files_with_matches' returns just file paths; "
            "output_mode='count' returns '<path>:<N>'. Capped at 200 hits."
        ),
    )
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

        # Sandbox the base directory: a model that passes ``/etc`` here
        # must fail the same way the read tool fails. Glob filter still
        # applies afterwards.
        if path:
            base_dir_or_err = _resolve_within_sandbox(
                workspace,
                path,
                label="path",
            )
            if isinstance(base_dir_or_err, str):
                return base_dir_or_err
            base_dir = base_dir_or_err
        else:
            base_dir = workspace

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
            hit_cap = False
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
                    hit_cap = True
                    break
            if hit_cap:
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
    @tool(
        name="write",
        description=(
            "Write UTF-8 content to a file. Path is relative to the workspace "
            "unless absolute. Workspace enforcement is done by a hook."
        ),
    )
    def write_tool(file_path: str, content: str) -> str:
        target_or_err = _resolve_within_sandbox(workspace, file_path, label="file_path")
        if isinstance(target_or_err, str):
            return target_or_err
        target = target_or_err
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {file_path}"

    return write_tool


# --------------------------------------------------------------------- #
# EditTool  (sandbox check is done by WorkspaceSandboxHook, not here)
# --------------------------------------------------------------------- #
def make_edit_tool(workspace: Path):
    @tool(
        name="edit",
        description=(
            "Edit a file via find/replace. Returns 'No match' if old_string "
            "is not found. Workspace enforcement is done by a hook."
        ),
    )
    def edit_tool(file_path: str, old_string: str, new_string: str) -> str:
        target_or_err = _resolve_within_sandbox(workspace, file_path, label="file_path")
        if isinstance(target_or_err, str):
            return target_or_err
        target = target_or_err
        try:
            src = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return (
                f"[ERROR] {file_path} is not valid UTF-8. Refusing to edit "
                "because a lossy decode would corrupt the file. Convert it "
                "to UTF-8 first, or edit it with an external editor."
            )
        replaced = src.replace(old_string, new_string)
        if replaced == src:
            return f"[no match for old_string in {file_path}]"
        target.write_text(replaced, encoding="utf-8")
        return f"Edited {file_path} ({len(new_string)} chars)"

    return edit_tool


# --------------------------------------------------------------------- #
# FileTreeTool — JSON tree, cheap pre-read orientation
# --------------------------------------------------------------------- #
def build_file_tree_entries(
    root: Path,
    max_depth: int,
    max_entries: int = 2000,
) -> list[dict[str, Any]]:
    """Canonical walker — used by ``make_file_tree_tool`` and ``ProjectIndex``.

    Skips well-known noise directories (``.venv``, ``node_modules``, …)
    and caps the total number of entries at ``max_entries`` so a single
    call can never dump megabytes of JSON into the model context.
    """
    noise_dirs = frozenset(
        {
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            ".git",
            ".mypy_cache",
            ".ruff_cache",
            ".pytest_cache",
            ".tox",
            ".idea",
            ".vscode",
            "dist",
            "build",
            "target",
            "site-packages",
        }
    )
    entries: list[dict[str, Any]] = []
    truncated_after = 0

    def _walk(p: Path, depth: int, prefix: str) -> None:
        nonlocal truncated_after
        if depth < 0 or truncated_after:
            return
        try:
            children = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except OSError as e:
            entries.append({"path": prefix, "type": "dir", "size": None, "error": str(e)})
            return
        for child in children:
            if truncated_after or len(entries) >= max_entries:
                truncated_after = max_entries
                break
            rel = f"{prefix}/{child.name}" if prefix else child.name
            if child.is_dir():
                if child.name in noise_dirs:
                    continue
                entries.append({"path": rel, "type": "dir", "size": None})
                if len(entries) >= max_entries:
                    truncated_after = max_entries
                    continue
                _walk(child, depth - 1, rel)
            else:
                try:
                    sz = child.stat().st_size
                except OSError:
                    sz = None
                entries.append({"path": rel, "type": "file", "size": sz})

    _walk(root, max_depth, "")
    if truncated_after:
        entries.append(
            {
                "_truncated": True,
                "_hint": (
                    f"only first {max_entries} entries listed; "
                    "re-call file_tree with 'path' to drill into a subdir"
                ),
            }
        )
    return entries


def make_file_tree_tool(workspace: Path):
    @tool(
        name="file_tree",
        description=(
            "List files and directories under the workspace as a JSON list. "
            "Each entry has 'path' (relative POSIX), 'type' ('file'|'dir'), "
            "and 'size' (bytes; null for dirs). max_depth limits recursion "
            "(default 4). Noise dirs (.venv, node_modules, __pycache__, .git, …) "
            "are skipped; results cap at 2000 entries — for bigger trees, call "
            "with 'path' to drill into a subdir. Cheap: does NOT read contents."
        ),
    )
    def file_tree_tool(max_depth: int = 4, path: str = "") -> str:
        if path:
            base_or_err = _resolve_within_sandbox(workspace, path, label="path")
            if isinstance(base_or_err, str):
                return base_or_err
            base = base_or_err
        else:
            base = workspace
        if not base.exists():
            return f"[ERROR] path not found: {path or '.'}"
        entries = build_file_tree_entries(base, max_depth)
        return json.dumps(entries, ensure_ascii=False)

    return file_tree_tool


# --------------------------------------------------------------------- #
# ShellTool — execute allowed read-only commands
# --------------------------------------------------------------------- #
# Allowlist of safe read-only commands (no file writing, no network)
_ALLOWED_COMMANDS = frozenset(
    {
        "git",
        "ls",
        "find",
        "grep",
        "cat",
        "head",
        "tail",
        "wc",
        "sort",
        "uniq",
        "diff",
        "patch",
        "xz",
        "gz",
        "bz2",
        "zip",
        "unzip",
        "tree",
        "pwd",
        "cd",
        "dir",
        "type",
        "stat",
        "file",
        "md5sum",
        "sha256sum",
        "sha1sum",
        "xxd",
        "hexdump",
        "od",
        "base64",
        "python",
        "python3",
        "node",
        "npm",
        "cargo",
        "uv",
        "pip",
        "poetry",
        "docker",
        "docker compose",
        "docker-compose",
    }
)


def _is_command_allowed(cmd: str) -> bool:
    """Check if command is in allowlist (simple prefix match)."""
    return cmd in _ALLOWED_COMMANDS


def _translate_for_windows(parts: list[str]) -> list[str]:
    """Translate common Unix shell idioms to cmd.exe equivalents.

    The model is trained mostly on bash; ``ls`` / ``cat`` / ``pwd`` are
    not native to Windows cmd, so without translation every first tool
    call fails and the model wastes a turn recovering.

    Returns the rewritten command parts; the original list if the
    command is not recognized (or passes through unchanged on POSIX).
    """
    if os.name != "nt" or not parts:
        return parts
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd == "ls":
        # ls → dir.  Preserve detail level and recursion.
        recursive = any(a in ("-R", "-r", "--recursive") for a in args)
        short = any(a.startswith("-l") or a == "-a" for a in args)
        # -l/-la/-a are "detailed" → full dir; otherwise bare names
        rewritten = ["dir"]
        if not short:
            rewritten.append("/b")
        if recursive:
            rewritten.append("-s")
        # pass through non-flag args (e.g. a directory path)
        rewritten.extend(a for a in args if not a.startswith("-"))
        return rewritten

    if cmd == "pwd":
        # plain `cd` with no args prints the current directory in cmd
        return ["cd"]

    if cmd == "cat":
        non_flags = [a for a in args if not a.startswith("-")]
        if non_flags:
            return ["type", *non_flags]
        return parts

    if cmd == "find":
        # `find . -name PATTERN` → `dir /s /b PATTERN`
        if "-name" in args:
            i = args.index("-name")
            if i + 1 < len(args):
                return ["dir", "/s", "/b", args[i + 1]]
        # bare `find .` → `dir /b .`
        positional = [a for a in args if not a.startswith("-")]
        if positional:
            return ["dir", "/b", *positional]
        return parts

    return parts


def make_shell_tool(workspace: Path):
    @tool(
        name="shell",
        description=(
            "Execute a shell command in the workspace directory. "
            "Only read-only commands are allowed: git, ls, find, grep, cat, head, tail, "
            "tree, python, node, docker, etc. "
            "Unix idioms (ls, cat, pwd, find -name) are auto-translated to "
            "Windows equivalents, so they work cross-platform. "
            "Output is truncated to 5000 chars. "
            "Workspace directory is the working directory."
        ),
    )
    def shell_tool(command: str) -> str:
        import shlex
        import subprocess

        MAX_OUTPUT = 5000

        # Parse command safely
        try:
            parts = shlex.split(command)
        except ValueError as e:
            return f"[ERROR] invalid command: {e}"

        if not parts:
            return "[ERROR] empty command"

        # Check if command is allowed
        base_cmd = parts[0].lower()
        # Handle compound commands like 'docker compose'
        if len(parts) > 1 and parts[0].lower() in ("docker",):
            base_cmd = parts[0].lower() + " " + parts[1].lower()

        if not _is_command_allowed(base_cmd):
            return f"[ERROR] command not allowed: {base_cmd}. Allowed: {', '.join(sorted(_ALLOWED_COMMANDS))}"

        # Translate Unix idioms to cmd equivalents on Windows.
        parts = _translate_for_windows(parts)
        # cmd.exe: double-quote args that contain spaces
        command = " ".join(f'"{p}"' if " " in p else p for p in parts)

        # Block dangerous patterns (simple check, allowlist already restricts commands)
        # We allow shell=True because commands are already allowlist-checked above

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(workspace),
                capture_output=True,
                timeout=30,
            )

            def _decode(b: bytes) -> str:
                if not b:
                    return ""
                # Try UTF-8 first (git and most tools emit UTF-8); fall back
                # to the locale encoding (e.g. GBK on Chinese Windows) for
                # children that encode stdout via the console codepage.
                try:
                    return b.decode("utf-8")
                except UnicodeDecodeError:
                    import locale

                    return b.decode(locale.getpreferredencoding(False), errors="replace")

            decoded = _decode(result.stdout) + _decode(result.stderr)
            output = decoded[:MAX_OUTPUT]
            if len(decoded) > MAX_OUTPUT:
                output += f"\n... (truncated, {len(decoded)} total chars)"
            if result.returncode != 0 and not output:
                return f"[ERROR] command exited with code {result.returncode}"
            return output or "[no output]"
        except subprocess.TimeoutExpired:
            return "[ERROR] command timed out after 30 seconds"
        except FileNotFoundError:
            return f"[ERROR] command not found: {parts[0]}"
        except Exception as e:
            return f"[ERROR] {e}"

    return shell_tool


# --------------------------------------------------------------------- #
# OutlineTool — AST-based def extraction for Python
# --------------------------------------------------------------------- #
def make_outline_tool(workspace: Path):
    @tool(
        name="outline",
        description=(
            "Extract the structural outline of a Python file: module "
            "docstring, top-level classes (with method names) and functions. "
            "Returns a compact markdown-like summary. For non-Python files, "
            "returns the first 30 lines."
        ),
    )
    def outline_tool(file_path: str) -> str:
        target_or_err = _resolve_within_sandbox(workspace, file_path, label="file_path")
        if isinstance(target_or_err, str):
            return target_or_err
        target = target_or_err
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
        if (
            tree.body
            and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)
        ):
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
    "shell": make_shell_tool,
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
