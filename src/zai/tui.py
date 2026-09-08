"""Terminal UI helpers for Zai Agent.

Provides:
- ANSI color shortcuts (info, success, warn, error, tool, thinking)
- Markdown rendering via Rich (if available)
- Progress spinner for long-running operations
- Box-drawing helper for multi-line banners

Design principles
=================
- **Zero deps default**: All helpers fall back to plain text if Rich is missing.
- **Crash-proof**: All writes use ``sys.stdout.reconfigure(errors="replace")``-style safety
  (see ``_harden_stdio`` in ``main.py``).
- **Idempotent imports**: Safe to import multiple times.
"""

from __future__ import annotations

import contextlib
import itertools
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# ---- ANSI color helpers ----------------------------------------------------

_ENABLE_COLOR = True  # flip to False to disable globally


def _ansi(code: str) -> str:
    return code if _ENABLE_COLOR and sys.stdout.isatty() else ""


def c_red(s: str) -> str:
    return f"\033[31m{s}\033[0m"


def c_green(s: str) -> str:
    return f"\033[32m{s}\033[0m"


def c_yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m"


def c_blue(s: str) -> str:
    return f"\033[34m{s}\033[0m"


def c_magenta(s: str) -> str:
    return f"\033[35m{s}\033[0m"


def c_cyan(s: str) -> str:
    return f"\033[36m{s}\033[0m"


def _cyan(s: str) -> str:
    """Internal: returns the ANSI-cyan-wrapped string (always)."""
    return f"\033[36m{s}\033[0m"


def c_dim(s: str) -> str:
    return f"\033[2m{s}\033[0m"


def c_bold(s: str) -> str:
    return f"\033[1m{s}\033[0m"


# ---- Rich availability check ----------------------------------------------

try:
    from rich.console import Console
    from rich.markdown import Markdown

    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False


def render_markdown(text: str) -> None:
    """Render markdown text to terminal.

    Falls back to plain text if Rich is not installed.
    """
    if _HAS_RICH:
        console = Console(highlight=False, soft_wrap=True)
        console.print(Markdown(text))
    else:
        sys.stdout.write(text)
        sys.stdout.write("\n")
        sys.stdout.flush()


# ---- Spinner for long-running operations ----------------------------------

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Spinner:
    """Simple threaded spinner for long operations.

    Usage:
        with Spinner("处理中"):
            do_long_running_thing()
    """

    def __init__(self, message: str = "处理中", interval: float = 0.1):
        self.message = message
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        frames = itertools.cycle(_SPINNER_FRAMES)
        while not self._stop.is_set():
            frame = next(frames)
            sys.stdout.write(f"\r  \033[36m{frame}\033[0m {self.message}...")
            sys.stdout.flush()
            self._stop.wait(self.interval)
        # Clear the spinner line
        sys.stdout.write("\r" + " " * (len(self.message) + 12) + "\r")
        sys.stdout.flush()

    def __enter__(self) -> Spinner:
        # Only start spinner if stdout is a TTY (skip in scripts/pipes)
        if sys.stdout.isatty():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=0.5)


# ---- Banner ---------------------------------------------------------------


def print_banner_v2(
    model: str,
    workspace: Path,
    session_id: str,
    log_file: Path | None = None,
    zai_home: Path | None = None,
    interactive: bool = False,
    extra_parts: list[str] | None = None,
) -> None:
    """Print startup banner with nice formatting.

    Layout::

        ╭─ zai · <model> · <workspace> · session <id>
        ╰─ 📁 <zai_home>  📝 <log-file>  ──  /help /clear /exit
    """
    title = f"zai · {model} · {workspace} · session {session_id}"
    print(f"╭─ {c_cyan(title)}")

    line2: list[str] = []
    if zai_home is not None:
        line2.append(f"📁 {c_dim(str(zai_home))}")
    if log_file is not None:
        line2.append(f"📝 {c_dim(str(log_file))}")
    if extra_parts:
        line2.extend(extra_parts)
    if interactive:
        line2.append(c_dim("/help /clear /exit"))

    if line2:
        joined = "  ".join(line2)
        print(f"╰─ {joined}")
    print()


