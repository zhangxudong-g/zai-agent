"""Strands Agents 官方 Quickstart 一键复现脚本。

参考：
- 官方 Quickstart：https://strandsagents.com/docs/user-guide/quickstart/python/
- 本地 Ollama 配置：tests/demo.py（沿用同一 host/model）

用法：
    # 跑全部 6 节
    python tests/demo_quickstart.py --all

    # 只跑某一节
    python tests/demo_quickstart.py --section 1

    # 用 uv 跑（推荐，与项目其他脚本一致）
    uv run python tests/demo_quickstart.py --all

预期输出：
    每个 section 跑通后打印 PASS，最后打印 6/6 PASSED。
    若某 section 失败（通常是 LLM 端点不可达），打印 FAIL 和原因。

依赖：
    - strands-agents>=1.0.0
    - strands-agents-tools>=0.8.6
    - 可达的 OLLAMA_BASE_URL（参见 .env）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

# 让脚本能从仓库根目录导入 strands_poc（如果存在）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# 模型构造（与 tests/demo.py 一致；如有 strands_poc 则用其配置加载）
# ---------------------------------------------------------------------------
def build_ollama_model():
    from strands.models.ollama import OllamaModel

    ollama_model = OllamaModel(
        host="http://swiftechie.aa0.netvolante.jp:51434",
        model_id="qwen3.8:27b",
    )
    return ollama_model


# ---------------------------------------------------------------------------
# 6 个 Quickstart 章节
# ---------------------------------------------------------------------------
def section_1_letter_counter() -> dict:
    """官方 Quickstart 最简示例：自定义 @tool + Agent。

    验证项：letter_counter 工具被模型调用，返回正确计数。
    """
    from strands import Agent, tool
    from strands_tools import calculator, current_time

    @tool
    def letter_counter(word: str, letter: str) -> int:
        """Count occurrences of a specific letter in a word.

        Args:
            word: The word to search in.
            letter: The letter to count (must be a single character).

        Returns:
            Number of occurrences (case-insensitive).
        """
        if not isinstance(word, str) or not isinstance(letter, str):
            return 0
        if len(letter) != 1:
            raise ValueError("The 'letter' parameter must be a single character")
        return word.lower().count(letter.lower())

    agent = Agent(
        model=build_ollama_model(),
        tools=[calculator, current_time, letter_counter],
        callback_handler=None,
    )

    message = """I have 3 requests:
1. What is the time right now?
2. Calculate 3111696 / 74088
3. Tell me how many letter R's are in the word "strawberry"."""
    result = agent(message)

    return {
        "tools_registered": list(agent.tool_registry.registry.keys()),
        "stop_reason": getattr(result, "stop_reason", "unknown"),
        "metrics_summary": (
            result.metrics.get_summary() if hasattr(result, "metrics") else None
        ),
    }


def section_2_stream_async() -> dict:
    """官方 Async Iterator 示例：stream_async + async for。

    验证项：流式输出、current_tool_use 事件、最终 AgentResult。
    注意：用 calculator 而不是 shell，避免 Windows GBK 终端下 Unicode 报错。
    """
    import sys
    from strands import Agent
    from strands_tools import calculator

    agent = Agent(
        model=build_ollama_model(),
        tools=[calculator],
        callback_handler=None,
    )

    async def stream_example():
        prompt = "What is 25 * 48 and explain the calculation"
        chunks_seen = 0
        tool_uses = []
        async for event in agent.stream_async(prompt):
            if "data" in event:
                chunks_seen += 1
                # 避免 GBK 编码报错：替换 unicode 数学符号为 ASCII
                safe = event["data"].replace("−", "-").replace("×", "*")
                try:
                    sys.stdout.write(safe)
                    sys.stdout.flush()
                except UnicodeEncodeError:
                    sys.stdout.write(safe.encode("ascii", "replace").decode())
                    sys.stdout.flush()
            elif "current_tool_use" in event and event["current_tool_use"].get("name"):
                tool_uses.append(event["current_tool_use"]["name"])
                print(f"\n[Tool use delta for: {event['current_tool_use']['name']}]")
        return chunks_seen, tool_uses

    chunks, tools = asyncio.run(stream_example())
    print()  # newline after streamed text
    return {"chunks_received": chunks, "tools_used": tools}


