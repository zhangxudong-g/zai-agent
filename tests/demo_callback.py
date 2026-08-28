"""Callback Handler 6 种实战模式。

涵盖：
  1. 最简 data + current_tool_use 捕获
  2. 工具调用收集器（去重 + 计数）
  3. 类型注解让 HookRegistry 推断事件类型
  4. 多个 callback 同时注册
  5. 过滤：屏蔽特定工具的输出
  6. JSONL 全量落盘

用法：
    python tests/demo_callback.py --pattern 1       # 单个
    python tests/demo_callback.py --pattern 1-3     # 范围
    python tests/demo_callback.py --all             # 全部
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _make_model():
    """构造 Ollama 模型（与 demo.py 一致）。"""
    try:
        from strands_poc.config import get_config  # type: ignore
        from strands_poc.llm import build_ollama_model  # type: ignore
        return build_ollama_model(get_config())
    except ImportError:
        import os

        from strands.models.ollama import OllamaModel
        return OllamaModel(
            host=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
            model_id=os.getenv("OLLAMA_MODEL", "qwen3:7b"),
        )


# ============================================================================
# Pattern 1: 最简 data + current_tool_use 捕获
# ============================================================================
def pattern_1_basic_capture() -> dict:
    """官方 Quickstart 的标准模式：把流式 data 输出 + 工具调用记录。"""
    from strands import Agent
    from strands_tools import calculator

    captured_data: list[str] = []
    tool_uses: list[str] = []

    def cb(**kwargs):
        if "data" in kwargs:
            captured_data.append(kwargs["data"])
        elif "current_tool_use" in kwargs:
            t = kwargs["current_tool_use"]
            if t.get("name") and t["name"] not in tool_uses:
                tool_uses.append(t["name"])

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=cb)
    agent("What is 123 * 456?")

    return {
        "data_chunks": len(captured_data),
        "tool_uses_unique": tool_uses,
        "data_total_chars": sum(len(d) for d in captured_data),
        "first_data_sample": captured_data[0][:50] if captured_data else None,
    }


# ============================================================================
# Pattern 2: 工具调用收集器（去重 + 完整参数）
# ============================================================================
def pattern_2_tool_collector() -> dict:
    """收集所有工具调用：tool name + tool_use_id + input + 状态。"""
    from strands import Agent
    from strands_tools import calculator

    calls = []  # list of dicts

    def cb(**kwargs):
        # 模型决定要调用工具时
        if "current_tool_use" in kwargs:
            t = kwargs["current_tool_use"]
            if t.get("name"):
                calls.append({
                    "phase": "tool_use_start",
                    "tool_use_id": t.get("toolUseId"),
                    "name": t.get("name"),
                    "input": t.get("input", {}),
                })
        # 工具调用结果返回时（data 里有 tool_result）
        if "data" in kwargs:
            data = kwargs["data"]
            # 简化判断：data 含 "Result:" 通常是 calculator 输出
            if "Result:" in data:
                calls.append({"phase": "tool_use_end", "result_preview": data[:80]})

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=cb)
    agent("Calculate (99 + 1) * 25 and tell me the answer")

    return {
        "total_events": len(calls),
        "tool_starts": sum(1 for c in calls if c["phase"] == "tool_use_start"),
        "tool_ends": sum(1 for c in calls if c["phase"] == "tool_use_end"),
        "first_call": calls[0] if calls else None,
    }


# ============================================================================
# Pattern 3: 类型注解让 HookRegistry 推断事件类型（双 callback 模式）
# ============================================================================
def pattern_3_type_annotated() -> dict:
    """演示：把 callback 作为 HookProvider 传入 Agent.hooks 时，
    参数类型注解决定接收什么事件。

    注意：callback_handler 参数虽然也可以用类型注解，但
    其调用方式与 hooks 不同 —— 见 docs/Callback_Handler_实战.md。

    踩坑记录：嵌套函数内的类型注解，get_type_hints 解析不到
    (因为 imports 是局部的)。解决办法有两种：
      A) 把 callback 定义在模块顶层（推荐用于 production）
      B) 在 Agent 创建后手动调 agent.hooks.add_callback(EventType, fn)
    """
    # ---- imports 在模块级，避开局部作用域陷阱 ----
    from strands import Agent
    from strands.hooks import AfterModelCallEvent, BeforeModelCallEvent
    from strands_tools import calculator

    before_model_calls = []
    after_model_calls = []

    # 这两个函数虽然在另一个函数内部定义，但类型注解引用的是模块级 import，
    # Python 通过 __globals__ 链能找到，所以可以工作。
    def on_before_model(event: BeforeModelCallEvent) -> None:
        before_model_calls.append({
            "event_type": type(event).__name__,
            "projected_input_tokens": event.projected_input_tokens,
        })

    def on_after_model(event: AfterModelCallEvent) -> None:
        after_model_calls.append({
            "event_type": type(event).__name__,
            "stop_reason": event.stop_response.stop_reason if event.stop_response else None,
        })

    agent = Agent(
        model=_make_model(),
        tools=[calculator],
        callback_handler=None,
    )
    # 显式注册，避开 infer_event_types 对 nested function 的失败
    agent.hooks.add_callback(BeforeModelCallEvent, on_before_model)
    agent.hooks.add_callback(AfterModelCallEvent, on_after_model)

    agent("What is 2+2?")

    return {
        "before_model_count": len(before_model_calls),
        "after_model_count": len(after_model_calls),
        "before_sample": before_model_calls[0] if before_model_calls else None,
        "after_sample": after_model_calls[0] if after_model_calls else None,
    }


# ============================================================================
# Pattern 4: 多个 callback 同时注册（事件顺序执行）
# ============================================================================
def pattern_4_multiple_callbacks() -> dict:
    """演示：注册多个 callback_handler 风格的函数。
    注意：Agent 只接受一个 callback_handler；要多个，需自己组合。

    模式 A：用 callback_handler 组合多个函数
    模式 B：hooks 列表（每个 hook 是独立回调）
    """
    from strands import Agent
    from strands.hooks import BeforeModelCallEvent
    from strands_tools import calculator

    log_a = []
    log_b = []
    log_c = []

    def cb_a(**kwargs):
        log_a.append("a")

    def cb_b(**kwargs):
        log_b.append("b")

    def composed(**kwargs):
        cb_a(**kwargs)
        cb_b(**kwargs)
        if "current_tool_use" in kwargs and kwargs["current_tool_use"].get("name"):
            log_c.append(f"tool:{kwargs['current_tool_use']['name']}")

    agent = Agent(
        model=_make_model(),
        tools=[calculator],
        callback_handler=composed,
    )
    # 用显式 add_callback 注册 hook（避开类型推断失败）
    def on_before_model(event: BeforeModelCallEvent) -> None:
        log_a.append("hook_a")
        log_b.append("hook_b")

    agent.hooks.add_callback(BeforeModelCallEvent, on_before_model)

    agent("What is 100/4?")

    return {
        "cb_a_calls": len(log_a),
        "cb_b_calls": len(log_b),
        "cb_c_tools": log_c,
    }


# ============================================================================
# Pattern 5: 过滤 - 屏蔽特定工具的输出
# ============================================================================
def pattern_5_filter() -> dict:
    """演示：过滤掉 calculator 工具的 data 输出，只保留文本回复。"""
    from strands import Agent
    from strands_tools import calculator

    visible_data = []
    suppressed_data = []
    in_tool_result = False

    def cb(**kwargs):
        nonlocal in_tool_result
        if "current_tool_use" in kwargs:
            t = kwargs["current_tool_use"]
            if t.get("name") == "calculator":
                in_tool_result = True
        elif "data" in kwargs:
            if in_tool_result:
                suppressed_data.append(kwargs["data"])
                # 工具结果不打印
                in_tool_result = False
            else:
                visible_data.append(kwargs["data"])

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=cb)
    final = agent("What is 7 * 8?")

    return {
        "visible_chars": sum(len(d) for d in visible_data),
        "suppressed_chars": sum(len(d) for d in suppressed_data),
        "visible_sample": "".join(visible_data)[:200],
        "final_stop_reason": final.stop_reason,
    }


# ============================================================================
# Pattern 6: JSONL 全量落盘（审计/回放）
# ============================================================================
def pattern_6_jsonl_logger() -> dict:
    """把所有 callback 事件以 JSONL 格式落盘，用于审计/回放。"""
    import tempfile

    from strands import Agent
    from strands_tools import calculator

    log_path = Path(tempfile.mkdtemp()) / "callback.jsonl"
    seq = 0

    def cb(**kwargs):
        nonlocal seq
        seq += 1
        event = {"seq": seq, "ts": time.time()}
        # 选择性序列化（去掉不可 JSON 的）
        if "data" in kwargs:
            event["kind"] = "data"
            event["payload"] = kwargs["data"]
        elif "current_tool_use" in kwargs:
            t = kwargs["current_tool_use"]
            event["kind"] = "tool_use"
            event["payload"] = {
                "tool_use_id": t.get("toolUseId"),
                "name": t.get("name"),
                "input": t.get("input"),
            }
        elif "message" in kwargs:
            event["kind"] = "message"
            event["payload"] = str(kwargs["message"])[:200]
        else:
            event["kind"] = "other"
            event["payload"] = {k: str(v)[:100] for k, v in kwargs.items()}

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=cb)
    agent("What is 5 * 5?")

    # 读取并汇总
    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    parsed = [json.loads(line) for line in lines]
    kinds = {}
    for p in parsed:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1

    return {
        "log_path": str(log_path),
        "total_events": len(parsed),
        "events_by_kind": kinds,
        "first_event": parsed[0] if parsed else None,
        "last_event": parsed[-1] if parsed else None,
    }


PATTERNS = {
    1: ("最简 data + tool_use 捕获", pattern_1_basic_capture),
    2: ("工具调用收集器（去重）", pattern_2_tool_collector),
    3: ("类型注解 hooks", pattern_3_type_annotated),
    4: ("多 callback 组合", pattern_4_multiple_callbacks),
    5: ("过滤工具结果", pattern_5_filter),
    6: ("JSONL 全量落盘", pattern_6_jsonl_logger),
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
    parser = argparse.ArgumentParser(description="Callback Handler 6 种实战模式")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true")
    group.add_argument("--pattern", type=int, choices=list(PATTERNS.keys()))
    group.add_argument("--range", dest="rng", type=str,
                       help="范围，如 1-3")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    selected = []
    if args.all:
        selected = list(PATTERNS.keys())
    elif args.pattern:
        selected = [args.pattern]
    elif args.rng:
        a, b = args.rng.split("-")
        selected = list(range(int(a), int(b) + 1))

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
