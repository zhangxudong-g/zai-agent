"""Multi-Agent 体系实战。

Strands 1.53.0 提供 4 种多 Agent 模式：
  - Swarm: 自组织协作，agents 之间 handoff
  - Graph: 显式 DAG，节点 + 边 + 条件
  - Agent.as_tool: 把 Agent 当工具用（最简单）
  - A2A: 跨进程的 Agent-to-Agent 协议

涵盖 6 种模式：
  1. Swarm - 2 agents handoff
  2. Swarm - 共享上下文
  3. Graph - 顺序管道
  4. Graph - 条件分支
  5. Agent.as_tool - 子 Agent 工具
  6. SwarmResult 字段探索

用法：
    python tests/demo_multi_agent.py --pattern 1
    python tests/demo_multi_agent.py --all
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
    import os

    from strands.models.ollama import OllamaModel

    return OllamaModel(
        host=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
        model_id=os.getenv("OLLAMA_MODEL", "qwen3.8:27b"),
    )


def _reset_logger():
    for name in ("strands", "strands.event_loop", "strands.tools", "strands.multiagent"):
        logging.getLogger(name).setLevel(logging.WARNING)


# ============================================================================
# Pattern 1: Swarm - 2 agents handoff
# ============================================================================
def pattern_1_swarm_basic() -> dict:
    """两个 Agent 协作：一个回答，一个校对。"""
    from strands import Agent
    from strands.multiagent import Swarm

    researcher = Agent(
        model=_make_model(),
        system_prompt="你是一名研究员。回答用户问题，提供事实信息。",
        name="researcher",
    )
    reviewer = Agent(
        model=_make_model(),
        system_prompt="你是一名审稿人。审查 researcher 的回答，指出问题。",
        name="reviewer",
    )

    # Swarm 自动组织 handoff
    swarm = Swarm([researcher, reviewer], entry_point=researcher, max_handoffs=3)

    result = swarm("法国的首都是什么？")

    return {
        "status": str(result.status),
        "node_history": [n.node_id for n in result.node_history],
        "results_count": len(result.results),
        "final_answer_sample": str(result)[:200] if result else None,
    }


# ============================================================================
# Pattern 2: Swarm - 共享上下文
# ============================================================================
def pattern_2_swarm_shared() -> dict:
    """3 个 agent 通过 shared_context 协作。"""
    from strands import Agent
    from strands.multiagent import Swarm

    planner = Agent(
        model=_make_model(),
        system_prompt="你是规划师。把任务分解成步骤。不要用 emoji。",
        name="planner",
    )
    executor = Agent(
        model=_make_model(),
        system_prompt="你是执行者。按照规划完成任务。",
        name="executor",
    )
    summarizer = Agent(
        model=_make_model(),
        system_prompt="你是总结者。把执行结果总结成一段话。不要用 emoji。",
        name="summarizer",
    )

    swarm = Swarm([planner, executor, summarizer], entry_point=planner, max_handoffs=5)

    result = swarm("写一个 Python 函数计算斐波那契数列")

    return {
        "status": str(result.status),
        "node_history": [n.node_id for n in result.node_history],
        "agents_executed": len(result.node_history),
        "final_answer_sample": str(result)[:200] if result else None,
    }


# ============================================================================
# Pattern 3: Graph - 顺序管道
# ============================================================================
def pattern_3_graph_sequential() -> dict:
    """显式顺序：A → B → C"""
    from strands import Agent
    from strands.multiagent import GraphBuilder

    a = Agent(model=_make_model(), name="a", system_prompt="你是第一步：提取关键词。")
    b = Agent(model=_make_model(), name="b", system_prompt="你是第二步：基于关键词生成句子。")
    c = Agent(model=_make_model(), name="c", system_prompt="你是第三步：翻译成英文。")

    graph = None
    # add_node 返回 GraphNode，不是 builder，所以要分步
    builder = GraphBuilder()
    builder.add_node(a, "step1")
    builder.add_node(b, "step2")
    builder.add_node(c, "step3")
    builder.add_edge("step1", "step2")
    builder.add_edge("step2", "step3")
    builder.set_entry_point("step1")
    graph = builder.build()

    result = graph("中文：今天天气真好")

    return {
        "status": str(result.status),
        "execution_order": [n.node_id for n in result.execution_order],
        "completed_nodes": result.completed_nodes,
        "total_nodes": result.total_nodes,
        "final_answer_sample": str(result)[:200] if result else None,
    }


# ============================================================================
# Pattern 4: Graph - 条件分支
# ============================================================================
def pattern_4_graph_conditional() -> dict:
    """根据 Agent 输出决定下一步走哪个分支。"""
    from strands import Agent
    from strands.multiagent import GraphBuilder

    classifier = Agent(
        model=_make_model(),
        name="classifier",
        system_prompt="你是分类器。如果问题涉及数学，回答 'MATH'；否则回答 'OTHER'。只回答一个词。",
    )
    math_agent = Agent(
        model=_make_model(),
        name="math",
        system_prompt="你是数学专家。",
    )
    general_agent = Agent(
        model=_make_model(),
        name="general",
        system_prompt="你是通用助手。",
    )

    def is_math(state):
        # 简单判断：从 classifier 结果里看有没有 MATH
        last_result = list(state.results.values())[-1] if state.results else None
        if last_result:
            return "MATH" in str(last_result).upper()
        return False

    builder = GraphBuilder()
    builder.add_node(classifier, "classify")
    builder.add_node(math_agent, "math")
    builder.add_node(general_agent, "general")
    builder.add_edge("classify", "math", condition=is_math)
    builder.add_edge("classify", "general", condition=lambda s: not is_math(s))
    builder.set_entry_point("classify")
    graph = builder.build()

    result = graph("What is 5 * 5?")

    return {
        "status": str(result.status),
        "execution_order": [n.node_id for n in result.execution_order],
        "completed_nodes": result.completed_nodes,
    }


# ============================================================================
# Pattern 5: Agent.as_tool - 子 Agent 当工具
# ============================================================================
def pattern_5_agent_as_tool() -> dict:
    """把 Agent 包装成 Tool：最简洁的多 Agent 模式。"""
    from strands import Agent
    from strands_tools import calculator

    # 子 Agent：专门做翻译
    translator = Agent(
        model=_make_model(),
        system_prompt="你是翻译器。把用户输入翻译成英文。",
        name="translator",
    )

    # 主 Agent：调用 translator 当工具
    main_agent = Agent(
        model=_make_model(),
        tools=[calculator, translator.as_tool()],
        system_prompt="你可以用翻译工具把中文翻成英文。",
    )

    result = main_agent("翻译'你好世界'")

    return {
        "stop_reason": result.stop_reason,
        "tools_used": [name for name in result.metrics.tool_metrics],
        "final_answer_sample": str(result)[:300],
    }


# ============================================================================
# Pattern 6: SwarmResult 字段探索
# ============================================================================
def pattern_6_result_inspection() -> dict:
    """探索 SwarmResult / GraphResult 的字段。"""
    import dataclasses

    from strands import Agent
    from strands.multiagent import GraphBuilder, Swarm

    a = Agent(model=_make_model(), name="a", system_prompt="只回答一个字：OK")
    b = Agent(model=_make_model(), name="b", system_prompt="只回答一个字：DONE")

    # Swarm
    swarm = Swarm([a, b], entry_point=a, max_handoffs=2)
    swarm_result = swarm("ping")

    # Graph
    builder = GraphBuilder()
    builder.add_node(a, "x")
    builder.add_node(b, "y")
    builder.add_edge("x", "y")
    builder.set_entry_point("x")
    graph = builder.build()
    graph_result = graph("ping")

    # 字段对比
    swarm_fields = [f.name for f in dataclasses.fields(swarm_result)]
    graph_fields = [f.name for f in dataclasses.fields(graph_result)]

    return {
        "swarm_result_class": type(swarm_result).__name__,
        "swarm_fields": swarm_fields,
        "graph_result_class": type(graph_result).__name__,
        "graph_fields": graph_fields,
        "swarm_status": str(swarm_result.status),
        "graph_status": str(graph_result.status),
    }


PATTERNS = {
    1: ("Swarm 基础", pattern_1_swarm_basic),
    2: ("Swarm 共享上下文", pattern_2_swarm_shared),
    3: ("Graph 顺序", pattern_3_graph_sequential),
    4: ("Graph 条件分支", pattern_4_graph_conditional),
    5: ("Agent.as_tool", pattern_5_agent_as_tool),
    6: ("结果对象探索", pattern_6_result_inspection),
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
    parser = argparse.ArgumentParser(description="Multi-Agent 6 种实战")
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
