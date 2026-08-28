"""LLM setup for the Strands PoC.

Strands Agents ships a native ``OllamaModel`` class via the
``strands.models.ollama`` module. This module is a thin factory so
tests can swap the model without touching the agent loop.

Note: import path may vary across Strands versions:
    - `strands.models.ollama.OllamaModel` (most common)
    - `strands.models.ollama.Ollama` (older)
    - fall back to `strands.models.ollama` (module) on AttributeError

This module also ``monkey-patches`` ``OllamaModel.stream`` so that the
local thinking-model output (qwen3 with ``think=True``) surfaces as
Strands-standard ``contentBlockDelta / reasoningContent`` events — the
shape Strands's event loop knows how to convert into ``ReasoningTextStreamEvent``
(``top-level reasoningText field``), which ``StreamConsumer`` then
renders as a ``kind='thinking'`` chunk. Without this patch, Strands's
stock ``OllamaModel.stream`` silently drops ``event.message.thinking``,
so the user never sees the model's reasoning.
"""

from __future__ import annotations

import functools
import importlib
from typing import Any

from .config import Config


def patch_ollama_thinking() -> bool:
    """Monkey-patch ``OllamaModel.stream`` to expose thinking deltas.

    The upstream ``OllamaModel.stream`` consumes ``event.message.thinking``
    inside its own iteration loop and emits no Strands event for it —
    meaning the model's reasoning is invisible to our ``StreamConsumer``.

    This patch is a *thin fork*, not a pure wrap: we cannot wrap upstream
    without losing the thinking field (upstream reads it then discards it
    inside its loop), so we re-iterate the ollama stream ourselves and
    reuse the upstream helpers (``format_request`` / ``format_chunk`` /
    ``OVERFLOW_MESSAGES``) so a Strands release that adds new fields or
    reshapes existing ones flows through here unchanged.

    The only logic local to this patch is the thinking injection —
    every other event (tool calls, text content, messageStart/Stop,
    metadata, overflow handling) goes through ``format_chunk``, so
    event shapes stay byte-compatible with upstream.

    Idempotent: calling this twice does not stack wrappers.

    Returns:
        ``True`` if the patch was applied (or was already applied);
        ``False`` if ``strands.models.ollama`` is not installed.
    """
    try:
        mod = importlib.import_module("strands.models.ollama")
    except ImportError:
        return False
    cls = getattr(mod, "OllamaModel", None) or getattr(mod, "Ollama", None)
    if cls is None:
        return False

    # Idempotency guard: re-patching would wrap a wrapper.
    if getattr(cls.stream, "_ollama_thinking_patched", False):
        return True

    original_stream = cls.stream

    @functools.wraps(original_stream)
    async def patched_stream(self, messages, tool_specs=None, system_prompt=None,
                              *, tool_choice=None, **kwargs: Any):
        from strands.models.ollama import (
            ContextWindowOverflowException,
            warn_on_tool_choice_not_supported,
        )
        import ollama as _ollama_pkg

        warn_on_tool_choice_not_supported(tool_choice)

        # Delegate request formatting to upstream — keeps parity with
        # whatever Strands's current shape is (options/keep_alive/etc.).
        request = self.format_request(messages, tool_specs, system_prompt)

        tool_requested = False
        last_event = None

        client = _ollama_pkg.AsyncClient(self.host, **self.client_args)
        try:
            response = await client.chat(**request)
            yield self.format_chunk({"chunk_type": "message_start"})
            yield self.format_chunk({"chunk_type": "content_start", "data_type": "text"})

            async for event in response:
                # Tool calls (unchanged from upstream shape).
                for tool_call in event.message.tool_calls or []:
                    yield self.format_chunk(
                        {"chunk_type": "content_start", "data_type": "tool", "data": tool_call},
                    )
                    yield self.format_chunk(
                        {"chunk_type": "content_delta", "data_type": "tool", "data": tool_call},
                    )
                    yield self.format_chunk(
                        {"chunk_type": "content_stop", "data_type": "tool", "data": tool_call},
                    )
                    tool_requested = True

                # PATCH INJECTION POINT — surface thinking AFTER tool calls
                # but BEFORE the text delta for this ollama event, so the
                # ordering matches what the model produced.
                thinking = getattr(event.message, "thinking", None)
                if thinking:
                    yield {
                        "contentBlockDelta": {
                            "delta": {
                                "reasoningContent": {
                                    "reasoningText": {"text": thinking},
                                },
                            },
                        },
                    }

                # Text content (unchanged from upstream shape).
                yield self.format_chunk(
                    {"chunk_type": "content_delta", "data_type": "text",
                     "data": event.message.content},
                )

                last_event = event
        except _ollama_pkg.ResponseError as error:
            if any(message in str(error).lower() for message in self.OVERFLOW_MESSAGES):
                raise ContextWindowOverflowException(str(error)) from error
            raise

        stop_reason = "tool_use" if tool_requested else (
            last_event.done_reason if last_event is not None else None
        )
        yield self.format_chunk({"chunk_type": "content_stop", "data_type": "text"})
        yield self.format_chunk({"chunk_type": "message_stop", "data": stop_reason})
        if last_event is not None:
            yield self.format_chunk({"chunk_type": "metadata", "data": last_event})

    patched_stream._ollama_thinking_patched = True  # idempotency marker
    cls.stream = patched_stream
    return True


def build_ollama_model(config: Config):
    """Construct the Ollama model instance used by the agent.

    Args:
        config: PoC configuration holding ``ollama_*`` fields.

    Returns:
        A configured model instance ready to be passed to
        ``Agent(model=...)``.
    """
    mod = importlib.import_module("strands.models.ollama")
    cls = getattr(mod, "OllamaModel", None) or getattr(mod, "Ollama", None)
    if cls is None:
        raise RuntimeError(
            f"Could not find Ollama model class in strands.models.ollama "
            f"(available attributes: {[a for a in dir(mod) if not a.startswith('_')]})"
        )

    # Strip any /no_think suffix since we control thinking via additional_args
    model_id = config.ollama_model.replace(":no_think", "").replace("/no_think", "")

    # Install the thinking-surface patch on OllamaModel.stream so qwen3's
    # ``think=True`` output is not silently dropped.
    patch_ollama_thinking()

    return cls(
        host=config.ollama_base_url,
        model_id=model_id,
        # Enable Qwen3's thinking mode to improve reasoning
        # See: https://ollama.com/library/qwen3 - think param for thinking models
        additional_args={"think": True},
    )


def build_ollama_model_safe(config: Config):
    """Like ``build_ollama_model`` but with extra options passed through."""
    model = build_ollama_model(config)

    # Best-effort: tune keep_alive and temperature via attributes
    # (newer Strands versions expose them; older versions ignore unknown attrs).
    for attr, value in (("keep_alive", "10m"), ("temperature", 0.7)):
        try:
            setattr(model, attr, value)
        except (AttributeError, TypeError):
            pass

    return model