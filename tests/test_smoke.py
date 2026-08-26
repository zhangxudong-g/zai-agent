"""Smoke tests for the Strands PoC.

These tests do **not** require a running Ollama instance or the
strands-agents SDK to be installed. They cover:

1. Config loading from env / .env
2. WorkspaceSandboxHook flags out-of-workspace write/edit actions
3. WorkspaceSandboxHook allows in-workspace actions
4. SessionLogger writes well-formed JSONL lines (matches claude-agent schema)
5. StreamConsumer translates Strands dict events into StreamChunks
6. Strands SDK importability check

The StreamConsumer tests feed synthetic dicts so we can verify the
shape of the consumer without depending on the SDK runtime.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the package importable when pytest is invoked from the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


from strands_poc.config import get_config  # noqa: E402
from strands_poc.security import WorkspaceSandboxHook  # noqa: E402
from strands_poc.stream import StreamChunk, StreamConsumer  # noqa: E402
from strands_poc.trace import SessionLogger  # noqa: E402


# --------------------------------------------------------------------- #
# Mock BeforeToolCallEvent (no SDK dependency)
# --------------------------------------------------------------------- #
class _MockBeforeToolCallEvent:
    """Minimal stand-in for ``strands.hooks.BeforeToolCallEvent``."""

    def __init__(self, tool_use: dict, cancel_tool: str | None = None):
        self.tool_use = tool_use
        self.cancel_tool = cancel_tool


# --------------------------------------------------------------------- #
# 1. Config loading
# --------------------------------------------------------------------- #
def test_config_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:7b")
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("SESSION_LOG_DIR", str(tmp_path / "sessions"))

    cfg = get_config(env_file=None)
    assert cfg.ollama_base_url == "http://localhost:11434"
    assert cfg.ollama_model == "qwen3:7b"
    assert cfg.allowed_tools == [
        "read", "glob", "grep", "file_tree", "outline", "write", "edit",
    ]


def test_config_custom_tools(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:7b")
    monkeypatch.setenv("AGENT_WORKSPACE", ".")
    monkeypatch.setenv("SESSION_LOG_DIR", ".")
    monkeypatch.setenv("ALLOWED_TOOLS", "Read,Glob")
    cfg = get_config(env_file=None)
    # Tools are lowercased per Strands convention.
    assert cfg.allowed_tools == ["read", "glob"]


# --------------------------------------------------------------------- #
# 2. WorkspaceSandboxHook
# --------------------------------------------------------------------- #
def test_sandbox_blocks_outside_path(tmp_path: Path) -> None:
    hook = WorkspaceSandboxHook(tmp_path)
    event = _MockBeforeToolCallEvent({"name": "write", "input": {"file_path": "/etc/passwd"}})
    hook.before_tool(event)
    assert event.cancel_tool is not None
    assert "Refusing write" in event.cancel_tool
    assert "/etc/passwd" in event.cancel_tool


def test_sandbox_blocks_edit_outside(tmp_path: Path) -> None:
    hook = WorkspaceSandboxHook(tmp_path)
    event = _MockBeforeToolCallEvent({"name": "edit", "input": {"file_path": "../outside.txt"}})
    hook.before_tool(event)
    assert event.cancel_tool is not None
    assert "Refusing edit" in event.cancel_tool


def test_sandbox_allows_inside_path(tmp_path: Path) -> None:
    hook = WorkspaceSandboxHook(tmp_path)
    event = _MockBeforeToolCallEvent({"name": "write", "input": {"file_path": "ok.txt"}})
    hook.before_tool(event)
    assert event.cancel_tool is None


def test_sandbox_allows_absolute_inside(tmp_path: Path) -> None:
    hook = WorkspaceSandboxHook(tmp_path)
    inside = tmp_path / "ok.txt"
    event = _MockBeforeToolCallEvent({"name": "write", "input": {"file_path": str(inside)}})
    hook.before_tool(event)
    assert event.cancel_tool is None


def test_sandbox_ignores_readonly_tools(tmp_path: Path) -> None:
    """Read/Glob/Grep are not in _WRITE_TOOLS and must never be vetoed."""
    hook = WorkspaceSandboxHook(tmp_path)
    for name in ("read", "glob", "grep"):
        event = _MockBeforeToolCallEvent({"name": name, "input": {"file_path": "/etc/passwd"}})
        hook.before_tool(event)
        assert event.cancel_tool is None


def test_sandbox_skips_when_path_empty(tmp_path: Path) -> None:
    """If the model hasn't supplied a path yet (input is empty), don't veto."""
    hook = WorkspaceSandboxHook(tmp_path)
    event = _MockBeforeToolCallEvent({"name": "write", "input": {}})
    hook.before_tool(event)
    assert event.cancel_tool is None


def test_sandbox_register_hooks_smoke(tmp_path: Path) -> None:
    """``register_hooks`` should accept a registry-like object."""
    hook = WorkspaceSandboxHook(tmp_path)

    class _Registry:
        def __init__(self):
            self.callbacks: list = []

        def add_callback(self, event_type, callback):
            self.callbacks.append((event_type, callback))

    registry = _Registry()
    hook.register_hooks(registry)
    assert len(registry.callbacks) == 1


# --------------------------------------------------------------------- #
# 3. SessionLogger JSONL shape
# --------------------------------------------------------------------- #
def test_session_logger_writes_well_formed_jsonl(tmp_path: Path) -> None:
    logger = SessionLogger(session_id="test_sid", log_dir=tmp_path)
    logger.session_start()
    logger.user_prompt("hello")
    logger.agent_start("hello")
    logger.tool_call_start(tool_name="read", tool_call_id="abc", arguments={"file_path": "x"})
    logger.tool_call_end(tool_call_id="abc", result="file contents", tool_name="read")
    logger.result_message(
        subtype="success",
        is_error=False,
        num_turns=2,
        duration_ms=123,
        stop_reason="end_turn",
    )
    logger.agent_end("done")
    logger.session_summary(duration_ms=123, model="x", tokens={"input": 1, "output": 2, "total": 3})
    logger.session_end()

    lines = (tmp_path / "test_sid.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 9
    parsed = [json.loads(line) for line in lines]
    events = [r["event"] for r in parsed]
    assert events == [
        "session_start",
        "user_prompt",
        "agent_start",
        "tool_call_start",
        "tool_call_end",
        "result",
        "agent_end",
        "session_summary",
        "session_end",
    ]
    assert [r["sequence"] for r in parsed] == list(range(1, 10))
    assert {r["session_id"] for r in parsed} == {"test_sid"}


def test_session_logger_writes_error(tmp_path: Path) -> None:
    logger = SessionLogger(session_id="err_sid", log_dir=tmp_path)
    logger.session_start()
    logger.log_error(error="boom", context={"phase": "test"})
    logger.session_end()
    events = [
        json.loads(line) for line in (tmp_path / "err_sid.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    err = next(e for e in events if e["event"] == "error")
    assert err["error"] == "boom"
    assert err["context"] == {"phase": "test"}


def test_session_logger_writes_message(tmp_path: Path) -> None:
    logger = SessionLogger(session_id="msg_sid", log_dir=tmp_path)
    logger.session_start()
    logger._write_message("[compaction] some summary")
    logger.session_end()
    events = [
        json.loads(line) for line in (tmp_path / "msg_sid.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    msg = next(e for e in events if e["event"] == "message")
    assert "compaction" in msg["content"]


# --------------------------------------------------------------------- #
# 4. StreamConsumer — Strands dict event translation
# --------------------------------------------------------------------- #
def test_consumer_emits_text_for_data_event() -> None:
    consumer = StreamConsumer()
    chunks = consumer.feed({"data": "hello "})
    chunks += consumer.feed({"data": "world"})
    text_chunks = [c for c in chunks if c.kind == "text"]
    assert [c.text for c in text_chunks] == ["hello ", "world"]


def test_consumer_emits_thinking_for_reasoning_text() -> None:
    consumer = StreamConsumer()
    chunks = consumer.feed({"reasoningText": "let me think"})
    assert len(chunks) == 1
    assert chunks[0].kind == "thinking"
    assert chunks[0].thinking == "let me think"


def test_consumer_emits_tool_start_then_input() -> None:
    consumer = StreamConsumer()
    # First time we see the tool → tool_start
    chunks = consumer.feed({"current_tool_use": {
        "toolUseId": "t1", "name": "read", "input": {"file_path": "a.txt"}
    }})
    assert len(chunks) == 1
    assert chunks[0].kind == "tool_start"
    assert chunks[0].tool_name == "read"
    assert chunks[0].tool_use_id == "t1"

    # Subsequent update → tool_input
    chunks = consumer.feed({"current_tool_use": {
        "toolUseId": "t1", "name": "read", "input": {"file_path": "a.txt", "encoding": "utf-8"}
    }})
    assert len(chunks) == 1
    assert chunks[0].kind == "tool_input"
    assert chunks[0].input_args == {"file_path": "a.txt", "encoding": "utf-8"}


def test_consumer_emits_done_for_result_event() -> None:
    consumer = StreamConsumer()

    class _MockResult:
        def __init__(self):
            self.message = "final answer"
            self.metrics = None
            self.stop_reason = "end_turn"

    chunks = consumer.feed({"result": _MockResult()})
    assert len(chunks) == 1
    assert chunks[0].kind == "done"
    assert chunks[0].result == "final answer"
    assert chunks[0].is_error is False
    assert chunks[0].stop_reason == "end_turn"
    assert consumer.done_emitted is True


def test_consumer_emits_done_only_once() -> None:
    consumer = StreamConsumer()
    event = {"result": type("R", (), {"message": "x", "metrics": None, "stop_reason": "end_turn"})()}
    chunks1 = consumer.feed(event)
    chunks2 = consumer.feed(event)
    assert len(chunks1) == 1
    assert chunks2 == []  # second time → no done chunk


def test_consumer_emits_force_stop() -> None:
    consumer = StreamConsumer()
    chunks = consumer.feed({"force_stop": True, "force_stop_reason": "max_turns"})
    assert len(chunks) == 1
    assert chunks[0].kind == "done"
    assert chunks[0].is_error is True
    assert chunks[0].stop_reason == "force_stop"
    assert "max_turns" in chunks[0].result


def test_consumer_handles_tool_stream_event() -> None:
    consumer = StreamConsumer()
    # First register the tool
    consumer.feed({"current_tool_use": {
        "toolUseId": "t2", "name": "grep", "input": {"pattern": "x"}
    }})
    # Now feed tool_stream_event with updated input
    chunks = consumer.feed({"tool_stream_event": {
        "tool_use": {"toolUseId": "t2", "name": "grep", "input": {"pattern": "x", "path": "src"}}
    }})
    assert len(chunks) == 1
    assert chunks[0].kind == "tool_input"
    assert chunks[0].input_args == {"pattern": "x", "path": "src"}


# --------------------------------------------------------------------- #
# 5. Strands SDK importability check
# --------------------------------------------------------------------- #
def test_strands_sdk_importable() -> None:
    """Sanity check that the Strands SDK is importable in this environment.

    Skipped when the SDK is not installed yet.
    """
    strands = pytest.importorskip("strands", reason="strands-agents not installed")
    assert hasattr(strands, "Agent")

    # Ollama model class is in strands.models.ollama (either OllamaModel or Ollama).
    ollama_mod = pytest.importorskip("strands.models.ollama", reason="ollama model not available")
    assert hasattr(ollama_mod, "OllamaModel") or hasattr(ollama_mod, "Ollama")


def test_strands_hooks_importable() -> None:
    """Sanity check for the hooks module."""
    hooks = pytest.importorskip("strands.hooks", reason="strands.hooks not available")
    assert hasattr(hooks, "BeforeToolCallEvent")
    assert hasattr(hooks, "AfterToolCallEvent")
    assert hasattr(hooks, "HookProvider")


def test_strands_tool_decorator_importable() -> None:
    """Sanity check for the @tool decorator."""
    strands = pytest.importorskip("strands", reason="strands-agents not installed")
    assert hasattr(strands, "tool")


# --------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------- #
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))