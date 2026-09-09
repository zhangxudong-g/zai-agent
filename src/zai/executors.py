"""Tool executors using Strands' built-in ConcurrentToolExecutor and SequentialToolExecutor.

Provides a simple way to switch between sequential (default, safer) and
concurrent (faster) tool execution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from strands.tools.executors import ToolExecutor


def build_concurrent_executor() -> ToolExecutor:
    """Build a concurrent tool executor.

    Returns:
        ToolExecutor that runs multiple tool calls in parallel.
    """
    from strands.tools.executors import ConcurrentToolExecutor

    return ConcurrentToolExecutor()


def build_sequential_executor() -> ToolExecutor:
    """Build a sequential tool executor.

    Returns:
        ToolExecutor that runs tool calls one at a time (default).
    """
    from strands.tools.executors import SequentialToolExecutor

    return SequentialToolExecutor()


def get_executor(mode: str = "sequential") -> ToolExecutor:
    """Get a tool executor by mode name.

    Args:
        mode: Either "sequential" (default) or "concurrent"

    Returns:
        Configured tool executor

    Raises:
        ValueError: If mode is not recognized
    """
    mode = mode.lower().strip()
    if mode == "concurrent":
        return build_concurrent_executor()
    elif mode == "sequential":
        return build_sequential_executor()
    else:
        raise ValueError(f"Unknown executor mode: {mode!r}. Use 'sequential' or 'concurrent'.")
