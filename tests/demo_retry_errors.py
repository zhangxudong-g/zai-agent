"""错误处理与重试实战（生产化 3.3）。

涵盖：
  1. 默认 ModelRetryStrategy 对 ModelThrottledException 的指数退避重试（零延迟仿真）
  2. 反例：默认策略**不会**重试非 throttle 异常 + force_stop 事件 + EventLoopException 解包
  3. 自定义 is_retryable 子类扩展重试范围（本环境的 ConnectError）
  4. AfterModelCallEvent 通用重试钩子（无 ModelRetryStrategy 的路径）
  5. SlidingWindowConversationManager 窗口裁剪 + ContextWindowOverflowException 语义
  6. 正常路径 stop_reason 采集 + 异常家族速查

仿真方式：monkeypatch `model.stream`（1.53 的模型入口），前 N 次抛指定异常，
之后委托真实调用 —— 除最后一次外全部离线，重试语义可确定断言。

用法：
    python tests/demo_retry_errors.py --pattern 3
    python tests/demo_retry_errors.py --all
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------


from strands.event_loop._retry import ModelRetryStrategy


class _CallCounter:
    def __init__(self) -> None:
        self.n = 0


def _make_model():
    try:
        from zai.config import get_config  # type: ignore
        from zai.llm import build_ollama_model  # type: ignore

        return build_ollama_model(get_config())
    except ImportError:
        import os

        from strands.models.ollama import OllamaModel

        return OllamaModel(
            host=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
            model_id=os.getenv("OLLAMA_MODEL", "qwen3:7b"),
        )


def patch_flaky(model: Any, counter: _CallCounter, fail_times: int, exc_factory) -> None:
    """让 model.stream 前 fail_times 次调用抛 exc_factory()，之后委托真实 stream。"""
    real_stream = model.stream

    def flaky_stream(*a, **k):
        counter.n += 1
        if counter.n <= fail_times:
            async def failing():
                raise exc_factory()
                yield  # pragma: no cover  (使其成为 async generator)

            return failing()
        return real_stream(*a, **k)

    model.stream = flaky_stream


def _make_agent(model: Any = None, **kwargs: Any) -> Any:
    from strands import Agent

    extras = {"system_prompt": "You answer in one short sentence.", "callback_handler": None}
    extras.update(kwargs)
    return Agent(model=model if model is not None else _make_model(), **extras)


# ============================================================================
# Pattern 1: 默认 ModelRetryStrategy（throttle → 指数退避 → 恢复）
# ============================================================================


def pattern_1_default_throttle_retry() -> dict:
    from strands.event_loop._retry import ModelRetryStrategy
    from strands.types.exceptions import ModelThrottledException

    model = _make_model()
    counter = _CallCounter()
    patch_flaky(model, counter, fail_times=2, exc_factory=lambda: ModelThrottledException("simulated 429"))

    agent = _make_agent(
        model=model,
        retry_strategy=ModelRetryStrategy(max_attempts=6, initial_delay=0, max_delay=0),
    )

    result = agent("Say ok.")
    return {
        "model_calls": counter.n,
        "expected": "2 次失败 + 1 次成功 = 3",
        "final": str(result)[:80],
        "conclusion": "默认策略对 ModelThrottledException 自动指数退避重试（delay: 4→8→16→…→max_delay）",
    }


# ============================================================================
# Pattern 2: 反例 + force_stop + EventLoopException 解包
# ============================================================================


def pattern_2_no_retry_foreign_exception() -> dict:
    from strands.types.exceptions import EventLoopException  # noqa: F401

    model = _make_model()
    counter = _CallCounter()
    patch_flaky(model, counter, fail_times=99, exc_factory=lambda: ConnectionError("simulated connect failure"))

    agent = _make_agent(model=model)  # 默认 ModelRetryStrategy：ConnectionError 不在重试范围

    force_stops: list[dict] = []
    raised: Exception | None = None

    async def _collect() -> None:
        nonlocal raised
        agen = agent.stream_async("Say ok.")
        try:
            async for ev in agen:
                d = ev.as_dict() if hasattr(ev, "as_dict") else ev
                if isinstance(d, dict) and d.get("force_stop"):
                    force_stops.append({k: d.get(k) for k in ("force_stop", "force_stop_reason")})
        except Exception as e:
            raised = e
        finally:
            with contextlib.suppress(Exception):
                await agen.aclose()

    asyncio.run(_collect())

    assert counter.n == 1, f"默认策略不应重试 ConnectionError，实际 model 被调 {counter.n} 次"
    assert raised is not None, "异常应向上抛出"
    orig = getattr(raised, "original_exception", None)
    assert force_stops, "应在 raise 前发出 ForceStopEvent(force_stop=True)"
    return {
        "model_calls": counter.n,
        "raised_type": type(raised).__name__,
        "original_exception_type": type(orig).__name__ if orig else None,
        "unwrapped_message": (str(orig) if orig else str(raised))[:80],
        "force_stop_event": force_stops[0],
        "conclusion": "is_retryable=false 的异常直接穿透；ForceStopEvent(reason) 在 raise 前发出；"
        "EventLoopException.original_exception 保留原始异常",
    }


# ============================================================================
# Pattern 3: 自定义 is_retryable（扩展重试范围）
# ============================================================================


class ConnectRetryStrategy(ModelRetryStrategy):
    """把连接类错误也纳入重试（本环境 WSL DNS 抖动就是这种情况）。"""

    def is_retryable(self, exception: Exception) -> bool:
        return super().is_retryable(exception) or isinstance(exception, (ConnectionError,))


def pattern_3_custom_is_retryable() -> dict:
    from strands.types.exceptions import ModelThrottledException  # noqa: F401

    model = _make_model()
    counter = _CallCounter()
    patch_flaky(model, counter, fail_times=2, exc_factory=lambda: ConnectionError("simulated DNS blip"))

    agent = _make_agent(
        model=model,
        retry_strategy=ConnectRetryStrategy(max_attempts=6, initial_delay=0, max_delay=0),
    )
    result = agent("Say ok.")
    return {
        "model_calls": counter.n,
        "expected": "2 次 ConnectionError + 1 次成功 = 3",
        "final": str(result)[:80],
        "conclusion": "子类只重写 is_retryable()，backoff/状态机全部继承默认策略",
    }


# ============================================================================
# Pattern 4: AfterModelCallEvent 通用重试钩子
# ============================================================================


def pattern_4_hook_generic_retry() -> dict:
    from strands.hooks.events import AfterModelCallEvent

    model = _make_model()
    counter = _CallCounter()
    patch_flaky(model, counter, fail_times=1, exc_factory=lambda: TimeoutError("simulated timeout"))

    retried: list[dict] = []

    def on_after_model_call(event: AfterModelCallEvent) -> None:
        if event.exception is not None and isinstance(event.exception, TimeoutError):
            retried.append({"exc": type(event.exception).__name__, "stop_response": None})
            event.retry = True  # ← 丢弃当前响应，重新调模型（事件循环看到 retry 后 continue）

    agent = _make_agent(model=model, retry_strategy=None)
    agent.hooks.add_callback(AfterModelCallEvent, on_after_model_call)

    result = agent("Say ok.")
    assert counter.n == 2, f"应 1 失败 + 1 成功，实际 {counter.n}"
    return {
        "model_calls": counter.n,
        "retry_hook_fired": len(retried) == 1,
        "saw_exception_in_hook": retried[0]["exc"] if retried else None,
        "final": str(result)[:80],
        "conclusion": "hook 里 event.retry=True 即可对**任意异常**重试；注意流式消费者会先看到被丢弃响应的碎片事件",
    }


# ============================================================================
# Pattern 5: SlidingWindow 窗口裁剪 + 溢出语义
# ============================================================================


def pattern_5_sliding_window() -> dict:
    from strands.agent.conversation_manager import SlidingWindowConversationManager
    from strands.types.exceptions import ContextWindowOverflowException  # noqa: F401

    window = SlidingWindowConversationManager(window_size=4)
    agent = _make_agent(model=_make_model(), conversation_manager=window)

    for i in range(3):
        agent(f"turn {i}: 记一个词：word{i}. 只答 ok.")
        after = len(agent.messages)
        if after <= 4 and i >= 1:
            break

    state = window.get_state()
    return {
        "n_turns": 3,
        "final_messages_len": len(agent.messages),
        "manager_state": state if isinstance(state, (str, int, float, bool, type(None))) else str(state)[:120],
        "overflow_exception": "ContextWindowOverflowException：reduce_context 无法继续裁剪时（如工具配对打散）抛出，"
        "agent 会捕获并走 force_stop；1.53 中 Ollama provider 命中 OVERFLOW_MESSAGES 文案也映射到它",
        "conclusion": "window_size 按**消息条数**裁剪（1.53 语义；不是 token），pin_first 可固定系统/首条",
    }


# ============================================================================
# Pattern 6: 正常 stop_reason + 异常家族速查
# ============================================================================


def pattern_6_stop_reason_taxonomy() -> dict:
    from strands.types import exceptions as ex

    agent = _make_agent()
    result = agent("Reply with exactly: ok.")

    family = {cls.__name__ for cls in (
        ex.EventLoopException,
        ex.ModelThrottledException,
        ex.MaxTokensReachedException,
        ex.ContextWindowOverflowException,
        ex.SessionException,
        ex.MCPClientInitializationError,
    )}
    return {
        "stop_reason": result.stop_reason,
        "final_message": str(result)[:60],
        "exception_family": sorted(family),
        "handling_map": {
            "ModelThrottledException": "默认重试（指数退避）",
            "ConnectionError 类": "自行扩展 is_retryable（Pattern 3）",
            "MaxTokensReachedException": "输出被截断；result.message 已含部分文本，可继续对话续写",
            "ContextWindowOverflowException": "上下文超窗且裁剪失败；换 summarizing conversation manager 或减小窗口",
            "EventLoopException": "事件循环兜底包装；unwrap .original_exception",
        },
    }


PATTERNS = {
    1: ("默认 throttle 重试", pattern_1_default_throttle_retry),
    2: ("反例 + force_stop 解包", pattern_2_no_retry_foreign_exception),
    3: ("自定义 is_retryable", pattern_3_custom_is_retryable),
    4: ("hook 通用重试", pattern_4_hook_generic_retry),
    5: ("滑动窗口裁剪", pattern_5_sliding_window),
    6: ("stop_reason + 异常家族", pattern_6_stop_reason_taxonomy),
}


def run_one(n: int) -> tuple[bool, dict]:
    name, fn = PATTERNS[n]
    print(f"\n{'=' * 70}")
    print(f"[Pattern {n}] {name}")
    print("=" * 70)
    start = time.time()
    try:
        payload = fn()
        elapsed = time.time() - start
        print("--- Result ---")
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        print(f"[Pattern {n}] PASS ({elapsed:.1f}s)")
        return True, payload
    except Exception as e:
        elapsed = time.time() - start
        print(f"[Pattern {n}] FAIL ({elapsed:.1f}s): {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return False, {"error": str(e), "type": type(e).__name__}


def main() -> int:
    parser = argparse.ArgumentParser(description="错误处理与重试 6 种实战")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true")
    group.add_argument("--pattern", type=int, choices=list(PATTERNS.keys()))
    args = parser.parse_args()

    selected = list(PATTERNS.keys()) if args.all else [args.pattern]
    results = {}
    for n in selected:
        ok, payload = run_one(n)
        results[n] = (ok, payload)

    passed = sum(1 for ok, _ in results.values() if ok)
    print(f"\n{'=' * 70}")
    print(f"结果：{passed}/{len(selected)} passed")
    print("=" * 70)
    return 0 if passed == len(selected) else 1


if __name__ == "__main__":
    sys.exit(main())
