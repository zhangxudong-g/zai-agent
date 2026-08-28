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
    """The patch function must exist on strands_poc.llm and be callable."""
    from strands_poc import llm

    assert hasattr(llm, "patch_ollama_thinking")
    assert callable(llm.patch_ollama_thinking)


def test_patch_ollama_thinking_is_idempotent():
    """Calling the patch twice must not stack multiple wrappers."""
    import strands.models.ollama as ollama_mod

    from strands_poc import llm

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

    from strands_poc import llm

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

    from strands_poc import llm

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
