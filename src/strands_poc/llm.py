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

import contextlib
import functools
import importlib
import logging
import os
from pathlib import Path
from typing import Any

from .config import Config

# Debug logging for Ollama requests — opt-out via ZAI_DEBUG_OLLAMA=0.
# Always writes a JSONL line per Ollama call to the session log dir so we
# can inspect the messages array sent to Ollama on every cycle (first call
# + after every tool result). This was added to debug the recurring
# "no user query found in messages" 500 from Ollama.
#
# Set ZAI_DEBUG_OLLAMA=0 to disable. File path: <session_log_dir>/ollama-debug.jsonl.
_ollama_debug_log = logging.getLogger("zai.ollama_debug")
_ollama_debug_log.setLevel(logging.DEBUG)
_ollama_debug_log.propagate = False
_ollama_debug_handler: logging.Handler | None = None
_ollama_debug_call_counter = 0
_ollama_debug_path: Path | None = None


def _setup_ollama_debug() -> None:
    """Enable per-request debug logging unless explicitly disabled.

    Writes one JSON line per Ollama call to ``<cwd>/ollama-debug.jsonl``
    (overwritten on each invocation). When this module is imported from a
    different working directory, write to that directory.

    Set ``ZAI_DEBUG_OLLAMA=0`` to disable.
    """
    global _ollama_debug_handler, _ollama_debug_path
    if _ollama_debug_handler is not None:
        return
    if os.getenv("ZAI_DEBUG_OLLAMA") == "0":
        return
    try:
        log_path = Path.cwd() / "ollama-debug.jsonl"
        h = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        h.setFormatter(logging.Formatter("%(message)s\n"))
        _ollama_debug_log.addHandler(h)
        _ollama_debug_handler = h
        _ollama_debug_path = log_path
    except OSError:
        pass


_setup_ollama_debug()


# ---------------------------------------------------------------- #
# Connection-failure handling
# ---------------------------------------------------------------- #
#
# When the configured Ollama host is unreachable (DNS resolution
# fails, the TCP connection is refused, the host is offline, or the
# request times out during handshake), ``ollama.AsyncClient.chat``
# raises a low-level transport exception:
#
#   * httpx.ConnectError  (DNS / connect refused / network unreachable)
#   * httpx.TimeoutException  (read / connect timeout)
#   * OSError             (raw socket errors, e.g. gaierror)
#
# The Ollama Python SDK's streaming path does NOT wrap these — only
# ``_request_raw`` (non-streaming chat) catches ``ConnectError`` and
# turns it into a ``ConnectionError``. So ``ollama.AsyncClient.chat(..., stream=True)``
# propagates the raw ``httpx.ConnectError`` straight to us.
#
# Strands's event loop converts any exception raised out of
# ``OllamaModel.stream`` into a ``ForceStopEvent`` with
# ``stop_reason='force_stop'``, which kills the whole session on a
# transient network blip. To avoid that, we detect these errors
# locally and yield a *normal-looking* model response whose text is a
# friendly diagnostic and whose ``stopReason`` is ``end_turn`` — that
# is the event-loop success path.
class _NetworkUnreachable(Exception):
    """Ollama host is unreachable (DNS / refused / timeout / offline).

    Wrapping the underlying ``httpx`` / ``OSError`` exception lets the
    patched stream handle every transport failure uniformly without
    importing httpx at module scope (the SDK depends on httpx but the
    PoC does not).
    """


def _classify_network_error(exc: BaseException) -> _NetworkUnreachable | None:
    """Map a low-level transport exception to ``_NetworkUnreachable``.

    Returns ``None`` for exceptions we don't recognise — those should
    keep propagating so strands / the user can see the real cause.
    """
    # Lazy import: ollama SDK pulls httpx, but we don't want llm.py to
    # hard-require httpx when only the patched stream ever sees it.
    try:
        import httpx
    except ImportError:  # pragma: no cover - ollama SDK always pulls httpx
        httpx = None  # type: ignore[assignment]

    if httpx is not None and isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return _NetworkUnreachable(str(exc) or type(exc).__name__)

    # socket.gaierror (subclass of OSError) on plain DNS failures.
    # ConnectionRefusedError is also OSError — covers "Ollama not running".
    if isinstance(exc, OSError) and not isinstance(exc, NotADirectoryError):
        return _NetworkUnreachable(str(exc) or type(exc).__name__)

    return None


