"""Ollama thinking patch tests (TDD RED→GREEN).

Strands's stock ``OllamaModel.stream`` ignores ``event.message.thinking``
(only ``bedrock.py`` / ``sagemaker.py`` / ``openai.py`` surface
reasoning_content). When the local model is in thinking mode (qwen3 with
``think=True``), the model produces thinking tokens that never reach the
user.

We monkey-patch ``OllamaModel.stream`` to inject Strands-standard
``contentBlockDelta`` events carrying ``reasoningContent`` — the shape
``event_loop/streaming.py`` already knows how to turn into
``ReasoningTextStreamEvent`` (which our ``StreamConsumer`` consumes).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ============================================================ #
# Group 1: the patch function exists and is safe to call
# ============================================================ #
def test_patch_ollama_thinking_is_callable():
    """The patch function must exist on zai.llm and be callable."""
    from zai import llm

    assert hasattr(llm, "patch_ollama_thinking")
    assert callable(llm.patch_ollama_thinking)


def test_patch_ollama_thinking_is_idempotent():
    """Calling the patch twice must not stack multiple wrappers."""
    import strands.models.ollama as ollama_mod

    from zai import llm

    cls = getattr(ollama_mod, "OllamaModel", None) or getattr(ollama_mod, "Ollama", None)
    if cls is None:
        pytest.skip("strands.models.ollama not installed")
    llm.patch_ollama_thinking()
    after_first = cls.stream
    llm.patch_ollama_thinking()
    after_second = cls.stream
    # Second call must not produce a different wrapper
    assert after_first is after_second


# ============================================================ #
# Group 2: patched stream surfaces reasoning_content events
# ============================================================ #
async def test_patched_stream_yields_reasoning_content_delta(monkeypatch):
    """When ollama returns event.message.thinking, the patched stream
    must yield a contentBlockDelta with reasoningContent before the text."""
    import json
    from unittest.mock import MagicMock

    import strands.models.ollama as ollama_mod

    from zai import llm

    cls = getattr(ollama_mod, "OllamaModel", None) or getattr(ollama_mod, "Ollama", None)
    if cls is None:
        pytest.skip("strands.models.ollama not installed")

    # Fake ollama event: thinking + text content
    class _FakeMsg:
        def __init__(self):
            self.thinking = "let me reason step by step..."
            self.content = "the answer"
            self.tool_calls = None

    class _FakeEvt:
        def __init__(self):
            self.message = _FakeMsg()
            self.done_reason = None
            self.prompt_eval_count = 0
            self.eval_count = 0
            self.total_duration = 0

    async def _fake_chat(**_kw):
        async def _gen():
            yield _FakeEvt()
        return _gen()

    # Patch the ollama client used inside the patched stream
    monkeypatch.setattr(ollama_mod.ollama, "AsyncClient", MagicMock())

    async def _fake_client(*_args, **_kw):
        client = MagicMock()
        client.chat = _fake_chat
        return client

    # Replace the AsyncClient constructor: it must return an awaitable that
    # resolves to a client whose chat() returns an async iter.
    async def _make_client(*_args, **_kw):
        client = MagicMock()
        client.chat = _fake_chat
        return client

    # Newer ollama python client uses sync constructor; we replace the
    # constructor itself, not its return value.
    class _FakeAsyncClient:
        def __init__(self, host, **kw): pass

        async def chat(self, **_kw):
            async def _gen():
                yield _FakeEvt()
            return _gen()

    monkeypatch.setattr(ollama_mod.ollama, "AsyncClient", _FakeAsyncClient)

    # Make sure the patch is applied
    applied = llm.patch_ollama_thinking()
    assert applied is True

    # Build a bare OllamaModel instance (skip __init__)
    model = object.__new__(cls)
    model.host = "http://fake"
    # format_request reads these keys — provide the minimum required.
    model.config = {
        "model_id": "fake-model",
        "additional_args": {"think": True},
    }
    model.client_args = {}
    model.OVERFLOW_MESSAGES = []

    # Collect events from patched stream
    events = []
    agen = model.stream(messages=[])
    async for ev in agen:
        events.append(ev)
        # Stop after messageStop to avoid touching format_chunk internals
        if "messageStop" in ev:
            break

    # Assert: at least one event is a contentBlockDelta with reasoningContent
    reasoning_events = [
        ev for ev in events
        if "contentBlockDelta" in ev
        and "reasoningContent" in ev["contentBlockDelta"].get("delta", {})
    ]
    assert len(reasoning_events) >= 1, (
        f"patched stream must yield reasoningContent deltas; got events: "
        f"{[list(e.keys()) for e in events]}"
    )
    assert "let me reason step by step" in json.dumps(reasoning_events)


async def test_patched_stream_does_not_emit_reasoning_when_model_has_none(monkeypatch):
    """When ollama returns no thinking field, no reasoning_content events."""
    import strands.models.ollama as ollama_mod

    from zai import llm

    cls = getattr(ollama_mod, "OllamaModel", None) or getattr(ollama_mod, "Ollama", None)
    if cls is None:
        pytest.skip("strands.models.ollama not installed")

    class _FakeMsg:
        def __init__(self):
            self.thinking = None  # no thinking
            self.content = "hello"
            self.tool_calls = None

    class _FakeEvt:
        def __init__(self):
            self.message = _FakeMsg()
            self.done_reason = None
            self.prompt_eval_count = 0
            self.eval_count = 0
            self.total_duration = 0

    class _FakeAsyncClient:
        def __init__(self, host, **kw): pass

        async def chat(self, **_kw):
            async def _gen():
                yield _FakeEvt()
            return _gen()

    monkeypatch.setattr(ollama_mod.ollama, "AsyncClient", _FakeAsyncClient)
    llm.patch_ollama_thinking()

    model = object.__new__(cls)
    model.host = "http://fake"
    model.config = {"model_id": "fake-model", "additional_args": {}}
    model.client_args = {}
    model.OVERFLOW_MESSAGES = []

    events = []
    agen = model.stream(messages=[])
    async for ev in agen:
        events.append(ev)
        if "messageStop" in ev:
            break

    reasoning_events = [
        ev for ev in events
        if "contentBlockDelta" in ev
        and "reasoningContent" in ev["contentBlockDelta"].get("delta", {})
    ]
    assert reasoning_events == [], (
        f"no reasoning events when model has no thinking; got {reasoning_events}"
    )


# ============================================================ #
# Group 3: connection errors must NOT crash the agent
# ============================================================ #
async def test_patched_stream_surfaces_connect_error_as_message(monkeypatch):
    """When ollama host is unreachable (DNS / connection refused /
    timeout), the patched stream must convert the network error into a
    normal-looking model response containing a friendly error message
    and ``stop_reason='end_turn'`` — so the event loop ends gracefully
    instead of yielding ``ForceStopEvent``.

    Regression: previously this ConnectError bubbled up through
    ``strands.event_loop`` and produced ``Stop reason: force_stop``,
    interrupting the whole session on transient network blips.
    """
    import httpx
    import strands.models.ollama as ollama_mod

    from zai import llm

    cls = getattr(ollama_mod, "OllamaModel", None) or getattr(ollama_mod, "Ollama", None)
    if cls is None:
        pytest.skip("strands.models.ollama not installed")

    class _AsyncClientRaisesConnect:
        def __init__(self, host, **kw):
            self.host = host

        async def chat(self, **_kw):
            # Streaming path in ollama SDK does NOT wrap ConnectError,
            # so it surfaces as httpx.ConnectError directly.
            raise httpx.ConnectError(
                "[Errno 11001] getaddrinfo failed",
            )

    monkeypatch.setattr(ollama_mod.ollama, "AsyncClient", _AsyncClientRaisesConnect)
    llm.patch_ollama_thinking()

    model = object.__new__(cls)
    model.host = "http://nonexistent.invalid.example:11434"
    model.config = {"model_id": "fake-model", "additional_args": {}}
    model.client_args = {}
    model.OVERFLOW_MESSAGES = []

    events: list[dict] = []
    agen = model.stream(messages=[])
    # The stream MUST not raise — it should yield events instead.
    async for ev in agen:
        events.append(ev)
        if "messageStop" in ev:
            break

    # 1. messageStart must be present (so the message has a role).
    starts = [ev for ev in events if "messageStart" in ev]
    assert starts, "expected messageStart on connection-error path"

    # 2. content_delta events must contain a friendly error message.
    text_deltas = [
        ev for ev in events
        if "contentBlockDelta" in ev
        and "text" in ev["contentBlockDelta"].get("delta", {})
    ]
    assert text_deltas, "expected a text content delta carrying the error message"
    joined = "".join(
        ev["contentBlockDelta"]["delta"]["text"] for ev in text_deltas
    )
    assert "Cannot reach Ollama" in joined, (
        f"error message must point the user at Ollama; got: {joined!r}"
    )
    # The original host must be mentioned so the user knows which URL failed.
    assert "nonexistent.invalid.example" in joined, (
        f"error message must include the host that failed; got: {joined!r}"
    )

    # 3. messageStop must be end_turn (NOT force_stop — this is what
    #    strands treats as the success path).
    stops = [ev for ev in events if "messageStop" in ev]
    assert len(stops) == 1, f"expected exactly one messageStop; got {len(stops)}"
    assert stops[0]["messageStop"]["stopReason"] == "end_turn", (
        f"stopReason must be end_turn to avoid ForceStopEvent; "
        f"got {stops[0]['messageStop']['stopReason']!r}"
    )


async def test_patched_stream_surfaces_timeout_as_message(monkeypatch):
    """httpx.TimeoutException must also surface as a normal end_turn,
    not crash the event loop."""
    import httpx
    import strands.models.ollama as ollama_mod

    from zai import llm

    cls = getattr(ollama_mod, "OllamaModel", None) or getattr(ollama_mod, "Ollama", None)
    if cls is None:
        pytest.skip("strands.models.ollama not installed")

    class _AsyncClientRaisesTimeout:
        def __init__(self, host, **kw):
            self.host = host

        async def chat(self, **_kw):
            raise httpx.TimeoutException("read timed out")

    monkeypatch.setattr(ollama_mod.ollama, "AsyncClient", _AsyncClientRaisesTimeout)
    llm.patch_ollama_thinking()

    model = object.__new__(cls)
    model.host = "http://10.255.255.1:11434"
    model.config = {"model_id": "fake-model", "additional_args": {}}
    model.client_args = {}
    model.OVERFLOW_MESSAGES = []

    events: list[dict] = []
    agen = model.stream(messages=[])
    async for ev in agen:
        events.append(ev)
        if "messageStop" in ev:
            break

    assert any("messageStart" in ev for ev in events)
    assert any("messageStop" in ev for ev in events)
    stop = next(ev for ev in events if "messageStop" in ev)
    assert stop["messageStop"]["stopReason"] == "end_turn"
    text_deltas = [
        ev for ev in events
        if "contentBlockDelta" in ev
        and "text" in ev["contentBlockDelta"].get("delta", {})
    ]
    joined = "".join(
        ev["contentBlockDelta"]["delta"]["text"] for ev in text_deltas
    )
    assert "timed out" in joined.lower(), joined


async def test_patched_stream_surfaces_oserror_as_message(monkeypatch):
    """A raw OSError (e.g. socket.gaierror from getaddrinfo) must also
    be classified as a network-unreachable failure and surfaced as a
    normal end_turn response. This covers Windows where getaddrinfo
    can surface DNS failures as ``OSError`` rather than ``httpx``'s
    higher-level ``ConnectError``."""
    import strands.models.ollama as ollama_mod

    from zai import llm

    cls = getattr(ollama_mod, "OllamaModel", None) or getattr(ollama_mod, "Ollama", None)
    if cls is None:
        pytest.skip("strands.models.ollama not installed")

    class _AsyncClientRaisesOSError:
        def __init__(self, host, **kw):
            self.host = host

        async def chat(self, **_kw):
            # Mirrors what httpx surfaces when getaddrinfo returns
            # WSANO_DATA on Windows: a generic OSError subclass.
            raise ConnectionRefusedError(
                "[Errno 111] Connection refused",
            )

    monkeypatch.setattr(ollama_mod.ollama, "AsyncClient", _AsyncClientRaisesOSError)
    llm.patch_ollama_thinking()

    model = object.__new__(cls)
    model.host = "http://localhost:65535"
    model.config = {"model_id": "fake-model", "additional_args": {}}
    model.client_args = {}
    model.OVERFLOW_MESSAGES = []

    events: list[dict] = []
    agen = model.stream(messages=[])
    async for ev in agen:
        events.append(ev)
        if "messageStop" in ev:
            break

    assert any("messageStart" in ev for ev in events)
    stop = next(ev for ev in events if "messageStop" in ev)
    assert stop["messageStop"]["stopReason"] == "end_turn", (
        f"OSError must be classified as network-unreachable; "
        f"got stopReason={stop['messageStop']['stopReason']!r}"
    )
    text_deltas = [
        ev for ev in events
        if "contentBlockDelta" in ev
        and "text" in ev["contentBlockDelta"].get("delta", {})
    ]
    joined = "".join(
        ev["contentBlockDelta"]["delta"]["text"] for ev in text_deltas
    )
    assert "Cannot reach Ollama" in joined


async def test_patched_stream_propagates_unrelated_exceptions(monkeypatch):
    """An exception that is NOT a network failure (e.g. a bug in our
    format_request) must still propagate so the user sees the real
    error — we only swallow transport-layer failures."""
    import strands.models.ollama as ollama_mod

    from zai import llm

    cls = getattr(ollama_mod, "OllamaModel", None) or getattr(ollama_mod, "Ollama", None)
    if cls is None:
        pytest.skip("strands.models.ollama not installed")

    class _BugError(RuntimeError):
        """A non-network error to confirm we don't over-catch."""

    class _AsyncClientRaisesBug:
        def __init__(self, host, **kw):
            self.host = host

        async def chat(self, **_kw):
            raise _BugError("format_request misconfigured")

    monkeypatch.setattr(ollama_mod.ollama, "AsyncClient", _AsyncClientRaisesBug)
    llm.patch_ollama_thinking()

    model = object.__new__(cls)
    model.host = "http://fake"
    model.config = {"model_id": "fake-model", "additional_args": {}}
    model.client_args = {}
    model.OVERFLOW_MESSAGES = []

    agen = model.stream(messages=[])
    with pytest.raises(_BugError, match="format_request misconfigured"):
        async for _ in agen:
            pass  # pragma: no cover