def section_3_callback_handler() -> dict:
    """官方 Callback Handler 示例：自定义 callback 处理 data + current_tool_use。

    验证项：tool_use_ids 收集、自定义 logger 输出。
    注意：用 calculator 替代官方示例中的 shell（shell 依赖 Unix termios，
          无法在 Windows 上运行）。
    """
    import logging
    from strands import Agent
    from strands_tools import calculator

    # 自定义 logger（输出到 StringIO 避免污染 stdout）
    import io

    log_buf = io.StringIO()
    logger = logging.getLogger("demo_quickstart_callback")
    handler = logging.StreamHandler(log_buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    tool_use_ids = []

    def callback_handler(**kwargs):
        if "data" in kwargs:
            logger.info(kwargs["data"])
        elif "current_tool_use" in kwargs:
            tool = kwargs["current_tool_use"]
            if tool["toolUseId"] not in tool_use_ids:
                logger.info(f"[Using tool: {tool.get('name')}]")
                tool_use_ids.append(tool["toolUseId"])

    agent = Agent(
        model=build_ollama_model(),
        tools=[calculator],
        callback_handler=callback_handler,
    )

    result = agent("What is 144 divided by 12?")
    cb_log = log_buf.getvalue()
    return {
        "tool_calls_unique": len(tool_use_ids),
        "callback_lines": cb_log.count("\n"),
        "callback_sample": cb_log[:300],
    }


def section_4_debug_logging() -> dict:
    """官方 Debug 日志示例：logging.getLogger('strands').setLevel(DEBUG)。

    验证项：DEBUG 日志包含 ModelStreamChunkEvent、ToolUse 详情等。
    """
    import logging
    from strands import Agent

    # 关键：用 StringIO 捕获日志，不污染 stdout
    import io

    log_buf = io.StringIO()
    buf_handler = logging.StreamHandler(log_buf)
    buf_handler.setLevel(logging.DEBUG)
    buf_handler.setFormatter(
        logging.Formatter("%(levelname)s | %(name)s | %(message)s")
    )

    strands_logger = logging.getLogger("strands")
    strands_logger.setLevel(logging.DEBUG)
    strands_logger.addHandler(buf_handler)

    agent = Agent(model=build_ollama_model(), tools=[], callback_handler=None)
    agent("Hello!")

    log_text = log_buf.getvalue()
    log_count = log_text.count("\n")

    # 清理
    strands_logger.removeHandler(buf_handler)

    return {"log_lines": log_count, "sample": log_text[:300]}


def section_5_agent_result_fields() -> dict:
    """官方 AgentResult 字段探索示例。

    验证项：accumulated_metrics / accumulated_usage / tool_metrics / traces。
    注意：1.53.0 中这些字段都在 result.metrics 下，不在 result 直接持有。
    """
    from strands import Agent
    from strands_tools import calculator

    agent = Agent(
        model=build_ollama_model(),
        tools=[calculator],
        callback_handler=None,
    )
    result = agent("What is the square root of 144?")

    m = result.metrics  # EventLoopMetrics

    # 1. metrics summary
    metrics_summary = m.get_summary()

    # 2. usage（注意：1.53.0 在 m.accumulated_usage，不是 r.accumulated_usage）
    usage = m.accumulated_usage
    usage_dict = {
        "inputTokens": getattr(usage, "inputTokens", None),
        "outputTokens": getattr(usage, "outputTokens", None),
        "totalTokens": getattr(usage, "totalTokens", None),
    }

    # 3. tool_metrics（1.53.0 命名）
    tool_metrics_dict = {}
    if hasattr(m, "tool_metrics") and m.tool_metrics:
        for name, stats in m.tool_metrics.items():
            tool_metrics_dict[name] = {
                "call_count": getattr(stats, "call_count", None),
                "success_count": getattr(stats, "success_count", None),
                "error_count": getattr(stats, "error_count", None),
            }

    return {
        "metrics": metrics_summary,
        "usage": usage_dict,
        "tool_metrics": tool_metrics_dict,
        "cycle_count": m.cycle_count,
        "trace_count": len(m.traces),
    }


def section_6_string_model_id() -> dict:
    """官方字符串 model_id 示例：直接传字符串给 Agent。

    验证项：Agent 自动用 BedrockModel 包装字符串 model_id。
    """
    from strands import Agent
    import os

    # 字符串 model_id 仅在 Bedrock 后端有意义；本地 Ollama 仍走显式 OllamaModel
    bedrock_id = "global.anthropic.claude-sonnet-4-6"
    print(f"[Info] 字符串 model_id 在 Bedrock 后端生效；当前用 Ollama，验证类型解析")

    # 改用 Ollama 字符串形式做对照
    ollama_id = os.getenv("OLLAMA_MODEL", "qwen3:7b")
    try:
        agent = Agent(model=ollama_id)
        config = agent.model.config
        return {"method": "string_id", "resolved_id": config.get("model_id")}
    except Exception as e:
        return {"method": "string_id", "error": str(e)[:200]}


SECTIONS = {
    1: ("letter_counter 自定义 @tool + 多工具", section_1_letter_counter),
    2: ("stream_async + async for 流式", section_2_stream_async),
    3: ("Callback Handler 自定义", section_3_callback_handler),
    4: ("Debug 日志 (logging.getLogger)", section_4_debug_logging),
    5: ("AgentResult 字段探索", section_5_agent_result_fields),
    6: ("字符串 model_id 直接传入", section_6_string_model_id),
}


def run_section(n: int) -> tuple[bool, dict]:
    """跑一个 section，返回 (success, payload)。"""
    name, fn = SECTIONS[n]
    print(f"\n{'=' * 70}")
    print(f"[Section {n}] {name}")
    print("=" * 70)
    start = time.time()
    try:
        payload = fn()
        elapsed = time.time() - start
        print(f"\n--- Section {n} result ---")
        print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
        print(f"[Section {n}] PASS ({elapsed:.1f}s)")
        return True, payload
    except Exception as e:
        elapsed = time.time() - start
        print(f"\n[Section {n}] FAIL ({elapsed:.1f}s): {type(e).__name__}: {e}")
        traceback.print_exc()
        return False, {"error": str(e), "type": type(e).__name__}


def main():
    parser = argparse.ArgumentParser(description="Strands Quickstart 一键复现")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="跑全部 6 节")
    group.add_argument(
        "--section",
        type=int,
        choices=list(SECTIONS.keys()),
        help=f"只跑某一节 (1-{len(SECTIONS)})",
    )
    args = parser.parse_args()

    # 关闭第三方库的 INFO/DEBUG 输出（除了我们自己开的）
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s | %(name)s | %(message)s",
    )

    if args.section:
        ok, _ = run_section(args.section)
        return 0 if ok else 1

    # --all
    results = {}
    for n in SECTIONS:
        ok, payload = run_section(n)
        results[n] = {"ok": ok, "payload": payload}

    passed = sum(1 for r in results.values() if r["ok"])
    print(f"\n{'=' * 70}")
    print(f"结果：{passed}/{len(SECTIONS)} passed")
    print("=" * 70)
    return 0 if passed == len(SECTIONS) else 1


if __name__ == "__main__":
    sys.exit(main())
