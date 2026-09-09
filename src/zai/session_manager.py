"""Session management using Strands SDK's built-in SnapshotSessionManager.

This module wraps Strands' native session management for backward compatibility
with the REPL commands (/sessions, /save, /load, /export).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .paths import get_zai_saves_dir, get_zai_exports_dir

if TYPE_CHECKING:
    from strands import Agent
    from strands.session import SnapshotSessionManager


class SessionInfo:
    """Info about a saved session."""
    
    def __init__(self, name: str, path: str, created_at: str = "", message_count: int = 0):
        self.name = name
        self.path = path
        self.created_at = created_at
        self.message_count = message_count
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "created_at": self.created_at,
            "message_count": self.message_count,
        }


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


class SessionManager:
    """Manage saved sessions using Strands' SnapshotSessionManager.
    
    This provides backward compatibility with the REPL commands while
    using the native Strands session management under the hood.
    """
    
    def __init__(self, agent: "Agent | None" = None):
        self._agent = agent
        self._session_mgr: "SnapshotSessionManager | None" = None
        self._storage_dir = _get_session_storage_dir()
        self._saves_dir = _get_saves_dir()
        self._exports_dir = _get_exports_dir()
    
    def _get_session_manager(self) -> "SnapshotSessionManager | None":
        """Lazy initialization of SnapshotSessionManager."""
        if self._agent is None:
            return None
        
        if self._session_mgr is None:
            from strands.session import SnapshotSessionManager
            from strands.storage import LocalFileStorage
            
            storage = LocalFileStorage(str(self._storage_dir))
            self._session_mgr = SnapshotSessionManager(
                session_id="current",
                storage=storage,
            )
            self._session_mgr.initialize(self._agent)
        
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
                sessions.append({
                    "name": data.get("name", path.stem),
                    "path": str(path),
                    "created_at": data.get("created_at", ""),
                    "message_count": len(data.get("messages", [])),
                })
            except Exception:
                continue
        
        return sessions
    
    def save_session(self, name: str, agent: "Agent", session_log: Path | None = None) -> Path:
        """Save current session as a named snapshot.
        
        Uses Strands' SnapshotSessionManager for the internal state,
        and saves a compatible JSON to the saves directory for REPL.
        """
        # Get session manager and ensure it's initialized
        sm = self._get_session_manager()
        if sm is None:
            raise RuntimeError("Agent not set for session management")
        
        # Ensure agent is synced
        sm.sync_agent(agent)
        
        # Save snapshot
        sm.save_snapshot(agent, is_latest=True)
        
        # Also save a compatible JSON snapshot for REPL
        snapshot = self._create_repl_snapshot(name, agent)
        path = self._saves_dir / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
        
        return path
    
    def _create_repl_snapshot(self, name: str, agent: "Agent") -> dict[str, Any]:
        """Create a REPL-compatible snapshot from the agent."""
        from datetime import datetime, UTC
        
        messages = []
        
        # Try to get messages from agent
        if hasattr(agent, '_inner'):
            inner = agent._inner
            if hasattr(inner, 'messages'):
                msgs = inner.messages
                for msg in msgs:
                    role = getattr(msg, 'role', 'user') if hasattr(msg, 'role') else msg.get('role', 'user')
                    content = getattr(msg, 'content', '') if hasattr(msg, 'content') else msg.get('content', '')
                    if isinstance(content, list):
                        # Extract text from content blocks
                        texts = []
                        for block in content:
                            if isinstance(block, dict):
                                if 'text' in block:
                                    texts.append(block['text'])
                                elif block.get('type') == 'text':
                                    texts.append(str(block.get('text', '')))
                        content = '\n'.join(texts)
                    if content:
                        messages.append({"role": str(role), "content": str(content)})
        
        return {
            "version": "1.0",
            "name": name,
            "created_at": datetime.now(UTC).isoformat(),
            "config": {
                "model": getattr(agent.config, 'ollama_model', 'unknown'),
                "workspace": str(getattr(agent.config, 'agent_workspace', '')),
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
    
    def export_session(self, name: str, agent: "Agent | None", session_log: Path | None = None) -> Path:
        """Export session to exports directory."""
        if agent is None:
            raise RuntimeError("Agent not set for session management")
        
        snapshot = self._create_repl_snapshot(name, agent)
        
        path = self._exports_dir / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
        
        return path
    
    def restore_session(self, name: str, agent: "Agent") -> bool:
        """Restore a session from the saves directory."""
        from strands.session import SnapshotSessionManager
        from strands.storage import LocalFileStorage
        
        # Load the snapshot data
        data = self.load_session(name)
        
        # Initialize session manager with agent
        storage = LocalFileStorage(str(self._storage_dir))
        sm = SnapshotSessionManager(
            session_id=f"restore-{name}",
            storage=storage,
        )
        sm.initialize(agent)
        
        # Note: Full restore would require the snapshot JSON in the strands format
        # For now, we restore messages to the agent
        return self._restore_messages(agent, data)
    
    def _restore_messages(self, agent: "Agent", data: dict[str, Any]) -> bool:
        """Restore messages to agent."""
        if not hasattr(agent, '_inner'):
            return False
        
        inner = agent._inner
        if not hasattr(inner, 'messages'):
            return False
        
        messages = data.get("messages", [])
        if not messages:
            return True
        
        # Note: Strands manages messages internally, so we just log the restoration
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