async def test_unreachable_agent_returns_clean_assistant_message(monkeypatch):
    """End-to-end: an Agent whose Ollama host is unreachable returns a
    clean assistant string that contains the friendly diagnostic. No
    exception escapes — a transient network blip must not interrupt
    the session."""
    import httpx
    import strands.models.ollama as ollama_mod

    from zai import agent as agent_mod
    from zai import llm
    from zai.config import Config
    from zai.trace import SessionLogger

    cls = getattr(ollama_mod, "OllamaModel", None) or getattr(ollama_mod, "Ollama", None)
    if cls is None:
        pytest.skip("strands.models.ollama not installed")

    class _UnreachableClient:
        def __init__(self, host, **kw):
            self.host = host

        async def chat(self, **_kw):
            raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")

    monkeypatch.setattr(ollama_mod.ollama, "AsyncClient", _UnreachableClient)
    llm.patch_ollama_thinking()

    cfg = Config(
        ollama_base_url="http://nonexistent.invalid.example:11434",
        ollama_model="fake-model",
        agent_workspace=Path(ROOT / "workspace" / "sample_project").resolve(),
        session_log_dir=Path(ROOT / "sessions").resolve(),
    )
    logger = SessionLogger(session_id="unreach-test", log_dir=cfg.session_log_dir)
    a = agent_mod.Agent(config=cfg, logger=logger)

    final_text = await a.run_async("hello")
    # No exception must leak — the unreachable Ollama is reported as a
    # normal assistant turn instead.
    assert isinstance(final_text, str)
    assert "Cannot reach Ollama" in final_text, (
        f"diagnostic must surface as the assistant reply; got: {final_text!r}"
    )
    assert "nonexistent.invalid.example" in final_text, (
        f"the failing host must appear in the message; got: {final_text!r}"
    )
