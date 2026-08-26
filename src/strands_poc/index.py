"""Project index — small in-memory + on-disk cache for ``file_tree``.

The CodeAnalysisAgent wants to call ``file_tree`` once per session rather
than blowing tokens on each turn. ``ProjectIndex`` holds:

  - The last ``get_tree`` result
  - The mtime of every file scanned when the cache was built
  - The wall-clock time the cache was built (``ttl_seconds`` window)

On every call we recompute the workspace's max-mtime. If it differs from
the cached one (a file changed) OR the TTL window expired, we rebuild
and stamp ``last_hit_cache = False``; otherwise ``last_hit_cache = True``.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Reuse the canonical walker so ``file_tree`` tool + ``ProjectIndex`` agree.
from .tools import build_file_tree_entries


@dataclass
class ProjectIndex:
    """Workspace-level cache of ``file_tree`` results.

    Args:
        workspace: Root directory to index.
        ttl_seconds: Cache lifetime. ``0`` disables TTL (invalidation
            triggers only on mtime change).
    """

    workspace: Path
    ttl_seconds: int = 300
    last_hit_cache: bool = False
    last_rebuild_reason: str | None = None

    _cache: list[dict[str, Any]] | None = field(default=None, init=False, repr=False)
    _cache_signature: tuple[int, float] | None = field(default=None, init=False, repr=False)
    _cache_built_at: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.workspace = self.workspace.resolve()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def get_tree(self, max_depth: int = 4) -> list[dict[str, Any]]:
        """Return the cached tree (rebuilding if necessary)."""
        signature = (max_depth, self._signature_for_workspace())

        if self._cache is None:
            return self._rebuild(max_depth, signature, reason="cold")

        if (
            self._cache_signature != signature
            or (self.ttl_seconds > 0 and (time.time() - self._cache_built_at) > self.ttl_seconds)
        ):
            return self._rebuild(max_depth, signature, reason="invalidated")

        # Cache hit.
        self.last_hit_cache = True
        self.last_rebuild_reason = None
        return self._cache

    def invalidate(self) -> None:
        """Force the next ``get_tree`` call to rebuild."""
        self._cache = None
        self._cache_signature = None
        self._cache_built_at = 0.0
        self.last_rebuild_reason = "manual"

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _signature_for_workspace(self) -> float:
        """Return the max mtime across files in the workspace.

        Cheap: walks once, returns max mtime. A file's content change or
        any new file raises this value, which trips cache invalidation.
        """
        max_mtime = self.workspace.stat().st_mtime
        for root, _, files in os.walk(self.workspace):
            if "/.agent_index" in root.replace("\\", "/"):
                continue
            for name in files:
                try:
                    mt = (Path(root) / name).stat().st_mtime
                except OSError:
                    continue
                if mt > max_mtime:
                    max_mtime = mt
        return max_mtime

    def _rebuild(
        self,
        max_depth: int,
        signature: tuple[int, float],
        *,
        reason: str,
    ) -> list[dict[str, Any]]:
        entries = build_file_tree_entries(self.workspace, max_depth)
        self._cache = entries
        self._cache_signature = signature
        self._cache_built_at = time.time()
        self.last_hit_cache = False
        self.last_rebuild_reason = reason
        return entries
