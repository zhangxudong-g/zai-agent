"""Streaming output contracts (TDD RED→GREEN).

Pins the three fixes for the three symptoms observed in the live CLI:

  1. "no thinking"   — main.py used to silently drop ``kind=='thinking'``;
                      the CLI must now render thinking chunks.
  2. "tool calls not detailed" — StreamConsumer used to never emit
                      ``tool_end`` chunks; the consumer must now expose a
                      way for the AfterToolCallEvent hook to push a
                      ``tool_end`` chunk that the streaming loop can
                      yield to the user.
  3. "duplicated prints" — main.py used to render tool_start/input
                      during streaming AND then re-print a tool-call
                      summary from the JSONL log on exit; the streaming
                      path must skip the post-run summary.

No Ollama / no real Strands runtime required — we drive the
``StreamConsumer`` directly and inspect the contract surface.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from strands_poc.stream import StreamChunk, StreamConsumer


# ============================================================ #
# Group 1: StreamConsumer exposes a tool-result queue
# ============================================================ #
def test_register_tool_result_pushes_tool_end_chunk():
    """register_tool_result must queue a tool_end StreamChunk."""
    consumer = StreamConsumer()
    consumer.register_tool_result(
        tool_use_id="t1",
        tool_name="read",
        result="file contents here",
        is_error=False,
    )
    chunks = consumer.drain_tool_results()
    assert len(chunks) == 1
    c = chunks[0]
    assert c.kind == "tool_end"
    assert c.tool_use_id == "t1"
    assert c.tool_name == "read"
    assert c.result == "file contents here"
    assert c.is_error is False


def test_register_tool_result_marks_error():
    """Error results must surface is_error=True on the chunk."""
    consumer = StreamConsumer()
    consumer.register_tool_result(
        tool_use_id="t2",
        tool_name="grep",
        result="permission denied",
        is_error=True,
    )
    [c] = consumer.drain_tool_results()
    assert c.is_error is True
    assert c.tool_name == "grep"


def test_drain_tool_results_returns_empty_when_no_pending():
    """drain_tool_results must not block when nothing is queued."""
    consumer = StreamConsumer()
    chunks = consumer.drain_tool_results()
    assert chunks == []


def test_drain_tool_results_preserves_order():
    """Multiple registrations must come out in FIFO order."""
    consumer = StreamConsumer()
    consumer.register_tool_result("a", "read", "first", False)
    consumer.register_tool_result("b", "grep", "second", False)
    consumer.register_tool_result("c", "glob", "third", True)
    chunks = consumer.drain_tool_results()
    assert [c.tool_use_id for c in chunks] == ["a", "b", "c"]
    assert [c.is_error for c in chunks] == [False, False, True]


def test_drain_tool_results_clears_queue():
    """Each drain must remove its chunks — second drain returns []."""
    consumer = StreamConsumer()
    consumer.register_tool_result("x", "read", "data", False)
    first = consumer.drain_tool_results()
    second = consumer.drain_tool_results()
    assert len(first) == 1
    assert second == []


# ============================================================ #
# Group 2: JsonlTraceHook wires tool results into the consumer
# ============================================================ #
def test_trace_hook_pushes_tool_end_to_consumer():
    """AfterToolCallEvent must call consumer.register_tool_result so the
    streaming loop sees the result without going through JSONL."""
    from unittest.mock import MagicMock

    from strands_poc.agent import JsonlTraceHook

    consumer = StreamConsumer()
    # Bypass JsonlTraceHook.__init__ (no real SessionLogger needed for this assertion).
    hook = object.__new__(JsonlTraceHook)
    hook._logger = MagicMock()  # .tool_call_end / .tool_call_error just no-op
    hook._consumer = consumer

    # Build a minimal AfterToolCallEvent stand-in.
    event = MagicMock()
    event.tool_use = {"name": "read", "toolUseId": "call_42"}
    event.result = {"status": "success", "content": [{"type": "text", "text": "ok"}]}
    event.cancel_tool = None

    hook.after_tool(event)

    chunks = consumer.drain_tool_results()
    assert len(chunks) == 1
    c = chunks[0]
    assert c.kind == "tool_end"
    assert c.tool_use_id == "call_42"
    assert c.tool_name == "read"
    assert "ok" in c.result
    assert c.is_error is False


def test_trace_hook_can_be_construct_without_consumer():
    """Backwards-compat: constructing JsonlTraceHook without a consumer
    must still work (consumer is optional; legacy callers / tests do this)."""
    from unittest.mock import MagicMock

    from strands_poc.agent import JsonlTraceHook

    logger = MagicMock()
    hook = JsonlTraceHook(logger=logger)  # no consumer kwarg
    assert getattr(hook, "_consumer", None) is None


def test_trace_hook_with_consumer_records_error():
    """A cancelled tool call (cancel_tool truthy) must push is_error=True."""
    from unittest.mock import MagicMock

    from strands_poc.agent import JsonlTraceHook

    consumer = StreamConsumer()
    hook = object.__new__(JsonlTraceHook)
    hook._logger = MagicMock()
    hook._consumer = consumer

    event = MagicMock()
    event.tool_use = {"name": "edit", "toolUseId": "call_99"}
    event.result = {"status": "error", "content": "outside workspace"}
    event.cancel_tool = "outside workspace"

    hook.after_tool(event)

    [c] = consumer.drain_tool_results()
    assert c.is_error is True
    assert "outside workspace" in c.result


# ============================================================ #
# Group 3: main.py CLI — thinking displayed, stream skips JSONL re-print
# ============================================================ #
def _make_argv(**overrides):
    """Build a minimal argparse.Namespace mirroring main.parse_args() output."""
    from types import SimpleNamespace

    defaults = dict(
        workspace=Path("/tmp/none"),
        prompt="hello",
        prompt_file=None,
        stream=False,
        show_tools=True,
        mode="qa_fault",
        env_file=Path(".env"),
        use_community_tools=False,
        community_tool_categories=None,
        community_tool_names=None,
        list_tools=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_main_streaming_mode_renders_thinking(monkeypatch, capsys):
    """When the agent emits a 'thinking' chunk, main.py must print it
    (not silently drop it)."""
    from strands_poc import main as cli

    # Stub Agent so run_streaming yields a controlled sequence.
    class _FakeAgent:
        def __init__(self, *a, **kw): pass

        async def run_streaming(self, prompt, max_retries=3):
            for c in [
                StreamChunk(kind="thinking", thinking="reasoning step"),
                StreamChunk(kind="text", text="answer"),
                StreamChunk(kind="done", result="answer", stop_reason="end_turn"),
            ]:
                yield c

    monkeypatch.setattr(cli, "Agent", _FakeAgent)
    monkeypatch.setattr(cli, "get_config", lambda env_file=None: _fake_config())
    monkeypatch.setattr(cli, "SessionLogger", lambda *a, **kw: _fake_logger())
    monkeypatch.setattr(cli, "display_tool_results_from_log", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "generate_session_id", lambda: "TEST")

    # Streaming is now the default; no --stream flag needed
    cli.run_cli(["hi", "--workspace", "/tmp/w"])
    captured = capsys.readouterr().out

    assert "reasoning step" in captured, (
        f"main.py must render thinking chunks; got:\n{captured}"
    )
    assert "answer" in captured


def test_main_streaming_mode_skips_jsonl_summary(monkeypatch):
    """In --stream mode the JSONL post-run summary must NOT be invoked —
    the streaming loop already shows tool_start/tool_input/tool_end live."""
    from strands_poc import main as cli

    called = {"count": 0}

    def _spy_display(*a, **kw):
        called["count"] += 1

    class _FakeAgent:
        def __init__(self, *a, **kw): pass

        async def run_streaming(self, prompt, max_retries=3):
            yield StreamChunk(kind="text", text="hi")
            yield StreamChunk(kind="done", result="hi")

    monkeypatch.setattr(cli, "Agent", _FakeAgent)
    monkeypatch.setattr(cli, "get_config", lambda env_file=None: _fake_config())
    monkeypatch.setattr(cli, "SessionLogger", lambda *a, **kw: _fake_logger())
    monkeypatch.setattr(cli, "display_tool_results_from_log", _spy_display)
    monkeypatch.setattr(cli, "generate_session_id", lambda: "TEST")

    # Streaming is now the default; no --stream flag needed
    cli.run_cli(["hi", "--workspace", "/tmp/w"])

    assert called["count"] == 0, (
        f"streaming mode must skip display_tool_results_from_log; "
        f"called {called['count']} times"
    )


def test_main_non_streaming_mode_still_shows_jsonl_summary(monkeypatch):
    """With --sync, the JSONL post-run summary MUST still run
    (it's the only way the user sees tool outcomes)."""
    from strands_poc import main as cli

    called = {"count": 0}

    def _spy_display(*a, **kw):
        called["count"] += 1

    class _FakeAgent:
        def __init__(self, *a, **kw): pass

        def run(self, prompt):
            return "done"

    monkeypatch.setattr(cli, "Agent", _FakeAgent)
    monkeypatch.setattr(cli, "get_config", lambda env_file=None: _fake_config())
    monkeypatch.setattr(cli, "SessionLogger", lambda *a, **kw: _fake_logger())
    monkeypatch.setattr(cli, "display_tool_results_from_log", _spy_display)
    monkeypatch.setattr(cli, "generate_session_id", lambda: "TEST")

    cli.run_cli(["--sync", "hi", "--workspace", "/tmp/w"])

    assert called["count"] == 1, (
        f"sync mode must call display_tool_results_from_log; "
        f"called {called['count']} times"
    )


# ============================================================ #
# Helpers
# ============================================================ #
def _fake_config():
    from strands_poc.config import Config

    return Config(
        ollama_base_url="http://localhost:11434",
        ollama_model="qwen3:7b",
        agent_workspace=Path("/tmp/w"),
        session_log_dir=Path("/tmp/sessions"),
        allowed_tools=["read", "glob", "grep", "write", "edit"],
    )


class _fake_logger:
    """Stand-in for SessionLogger — only attributes main.py touches."""

    def __init__(self):
        from pathlib import Path

        self.log_file = Path("/tmp/fake.jsonl")

    def __getattr__(self, name):
        return lambda *a, **kw: None
