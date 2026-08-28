"""Hooks 体系实战。

涵盖 Strands 1.53.0 的全部主要 hook 事件类型：
  1. BeforeModelCallEvent - 取消 / 修改输入
  2. AfterModelCallEvent - 触发重试
  3. BeforeToolCallEvent - 工具参数校验 / 拒绝
  4. AfterToolCallEvent - 修改工具结果
  5. BeforeInvocationEvent / AfterInvocationEvent - 会话级
  6. MessageAddedEvent - 消息历史
  7. HookOrder - 优先级排序

用法：
    python tests/demo_hooks.py --pattern 1
    python tests/demo_hooks.py --all
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _make_model():
    import os

    from strands.models.ollama import OllamaModel
    return OllamaModel(
        host=os.getenv("OLLAMA_BASE_URL", "http://swiftechie.aa0.netvolante.jp:51434").rstrip("/"),
        model_id=os.getenv("OLLAMA_MODEL", "qwen3.8:27b"),
    )


def _reset_logger():
    import logging
    for name in ("strands", "strands.event_loop", "strands.tools"):
        logging.getLogger(name).setLevel(logging.WARNING)


# ============================================================================
# Pattern 1: BeforeModelCallEvent - 取消 / 修改输入
# ============================================================================
def pattern_1_before_model() -> dict:
    """在调用模型前拦截。可以：cancel（取消）/ 读取 projected_input_tokens。

    关键字段：
      - event.cancel: bool（True 取消）
      - event.projected_input_tokens: int（估算的输入 token 数）
      - event.invocation_state: dict（可写，会传给后续 hook）
    """
    from strands import Agent
    from strands.hooks import BeforeModelCallEvent
    from strands_tools import calculator

    cancel_count = []
    projected_tokens = []

    def maybe_cancel(event: BeforeModelCallEvent) -> None:
        projected_tokens.append(event.projected_input_tokens)
        # 演示：如果 projected tokens 超过 5000 就取消
        if event.projected_input_tokens and event.projected_input_tokens > 5000:
            event.cancel = True
            cancel_count.append(event.projected_input_tokens)

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent.hooks.add_callback(BeforeModelCallEvent, maybe_cancel)

    # 跑一个短提示（不会触发取消）
    result = agent("What is 1+1?")

    return {
        "projected_tokens_per_call": projected_tokens,
        "cancel_count": len(cancel_count),
        "stop_reason": result.stop_reason,
        "explanation": "BeforeModelCallEvent.cancel=True 会让模型返回 end_turn 而不调真实 LLM",
    }


# ============================================================================
# Pattern 2: AfterModelCallEvent - 触发重试
# ============================================================================
def pattern_2_after_model_retry() -> dict:
    """AfterModelCallEvent.retry=True 会让 event loop 重跑同一次 LLM 调用。

    用途：让模型重新生成（强制多模思考、修复 JSON 格式等）。

    注意：1.53.0 中 retry 标志会触发带 backoff 的重试。
    """
    from strands import Agent
    from strands.hooks import AfterModelCallEvent
    from strands_tools import calculator

    retry_count = []

    def maybe_retry(event: AfterModelCallEvent) -> None:
        # 仅在第一次调用时强制重试（演示用）
        if len(retry_count) < 1:
            retry_count.append("retry_triggered")
            event.retry = True

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent.hooks.add_callback(AfterModelCallEvent, maybe_retry)

    result = agent("What is 2+2?")

    return {
        "retry_triggered": retry_count,
        "stop_reason": result.stop_reason,
        "cycle_count": result.metrics.cycle_count,
        "explanation": "retry 触发了 1 次额外 cycle",
    }


# ============================================================================
# Pattern 3: BeforeToolCallEvent - 工具参数校验 / 拒绝
# ============================================================================
def pattern_3_before_tool() -> dict:
    """在工具执行前拦截。可以：cancel_tool / 修改 tool_use。

    关键字段：
      - event.cancel_tool: bool（True 取消）
      - event.tool_use: dict（可改 input）
      - event.selected_tool: 当前选中的工具（可换）
    """
    from strands import Agent
    from strands.hooks import BeforeToolCallEvent
    from strands_tools import calculator

    cancel_count = []
    rejected_calcs = []

    def validate_calculator(event: BeforeToolCallEvent) -> None:
        tu = event.tool_use
        # 1. 只对 calculator 工具做校验
        if tu.get("name") == "calculator":
            expr = tu.get("input", {}).get("expression", "")
            # 2. 拒绝包含 'rm ' 或 'import os' 的表达式（危险操作）
            if "rm " in expr or "import os" in expr:
                event.cancel_tool = True
                rejected_calcs.append(expr)
            # 3. 改写：把 'sqrt' 写成 'sqrt(1)' 避免除零（演示修改）
            elif "sqrt(0)" in expr:
                tu["input"]["expression"] = expr.replace("sqrt(0)", "sqrt(1)")

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent.hooks.add_callback(BeforeToolCallEvent, validate_calculator)

    # 跑正常调用
    result = agent("Calculate sqrt(16) + 2")

    return {
        "cancel_count": len(cancel_count),
        "rejected_calcs": rejected_calcs,
        "stop_reason": result.stop_reason,
        "tool_metrics": {
            name: {"call_count": s.call_count, "success_count": s.success_count}
            for name, s in result.metrics.tool_metrics.items()
        },
    }


# ============================================================================
# Pattern 4: AfterToolCallEvent - 修改工具结果
# ============================================================================
def pattern_4_after_tool_modify() -> dict:
    """在工具执行后修改结果。可以：覆盖 result / 触发 retry。"""
    from strands import Agent
    from strands.hooks import AfterToolCallEvent
    from strands_tools import calculator

    modifications = []

    def modify_result(event: AfterToolCallEvent) -> None:
        tu = event.tool_use
        if tu.get("name") == "calculator" and event.result:
            # 拿到原始结果，包装一层
            content = event.result.get("content", [])
            if content and isinstance(content[0], dict):
                original = content[0].get("text", "")
                modified = f"[AUDITED] {original}"
                # 直接修改 event.result 的内容
                event.result["content"] = [{"text": modified}]
                modifications.append({"original": original[:50], "modified": modified[:50]})

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent.hooks.add_callback(AfterToolCallEvent, modify_result)

    result = agent("Calculate 5*5")

    return {
        "modifications": modifications,
        "final_text_sample": str(result)[:300],
        "stop_reason": result.stop_reason,
    }


# ============================================================================
# Pattern 5: BeforeInvocationEvent / AfterInvocationEvent - 会话级
# ============================================================================
def pattern_5_invocation_events() -> dict:
    """InvocationEvent 在每次 agent() 调用前后触发（session-level）。"""
    from strands import Agent
    from strands.hooks import AfterInvocationEvent, BeforeInvocationEvent
    from strands_tools import calculator

    invocations = []

    def before_inv(event: BeforeInvocationEvent) -> None:
        invocations.append({
            "phase": "before",
            "agent_id": event.agent.agent_id,
        })

    def after_inv(event: AfterInvocationEvent) -> None:
        invocations.append({
            "phase": "after",
            "stop_reason": str(event.stop_reason) if hasattr(event, "stop_reason") else "n/a",
        })

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent.hooks.add_callback(BeforeInvocationEvent, before_inv)
    agent.hooks.add_callback(AfterInvocationEvent, after_inv)

    # 跑 2 次调用
    agent("What is 1+1?")
    agent("What is 2+2?")

    return {
        "invocation_count": len(invocations),
        "invocations": invocations,
        "explanation": "每次 agent(prompt) 触发一对 before/after",
    }


# ============================================================================
# Pattern 6: MessageAddedEvent - 消息历史追踪
# ============================================================================
def pattern_6_message_added() -> dict:
    """MessageAddedEvent 在 messages 列表添加新消息时触发。"""
    from strands import Agent
    from strands.hooks import MessageAddedEvent
    from strands_tools import calculator

    messages_log = []

    def track_message(event: MessageAddedEvent) -> None:
        msg = event.message
        messages_log.append({
            "role": msg.get("role"),
            "content_types": [
                list(b.keys()) for b in msg.get("content", [])
                if isinstance(b, dict)
            ],
        })

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent.hooks.add_callback(MessageAddedEvent, track_message)

    agent("Calculate 7 * 8")

    return {
        "messages_added_count": len(messages_log),
        "messages": messages_log,
        "explanation": "通常会看到 user (prompt) + assistant (tool_use) + user (tool_result) + assistant (text)",
    }


# ============================================================================
# Pattern 7: HookOrder - 优先级
# ============================================================================
def pattern_7_hook_order() -> dict:
    """演示 HookOrder 控制多个 hook 的执行顺序。

    HookOrder 预设值：
      - SDK_FIRST = -100
      - INTERVENTION_OUTPUT = -90
      - DEFAULT = 0
      - MODEL_ROUTING = 50
      - INTERVENTION_INPUT = 90
      - SDK_LAST = 100

    数字越小越先执行。
    """
    from strands import Agent
    from strands.hooks import BeforeModelCallEvent, HookOrder
    from strands_tools import calculator

    execution_order = []

    def high_priority(event: BeforeModelCallEvent) -> None:
        execution_order.append("SDK_FIRST(-100)")

    def default_priority(event: BeforeModelCallEvent) -> None:
        execution_order.append("DEFAULT(0)")

    def low_priority(event: BeforeModelCallEvent) -> None:
        execution_order.append("SDK_LAST(100)")

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    # 注意 add_callback 的 order 参数
    agent.hooks.add_callback(BeforeModelCallEvent, high_priority, order=HookOrder.SDK_FIRST)
    agent.hooks.add_callback(BeforeModelCallEvent, default_priority, order=HookOrder.DEFAULT)
    agent.hooks.add_callback(BeforeModelCallEvent, low_priority, order=HookOrder.SDK_LAST)

    agent("What is 3+3?")

    return {
        "execution_order": execution_order,
        "hook_count": len(execution_order),
        "explanation": "每次 BeforeModelCallEvent 触发都按 SDK_FIRST → DEFAULT → SDK_LAST 顺序",
    }


PATTERNS = {
    1: ("BeforeModelCallEvent - 取消", pattern_1_before_model),
    2: ("AfterModelCallEvent - 重试", pattern_2_after_model_retry),
    3: ("BeforeToolCallEvent - 校验", pattern_3_before_tool),
    4: ("AfterToolCallEvent - 修改结果", pattern_4_after_tool_modify),
    5: ("Invocation 事件", pattern_5_invocation_events),
    6: ("MessageAddedEvent", pattern_6_message_added),
    7: ("HookOrder 优先级", pattern_7_hook_order),
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
    parser = argparse.ArgumentParser(description="Hooks 7 种实战")
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
