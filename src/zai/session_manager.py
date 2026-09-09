"""Session management - list, save, load, export."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .paths import get_zai_saves_dir, get_zai_exports_dir
from .snapshot import Snapshot, save_snapshot, load_snapshot, snapshot_from_jsonl

if TYPE_CHECKING:
    from .config import Config


class SessionInfo:
    """Info about a saved session."""
    
    def __init__(self, name: str, path: Path, created_at: str, message_count: int):
        self.name = name
        self.path = path
        self.created_at = created_at
        self.message_count = message_count
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "created_at": self.created_at,
            "message_count": self.message_count,
        }


class SessionManager:
    """Manage saved sessions."""
    
    def __init__(self, saves_dir: Path | None = None, exports_dir: Path | None = None):
        self.saves_dir = saves_dir or get_zai_saves_dir()
        self.exports_dir = exports_dir or get_zai_exports_dir()
    
    def list_sessions(self) -> list[dict]:
        """List all saved sessions."""
        sessions = []
        if not self.saves_dir.exists():
            return sessions
        
        for path in sorted(self.saves_dir.glob("*.json")):
            try:
                data = path.read_text(encoding="utf-8")
                import json as _json
                data = _json.loads(data)
                sessions.append({
                    "name": data.get("name", path.stem),
                    "path": str(path),
                    "created_at": data.get("created_at", ""),
                    "message_count": len(data.get("messages", [])),
                })
            except Exception:
                continue
        
        return sessions
    
    def save_session(self, name: str, config: Config, session_log: Path) -> Path:
        """Save a session from JSONL log."""
        snapshot = snapshot_from_jsonl(session_log)
        snapshot.name = name
        snapshot.config = {
            "ollama_base_url": config.ollama_base_url,
            "ollama_model": config.ollama_model,
            "workspace": str(config.agent_workspace),
        }
        snapshot.metadata = {
            "total_messages": len(snapshot.messages),
            "total_tool_calls": len(snapshot.tool_calls),
        }
        
        path = self.saves_dir / f"{name}.json"
        save_snapshot(snapshot, path)
        return path
    
    def load_session(self, name: str) -> Snapshot:
        """Load a saved session."""
        path = self.saves_dir / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {name}")
        return load_snapshot(path)
    
    def export_session(self, name: str, session_log: Path) -> Path:
        """Export session to exports directory."""
        snapshot = snapshot_from_jsonl(session_log)
        snapshot.name = name
        
        path = self.exports_dir / f"{name}.json"
        save_snapshot(snapshot, path)
        return path
    
    def delete_session(self, name: str) -> bool:
        """Delete a saved session."""
        path = self.saves_dir / f"{name}.json"
        if path.exists():
            path.unlink()
            return True
        return False


def get_session_manager() -> SessionManager:
    """Get the global session manager instance."""
    return SessionManager()