def _friendly_unreachable_message(host: str, exc: Exception) -> str:
    """Build the user-facing diagnostic string surfaced as model text.

    Args:
        host: ``self.host`` of the ``OllamaModel`` instance, e.g.
            ``"http://10.0.0.5:11434"``.
        exc: The original transport exception (or our wrapper).
    """
    detail = str(exc) or type(exc).__name__
    # Trim unhelpful platform-specific noise (e.g. "[Errno 11001]") but
    # keep the human-readable part so users can search for it.
    import re as _re

    detail_clean = _re.sub(r"^\[Errno -?\d+\]\s*", "", detail).strip()
    return (
        f"Cannot reach Ollama at {host}: {detail_clean}. "
        f"Check (1) OLLAMA_BASE_URL in .env, "
        f"(2) the host is online and Ollama is running there, "
        f"and (3) your network/DNS can resolve the hostname."
    )


def _yield_unreachable_response(self, exc: Exception):
    """Yield a normal-looking assistant message reporting the network error.

    The event loop sees a ``messageStart`` → ``contentBlockDelta`` →
    ``messageStop`` (``stopReason='end_turn'``) sequence and treats it
    as a successful model completion — no ``ForceStopEvent``.
    """
    host = getattr(self, "host", "<unknown host>")
    text = _friendly_unreachable_message(host, exc)
    yield self.format_chunk({"chunk_type": "message_start"})
    yield self.format_chunk({"chunk_type": "content_start", "data_type": "text"})
    yield self.format_chunk(
        {"chunk_type": "content_delta", "data_type": "text", "data": text},
    )
    yield self.format_chunk({"chunk_type": "content_stop", "data_type": "text"})
    yield self.format_chunk({"chunk_type": "message_stop", "data": "end_turn"})


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
        global _ollama_debug_call_counter
        import json as _json

        import ollama as _ollama_pkg
        from strands.models.ollama import (
            ContextWindowOverflowException,
            warn_on_tool_choice_not_supported,
        )

        warn_on_tool_choice_not_supported(tool_choice)

        # Delegate request formatting to upstream — keeps parity with
        # whatever Strands's current shape is (options/keep_alive/etc.).
        request = self.format_request(messages, tool_specs, system_prompt)

        # OLLAMA-SPECIFIC: convert role='tool' to role='user'.
        # strands/models/ollama.py:_format_request_message_contents emits tool
        # results with role='tool' (OpenAI/Anthropic convention), but Ollama's
        # chat API only accepts user/assistant/system. After tool results in
        # role='tool' with no follow-up user message, Ollama returns 500
        # "no user query found in messages". Demote them to 'user' so Ollama
        # sees the tool results as continuation user input.
        for _m in request.get("messages", []):
            if _m.get("role") == "tool":
                _m["role"] = "user"
            elif _m.get("role") == "assistant" and "tool_calls" in _m:
                # Ollama expects content="" alongside tool_calls, not missing.
                _m.setdefault("content", "")

        # --- DEBUG: log the actual formatted request that goes to Ollama ---
        if _ollama_debug_handler is not None:
            try:
                fmt_messages = request.get("messages", [])
                fmt_roles = []
                fmt_summary = []
                for m in fmt_messages:
                    r = m.get("role", "?")
                    fmt_roles.append(r)
                    c = m.get("content")
                    tc = m.get("tool_calls")
                    if tc:
                        names = [t.get("function", {}).get("name", "?") for t in tc]
                        fmt_summary.append(f"{r}+tool_calls({names})")
                    elif isinstance(c, str):
                        fmt_summary.append(f"{r}(text,len={len(c)})")
                    else:
                        fmt_summary.append(f"{r}(content_type={type(c).__name__})")
                _ollama_debug_log.info(_json.dumps({
                    "_type": "formatted_request",
                    "call": _ollama_debug_call_counter,
                    "n_formatted": len(fmt_messages),
                    "formatted_roles": fmt_roles,
                    "formatted_summary": fmt_summary,
                }, ensure_ascii=False))
            except Exception as _e:
                _ollama_debug_log.info("# formatted-request log error: %s", _e)

        # --- DEBUG: log per-call messages structure ---
        if _ollama_debug_handler is not None:
            _ollama_debug_call_counter += 1
            try:
                roles = []
                content_kinds = []  # parallel list: per-message content summary
                for m in (messages or []):
                    role = m.get("role", "?") if isinstance(m, dict) else getattr(m, "role", "?")
                    roles.append(role)
                    content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                    if isinstance(content, list):
                        kinds = []
                        for blk in content:
                            if isinstance(blk, dict):
                                if "text" in blk:
                                    kinds.append(f"text({len(str(blk['text']))})")
                                elif "toolResult" in blk:
                                    tr = blk["toolResult"]
                                    status = tr.get("status", "?") if isinstance(tr, dict) else "?"
                                    text_len = 0
                                    if isinstance(tr, dict):
                                        for c in tr.get("content", []) or []:
                                            if isinstance(c, dict) and "text" in c:
                                                text_len += len(str(c["text"]))
                                    kinds.append(f"toolResult(status={status},len={text_len})")
                                elif "toolUse" in blk:
                                    tu = blk["toolUse"]
                                    name = tu.get("name", "?") if isinstance(tu, dict) else "?"
                                    kinds.append(f"toolUse({name})")
                                else:
                                    first_key = next(iter(blk.keys()), None) if blk else None
                                    kinds.append(first_key or "empty")
                            else:
                                kinds.append(type(blk).__name__)
                        content_kinds.append(kinds)
                    elif isinstance(content, str):
                        content_kinds.append([f"str(len={len(content)})"])
                    else:
                        content_kinds.append([type(content).__name__])
                _ollama_debug_log.info(_json.dumps({
                    "call": _ollama_debug_call_counter,
                    "host": getattr(self, "host", "?"),
                    "n_messages": len(messages or []),
                    "roles": roles,
                    "content_kinds": content_kinds,
                }, ensure_ascii=False))
            except Exception as _e:
                _ollama_debug_log.info("# debug-log error: %s", _e)

        tool_requested = False
        last_event = None

        client = _ollama_pkg.AsyncClient(self.host, **self.client_args)
        try:
            response = await client.chat(**request)
            yield self.format_chunk({"chunk_type": "message_start"})
            yield self.format_chunk({"chunk_type": "content_start", "data_type": "text"})

            stop_reason_seen: str | None = None
            async for event in response:
                # Capture model's final stop_reason for the debug log.
                sr = getattr(event, "done_reason", None) or getattr(event, "stop_reason", None)
                if sr:
                    stop_reason_seen = sr
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

            if _ollama_debug_handler is not None:
                try:
                    n_tools = 0
                    if last_event is not None and tool_requested:
                        n_tools = len(getattr(last_event.message, "tool_calls", []) or [])
                    _ollama_debug_log.info(_json.dumps({
                        "_type": "response_done",
                        "call": _ollama_debug_call_counter,
                        "stop_reason": stop_reason_seen or "?",
                        "n_tool_calls": n_tools,
                    }, ensure_ascii=False))
                except Exception:
                    pass
        except _ollama_pkg.ResponseError as error:
            if any(message in str(error).lower() for message in self.OVERFLOW_MESSAGES):
                raise ContextWindowOverflowException(str(error)) from error
            raise
        except Exception as error:
            # Connection failure BEFORE we received any model output.
            # Detect transport errors (DNS / refused / timeout) and
            # convert into a normal-looking model response so the event
            # loop ends with stop_reason='end_turn' instead of
            # 'force_stop'. Other exceptions propagate unchanged.
            net_err = _classify_network_error(error)
            if net_err is None:
                raise
            for ev in _yield_unreachable_response(self, net_err):
                yield ev
            return

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
        with contextlib.suppress(AttributeError, TypeError):
            setattr(model, attr, value)

    return model
