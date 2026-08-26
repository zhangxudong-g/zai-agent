"""Middleware 体系实战。

Strands 1.53.0 Middleware 是"洋葱模型"（类似 Express/Koa middleware）：
  - Input: 在 stage 执行前 transform context
  - Wrap: async generator 包裹整个 stage（最灵活）
  - Output: 在 stage 执行后 transform result

3 个 stage（拦截点）：
  - InvokeModelStage: 拦截 LLM 调用
  - ExecuteToolStage: 拦截工具执行
  - AgentStreamStage: 拦截整个 agent() 输出流

涵盖 6 种模式：
  1. Wrap phase - 计时
  2. Input phase - 修改 messages
  3. Output phase - 修改 LLM 结果
  4. 多个 middleware 链式组合（嵌套顺序）
  5. ExecuteToolStage middleware - 工具包装
  6. AgentStreamStage middleware - 最外层包裹

用法：
    python tests/demo_middleware.py --pattern 1
    python tests/demo_middleware.py --all
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _make_model():
    from strands.models.ollama import OllamaModel
    import os
    return OllamaModel(
        host=os.getenv("OLLAMA_BASE_URL", "http://swiftechie.aa0.netvolante.jp:51434").rstrip("/"),
        model_id=os.getenv("OLLAMA_MODEL", "qwen3.8:27b"),
    )


def _reset_logger():
    for name in ("strands", "strands.event_loop", "strands.tools"):
        logging.getLogger(name).setLevel(logging.WARNING)


# ============================================================================
# Pattern 1: Wrap phase - 计时
# ============================================================================
def pattern_1_wrap_timing() -> dict:
    """Wrap phase 计时：在 LLM 调用前后打印耗时。"""
    from strands import Agent
    from strands_tools import calculator
    from strands._middleware.stages import InvokeModelStage

    timings = []

    async def timing_middleware(ctx, next_fn):
        # Wrap phase：进入 → 执行 → 退出
        start = time.time()
        # 在 next_fn 前可以改 ctx
        async for event in next_fn(ctx):
            yield event
        elapsed = time.time() - start
        timings.append({
            "elapsed_s": round(elapsed, 3),
            "messages_count": len(ctx.messages),
        })

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent._middleware_registry.add_middleware(InvokeModelStage, timing_middleware)

    agent("What is 12 + 12?")

    return {
        "timings_recorded": len(timings),
        "first_timing": timings[0] if timings else None,
        "explanation": "Wrap phase 在 model invoke 前后执行，可访问完整 ctx",
    }


# ============================================================================
# Pattern 2: Input phase - 修改 messages
# ============================================================================
def pattern_2_input_inject() -> dict:
    """Input phase：在 LLM 调用前修改 messages。

    用途：动态注入 system context、注入参考资料、token截断等。
    """
    from strands import Agent
    from strands_tools import calculator
    from strands._middleware.stages import InvokeModelStage

    injection_log = []

    def inject_extra_context(ctx):
        # 在 messages 最前面插一条"内部提示"
        ctx.messages.insert(0, {
            "role": "user",
            "content": [{"text": "[INTERNAL HINT] The current date is 2026-08-26."}],
        })
        injection_log.append({"messages_after": len(ctx.messages)})
        return ctx  # 必须返回 ctx

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent._middleware_registry.add_middleware(InvokeModelStage.Input, inject_extra_context)

    agent("What is the year?")

    return {
        "injection_log": injection_log,
        "explanation": "Input phase 在 ctx 传给底层调用前 transform（可改 messages/system_prompt/tool_specs）",
    }


# ============================================================================
# Pattern 3: Output phase - 修改 LLM 结果
# ============================================================================
def pattern_3_output_modify() -> dict:
    """Output phase：在 LLM 返回后修改结果。

    用途：截断敏感内容、添加 metadata、后处理文本。
    """
    from strands import Agent
    from strands_tools import calculator
    from strands._middleware.stages import InvokeModelStage

    modifications = []

    def add_metadata(result):
        # result 是 MiddlewareResult（包装了 ModelStopReason event）
        if result.value and hasattr(result.value, "message"):
            # 在 message.content 第一块前加 audit 标记
            content = result.value.message.get("content", [])
            content.insert(0, {"text": "[AUDITED BY MIDDLEWARE]\n"})
            modifications.append({
                "original_first_block": str(content[1])[:50] if len(content) > 1 else None,
                "added": "[AUDITED BY MIDDLEWARE]\n",
            })
        return result.replace(value=result.value)

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent._middleware_registry.add_middleware(InvokeModelStage.Output, add_metadata)

    result = agent("What is 5 + 5?")

    return {
        "modifications": modifications,
        "final_text_sample": str(result)[:300],
        "explanation": "Output phase 拿到 MiddlewareResult.value（最后一个 event），可改后 replace",
    }


# ============================================================================
# Pattern 4: 多个 middleware 链式组合
# ============================================================================
def pattern_4_chain_composition() -> dict:
    """多个 middleware 按"洋葱模型"嵌套执行：先入后出。"""
    from strands import Agent
    from strands_tools import calculator
    from strands._middleware.stages import InvokeModelStage

    execution_log = []

    async def outer(ctx, next_fn):
        execution_log.append("OUTER: enter")
        async for event in next_fn(ctx):
            yield event
        execution_log.append("OUTER: exit")

    async def middle(ctx, next_fn):
        execution_log.append("MIDDLE: enter")
        async for event in next_fn(ctx):
            yield event
        execution_log.append("MIDDLE: exit")

    async def inner(ctx, next_fn):
        execution_log.append("INNER: enter")
        async for event in next_fn(ctx):
            yield event
        execution_log.append("INNER: exit")

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    # 注册顺序决定嵌套：最后注册的最外层
    agent._middleware_registry.add_middleware(InvokeModelStage, outer)
    agent._middleware_registry.add_middleware(InvokeModelStage, middle)
    agent._middleware_registry.add_middleware(InvokeModelStage, inner)

    agent("What is 2+2?")

    return {
        "execution_log": execution_log,
        "explanation": "洋葱模型：OUTER 进 → MIDDLE 进 → INNER 进 → 调用 → INNER 出 → MIDDLE 出 → OUTER 出",
    }


# ============================================================================
# Pattern 5: ExecuteToolStage middleware
# ============================================================================
def pattern_5_tool_wrap() -> dict:
    """ExecuteToolStage：拦截工具调用。"""
    from strands import Agent
    from strands_tools import calculator
    from strands._middleware.stages import ExecuteToolStage

    tool_logs = []

    async def tool_audit(ctx, next_fn):
        tool_name = ctx.tool_use.get("name")
        tool_input = ctx.tool_use.get("input", {})
        tool_logs.append({"phase": "before", "tool": tool_name, "input": tool_input})
        async for event in next_fn(ctx):
            yield event
        tool_logs.append({"phase": "after", "tool": tool_name})

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent._middleware_registry.add_middleware(ExecuteToolStage, tool_audit)

    agent("Calculate 50 * 2")

    return {
        "tool_logs": tool_logs,
        "explanation": "ExecuteToolStage 拿到 ExecuteToolContext（tool_use, tool, agent 等）",
    }


# ============================================================================
# Pattern 6: AgentStreamStage middleware - 最外层
# ============================================================================
def pattern_6_outer_wrap() -> dict:
    """AgentStreamStage：包裹整个 agent() 输出流。"""
    from strands import Agent
    from strands_tools import calculator
    from strands._middleware.stages import AgentStreamStage

    stream_logs = []

    async def stream_filter(ctx, next_fn):
        stream_logs.append("stream: enter")
        events_filtered = 0
        async for event in next_fn(ctx):
            # 在最外层可以过滤掉 data chunk，实现"静默模式"
            if "data" in event:
                events_filtered += 1
                continue  # 不 yield，丢弃
            yield event
        stream_logs.append({"phase": "exit", "data_filtered": events_filtered})

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent._middleware_registry.add_middleware(AgentStreamStage, stream_filter)

    result = agent("What is 3 * 3?")

    return {
        "stream_logs": stream_logs,
        "final_text_sample": str(result)[:200],
        "explanation": "AgentStreamStage 在最外层，可以过滤/转换整个输出流",
    }


PATTERNS = {
    1: ("Wrap phase 计时", pattern_1_wrap_timing),
    2: ("Input phase 注入", pattern_2_input_inject),
    3: ("Output phase 修改", pattern_3_output_modify),
    4: ("多个 middleware 链", pattern_4_chain_composition),
    5: ("ExecuteToolStage", pattern_5_tool_wrap),
    6: ("AgentStreamStage", pattern_6_outer_wrap),
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


def main():
    parser = argparse.ArgumentParser(description="Middleware 6 种实战")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true")
    group.add_argument("--pattern", type=int, choices=list(PATTERNS.keys()))
    args = parser.parse_args()

    _reset_logger()
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