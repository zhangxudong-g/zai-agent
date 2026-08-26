"""AgentResult 字段详解实战。

涵盖：
  1. 顶层结构 (stop_reason, message, metrics, state, interrupts, structured_output, checkpoint)
  2. message.content 的 blocks (text / toolUse / toolResult)
  3. stop_reason 取值（end_turn / tool_use / max_tokens 等）
  4. state 字段（事件循环累积状态）
  5. message 字段的 to_dict / 序列化
  6. structured_output（Pydantic 集成）
  7. context_size property

用法：
    python tests/demo_agent_result.py --pattern 1
    python tests/demo_agent_result.py --all
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _make_model():
    try:
        from strands_poc.config import get_config  # type: ignore
        from strands_poc.llm import build_ollama_model  # type: ignore
        return build_ollama_model(get_config())
    except ImportError:
        from strands.models.ollama import OllamaModel
        import os
        return OllamaModel(
            host=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
            model_id=os.getenv("OLLAMA_MODEL", "qwen3:7b"),
        )


# ============================================================================
# Pattern 1: 顶层结构（dataclass 字段列表）
# ============================================================================
def pattern_1_top_level() -> dict:
    """探索 AgentResult 的所有顶层字段。"""
    import dataclasses
    from strands import Agent
    from strands_tools import calculator

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    result = agent("What is 12 * 12?")

    # 1. dataclass 字段（真实定义）
    fields = [f.name for f in dataclasses.fields(result.__class__)]

    # 2. 实际值（采样）
    values = {}
    for f in fields:
        v = getattr(result, f, None)
        if v is None:
            values[f] = None
        elif isinstance(v, (str, int, float, bool)):
            values[f] = v
        elif dataclasses.is_dataclass(v):
            values[f] = f"<{type(v).__name__} dataclass>"
        elif hasattr(v, "__len__"):
            values[f] = f"<{type(v).__name__} len={len(v)}>"
        else:
            values[f] = f"<{type(v).__name__}>"

    # 3. 顶层 properties
    properties = []
    for attr in dir(result):
        if isinstance(getattr(type(result), attr, None), property):
            properties.append(attr)

    return {
        "dataclass_fields": fields,
        "field_values": values,
        "properties": properties,
        "class_module": type(result).__module__,
    }


# ============================================================================
# Pattern 2: message.content 的 block 类型
# ============================================================================
def pattern_2_message_blocks() -> dict:
    """探索 message.content 的所有 block 类型。"""
    from strands import Agent
    from strands_tools import calculator

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    result = agent("Calculate 25 * 4 and tell me what you got.")

    msg = result.message
    content = msg.get("content", [])

    block_types = {}  # type → 第一个 sample
    for i, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        for key in block.keys():
            if key not in block_types:
                block_types[key] = {
                    "first_index": i,
                    "sample": str(block[key])[:200],
                }

    # 角色
    return {
        "role": msg.get("role"),
        "content_length": len(content),
        "block_types_found": list(block_types.keys()),
        "block_details": block_types,
        "full_message_preview": str(msg)[:500],
    }


# ============================================================================
# Pattern 3: stop_reason 取值
# ============================================================================
def pattern_3_stop_reason() -> dict:
    """枚举 stop_reason 的所有可能取值。"""
    from strands.types.streaming import StopReason
    import dataclasses
    from strands import Agent
    from strands_tools import calculator

    # 1. StopReason 类型定义
    sr_definition = None
    try:
        # StopReason 是 Literal 类型
        import typing
        sr_definition = str(typing.get_args(StopReason))
    except Exception:
        pass

    # 2. 跑一个普通回合
    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    result = agent("What is 5 + 5?")
    observed = result.stop_reason

    # 3. 跑一个工具调用回合
    result2 = agent("Calculate 50 * 2")
    observed_with_tool = result2.stop_reason

    return {
        "stop_reason_definition": sr_definition,
        "observed_normal_turn": observed,
        "observed_tool_turn": observed_with_tool,
        "type_of_value": type(observed).__name__,
    }


# ============================================================================
# Pattern 4: state 字段（事件循环累积状态）
# ============================================================================
def pattern_4_state_field() -> dict:
    """state 字段是事件循环的累积状态，可被 plugin/middleware 写入。

    注意：1.53.0 中 state 是 JSONSerializableDict，不是普通 dict！
    必须用 .set(key, value) / .get(key)，不能用 state[key] = value。
    值必须是 JSON-serializable（str / int / float / bool / list / dict / None）。
    """
    from strands import Agent
    from strands_tools import calculator

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)

    # 通过 hook 写入 state（用正确的 .set API）
    from strands.hooks import BeforeModelCallEvent
    def inject_state(event: BeforeModelCallEvent) -> None:
        event.agent.state.set("trace_id", "demo-trace-001")
        event.agent.state.set("user_id", "user-42")
        event.agent.state.set("count", 3)  # int 也 OK
        event.agent.state.set("tags", ["a", "b"])  # list 也 OK

    agent.hooks.add_callback(BeforeModelCallEvent, inject_state)
    result = agent("What is 7 * 8?")

    state = result.state
    # 用 .get() 而非 dict()
    all_data = state.get() if state else {}

    return {
        "state_type": type(state).__name__,
        "state_keys": list(all_data.keys()),
        "state_values": {k: str(v)[:100] for k, v in all_data.items()},
        "agent_state_after": agent.state.get() if hasattr(agent, "state") else None,
        "explanation": "JSONSerializableDict 有版本号、强制 JSON 验证，比 dict 更安全",
    }


# ============================================================================
# Pattern 5: message 的序列化（to_dict / JSON）
# ============================================================================
def pattern_5_message_serialize() -> dict:
    """message 字段可以 to_dict 序列化（看 dict 结构）。"""
    from strands import Agent
    from strands_tools import calculator

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    result = agent("What is 9 * 9?")

    # AgentResult 本身有 to_dict
    if hasattr(result, "to_dict"):
        result_dict = result.to_dict()
    else:
        result_dict = {"message": result.message, "stop_reason": result.stop_reason}

    # message 转 dict
    msg_dict = result.message if isinstance(result.message, dict) else dict(result.message)

    # 尝试 JSON 序列化（验证可序列化）
    try:
        json_str = json.dumps(msg_dict, ensure_ascii=False, default=str)
        json_serializable = True
        json_size = len(json_str)
    except (TypeError, ValueError) as e:
        json_serializable = False
        json_size = 0
        json_str = str(e)

    return {
        "result_dict_keys": list(result_dict.keys()),
        "message_dict_keys": list(msg_dict.keys()),
        "json_serializable": json_serializable,
        "json_size_bytes": json_size,
        "json_preview": json_str[:300] if json_serializable else None,
    }


# ============================================================================
# Pattern 6: structured_output（Pydantic 集成）
# ============================================================================
def pattern_6_structured_output() -> dict:
    """用 Pydantic 模型作为 structured_output_model，让 Agent 输出结构化数据。"""
    from pydantic import BaseModel
    from strands import Agent
    from strands_tools import calculator

    class MathResult(BaseModel):
        question: str
        answer: int
        explanation: str

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    # 用 structured_output_model 强制输出符合 schema
    try:
        result = agent(
            "Calculate 100 * 25 and return the answer with explanation",
            structured_output_model=MathResult,
        )
        return {
            "structured_output_type": type(result.structured_output).__name__,
            "structured_output": result.structured_output.model_dump() if result.structured_output else None,
            "stop_reason": result.stop_reason,
        }
    except Exception as e:
        return {
            "error": str(e)[:300],
            "type": type(e).__name__,
            "explanation": "Ollama 可能不支持 tool_choice 强制结构化输出",
        }


# ============================================================================
# Pattern 7: context_size property
# ============================================================================
def pattern_7_context_size() -> dict:
    """context_size 和 projected_context_size 是 AgentResult 的 properties。"""
    from strands import Agent
    from strands_tools import calculator

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    result = agent("What is 11 * 11?")

    return {
        "context_size": result.context_size,
        "projected_context_size": result.projected_context_size,
        "metrics_cycle_count": result.metrics.cycle_count,
        "metrics_latest_context_size": result.metrics.latest_context_size,
        "metrics_projected_context_size": result.metrics.projected_context_size,
        "explanation": {
            "context_size": "最近一次 LLM 调用的输入 token 数",
            "projected_context_size": "预计下一轮 inputTokens + outputTokens",
        },
    }


PATTERNS = {
    1: ("顶层结构", pattern_1_top_level),
    2: ("message blocks", pattern_2_message_blocks),
    3: ("stop_reason 取值", pattern_3_stop_reason),
    4: ("state 字段", pattern_4_state_field),
    5: ("message 序列化", pattern_5_message_serialize),
    6: ("structured_output", pattern_6_structured_output),
    7: ("context_size property", pattern_7_context_size),
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
    parser = argparse.ArgumentParser(description="AgentResult 字段 7 种实战")
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