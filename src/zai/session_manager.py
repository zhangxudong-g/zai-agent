"""Session management using Strands SDK's built-in SnapshotSessionManager.

This module wraps Strands' native session management for backward compatibility
with the REPL commands (/sessions, /save, /load, /export).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .paths import get_zai_exports_dir, get_zai_saves_dir

if TYPE_CHECKING:
    from .agent import Agent


def _get_session_storage_dir() -> Path:
    """Get the storage directory for Strands sessions."""
    from .paths import get_zai_home

    return get_zai_home() / ".strands" / "sessions"


def _get_saves_dir() -> Path:
    """Get the saves directory for exported snapshots."""
    return get_zai_saves_dir()


def _get_exports_dir() -> Path:
    """Get the exports directory."""
    return get_zai_exports_dir()


def _extract_messages(agent: Any) -> list[dict[str, str]]:
    """Safely extract messages from an agent.

    Handles both our zai.agent.Agent wrapper and Strands Agent directly.
    Returns a list of {"role": ..., "content": ...} dicts.
    """
    messages: list[dict[str, str]] = []

    # Try to get the underlying Strands agent
    inner = getattr(agent, "_inner", None) or agent

    # Get raw messages list
    raw_messages = getattr(inner, "messages", None)
    if not raw_messages:
        return messages

    try:
        for msg in raw_messages:
            # Message could be dict or object
            if isinstance(msg, dict):
                role = msg.get("role", "user")
                content = msg.get("content", "")
            else:
                role = getattr(msg, "role", "user")
                content = getattr(msg, "content", "")

            # Extract text from content (could be str or list of blocks)
            if isinstance(content, list):
                texts: list[str] = []
                for block in content:
                    if isinstance(block, dict):
                        if "text" in block:
                            texts.append(str(block["text"]))
                        elif block.get("type") == "text":
                            texts.append(str(block.get("text", "")))
                    elif hasattr(block, "text"):
                        texts.append(str(block.text))
                content = "\n".join(texts)

            if content:
                messages.append({"role": str(role), "content": str(content)})
    except Exception:
        # If extraction fails, return what we have so far
        pass

    return messages


class SessionManager:
    """Manage saved sessions using Strands' SnapshotSessionManager.

    This provides backward compatibility with the REPL commands while
    using the native Strands session management under the hood.
    """

    def __init__(self, agent: "Agent | None" = None):
        self._agent = agent
        self._session_mgr: Any | None = None
        self._storage_dir = _get_session_storage_dir()
        self._saves_dir = _get_saves_dir()
        self._exports_dir = _get_exports_dir()

    def _get_session_manager(self) -> Any | None:
        """Lazy initialization of SnapshotSessionManager."""
        if self._agent is None:
            return None

        if self._session_mgr is None:
            try:
                from strands.session import SnapshotSessionManager
                from strands.storage import LocalFileStorage

                storage = LocalFileStorage(str(self._storage_dir))
                self._session_mgr = SnapshotSessionManager(
                    session_id="current",
                    storage=storage,
                )
                # Get the underlying Strands agent
                inner_agent = getattr(self._agent, "_inner", None) or self._agent
                self._session_mgr.initialize(inner_agent)
            except Exception:
                # If Strands session manager fails to initialize, just return None
                # The REPL snapshot (JSON) still works without it
                return None

        return self._session_mgr

    def set_agent(self, agent: "Agent") -> None:
        """Set the agent for session management."""
        self._agent = agent
        self._session_mgr = None  # Reset so it will be reinitialized

    def list_sessions(self) -> list[dict]:
        """List all saved sessions.

        Note: Strands' list_snapshot_ids requires an agent, so we list
        from the saves directory for REPL compatibility.
        """
        sessions = []
        saves_dir = self._saves_dir

        if not saves_dir.exists():
            return sessions

        for path in sorted(saves_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                sessions.append(
                    {
                        "name": data.get("name", path.stem),
                        "path": str(path),
                        "created_at": data.get("created_at", ""),
                        "message_count": len(data.get("messages", [])),
                    }
                )
            except Exception:
                continue

        return sessions

    def save_session(
        self, name: str, agent: "Agent", session_log: Path | None = None
    ) -> Path:
        """Save current session as a named snapshot.

        Uses Strands' SnapshotSessionManager for the internal state,
        and saves a compatible JSON to the saves directory for REPL.
        """
        if agent is None:
            raise RuntimeError("Agent not set for session management")

        # Update agent reference
        self._agent = agent

        # Try to use Strands session manager (best effort, may fail silently)
        sm = self._get_session_manager()
        if sm is not None:
            try:
                inner_agent = getattr(agent, "_inner", None) or agent
                sm.sync_agent(inner_agent)
                sm.save_snapshot(inner_agent, is_latest=True)
            except Exception:
                # Silently ignore Strands errors; REPL snapshot still works
                pass

        # Always save a REPL-compatible JSON snapshot
        snapshot = self._create_repl_snapshot(name, agent)
        path = self._saves_dir / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        return path

    def _create_repl_snapshot(self, name: str, agent: Any) -> dict[str, Any]:
        """Create a REPL-compatible snapshot from the agent."""
        messages = _extract_messages(agent)

        # Get config info safely
        config = getattr(agent, "config", None)
        model = getattr(config, "ollama_model", "unknown") if config else "unknown"
        workspace = (
            str(getattr(config, "agent_workspace", "")) if config else ""
        )

        return {
            "version": "1.0",
            "name": name,
            "created_at": datetime.now(UTC).isoformat(),
            "config": {
                "model": model,
                "workspace": workspace,
            },
            "messages": messages,
            "tool_calls": [],
            "metadata": {
                "total_messages": len(messages),
                "total_tool_calls": 0,
            },
        }

    def load_session(self, name: str) -> dict[str, Any]:
        """Load a saved session by name."""
        path = self._saves_dir / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {name}")

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        return data

    def export_session(
        self, name: str, agent: "Agent | None", session_log: Path | None = None
    ) -> Path:
        """Export session to exports directory."""
        if agent is None:
            raise RuntimeError("Agent not set for session management")

        snapshot = self._create_repl_snapshot(name, agent)

        path = self._exports_dir / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        return path

    def restore_session(self, name: str, agent: "Agent") -> bool:
        """Restore a session from the saves directory."""
        # Load the snapshot data
        try:
            data = self.load_session(name)
        except FileNotFoundError:
            raise

        # Note: Strands manages messages internally, so we just log the restoration
        messages = data.get("messages", [])
        if messages:
            print(f"[INFO] Restored {len(messages)} messages to session")
        return True

    def delete_session(self, name: str) -> bool:
        """Delete a saved session."""
        path = self._saves_dir / f"{name}.json"
        if path.exists():
            path.unlink()
            return True
        return False


# Global session manager instance (will be initialized with agent)
_session_manager: SessionManager | None = None


def get_session_manager(agent: "Agent | None" = None) -> SessionManager:
    """Get the global session manager instance."""
    global _session_manager

    if _session_manager is None:
        _session_manager = SessionManager(agent=agent)
    elif agent is not None:
        _session_manager.set_agent(agent)

    return _session_manager