def print_box(
    title: str,
    lines: list[str],
    *,
    color: str = "cyan",
    width: int = 60,
) -> None:
    """Print a bordered box with title and lines.

    Example::

        ╭─ Zai Agent ────────────────────────
        │ /help   显示帮助
        │ /clear  清屏
        ╰────────────────────────────────────
    """
    color_fn = {
        "red": c_red,
        "green": c_green,
        "yellow": c_yellow,
        "blue": c_blue,
        "cyan": c_cyan,
        "magenta": c_magenta,
    }.get(color, c_cyan)

    title_part = title
    # Title line: ╭─ <title> <...padding...>
    title_len = 2 + len(title_part)  # "─ " + title
    pad_len = max(1, width - title_len - 1)  # -1 for the trailing "─"
    top_line = f"╭─ {title_part} " + "─" * pad_len
    print(color_fn(top_line))

    for line in lines:
        # Pad each line to fit the box width
        # Calculate display width (rough, ignores wide chars)
        display_len = len(line)
        # Truncate if too long
        if display_len > width - 3:
            line = line[: width - 3 - 1] + "…"
            display_len = len(line)
        pad = " " * max(1, width - display_len - 3)
        print(f"│ {line}{pad}")

    bottom = "╰" + "─" * (width - 1)
    print(color_fn(bottom))


# ---- Tool call display -----------------------------------------------------


def print_tool_start(name: str, args: dict | None) -> None:
    """Print a compact tool-call line."""
    formatted = _format_tool_args(name, args)
    print(f"  \033[36m🔧 {formatted}\033[0m", flush=True)


def print_tool_result(
    name: str,
    result: str,
    *,
    is_error: bool = False,
    max_lines: int = 12,
    max_chars: int = 500,
) -> None:
    """Print a compact tool result, truncating if too long."""
    label = "✓" if not is_error else "✗"
    color = c_green if not is_error else c_red

    print(f"  {color(label)} {c_dim(name)}", flush=True)

    text = result.strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"

    for i, line in enumerate(text.splitlines()):
        if i >= max_lines:
            remaining = len(text.splitlines()) - max_lines
            print(f"    {c_dim(f'… ({remaining} more lines)')}")
            break
        print(f"    {line}")


def print_thinking(text: str, *, max_chars: int = 80) -> None:
    """Print model thinking text, truncated."""
    preview = text[:max_chars]
    if len(text) > max_chars:
        preview += "…"
    print(f"\033[33m🤔 {preview}\033[0m", flush=True)


def print_done(elapsed: float) -> None:
    """Print the success completion line."""
    print(f"\n\033[32m✓ {elapsed:.1f}s\033[0m", flush=True)


def print_error(message: str) -> None:
    """Print an error line."""
    print(f"\n\033[31m❌ {message}\033[0m", file=sys.stderr, flush=True)


def print_warn(message: str) -> None:
    """Print a warning line."""
    print(f"\033[33m⚠ {message}\033[0m", file=sys.stderr, flush=True)


def print_info(message: str) -> None:
    """Print an info line."""
    print(f"\033[36mℹ {message}\033[0m", flush=True)


# ---- Helpers ---------------------------------------------------------------


def _format_tool_args(name: str, args: dict | None) -> str:
    """Compact single-line tool-call display.

    Display rules:
    - ``shell``: skip tool name, show only the command (it's self-evident)
    - single-arg tools (``read``/``write``/``edit``/``outline`` etc.):
      ``<tool> <arg-value>``
    - multi-arg tools (``grep`` etc.): ``<tool> key=val key=val``
    - Long values truncated with ``…``
    """
    if not args:
        return name
    if name == "shell":
        cmd = str(args.get("command", ""))
        if len(cmd) > 100:
            cmd = cmd[:98] + "…"
        return cmd
    if len(args) == 1:
        v_str = str(next(iter(args.values())))
        if len(v_str) > 100:
            v_str = v_str[:98] + "…"
        return f"{name} {v_str}"
    parts: list[str] = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 40:
            v_str = v_str[:38] + "…"
        parts.append(f"{k}={v_str!r}")
    return f"{name} " + " ".join(parts)


@contextlib.contextmanager
def status(message: str) -> Iterator[None]:
    """Context manager that shows a spinner while work is happening.

    Usage::

        with status("Thinking"):
            result = expensive_call()
    """
    with Spinner(message):
        yield
