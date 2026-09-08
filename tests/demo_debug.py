"""Debug 日志与可观测性实战。

涵盖：
  1. 基础 DEBUG 日志（控制台）
  2. INFO 级别过滤
  3. 落盘 + 轮转（RotatingFileHandler）
  4. 按 logger 名过滤（strands.event_loop / strands.tools）
  5. EventLoopMetrics 探索
  6. result.metrics.traces 结构
  7. 禁用第三方噪音（urllib3, boto3 等）

用法：
    python tests/demo_debug.py --pattern 1
    python tests/demo_debug.py --all
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


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


def _reset_root_logger():
    """清掉之前的 handler，方便每个 pattern 独立配置。"""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    for name in ("strands", "strands.event_loop", "strands.event_loop.streaming",
                 "strands.models.ollama", "strands.tools.registry"):
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            lg.removeHandler(h)
        lg.setLevel(logging.WARNING)


# ============================================================================
# Pattern 1: 基础 DEBUG 日志（控制台）
# ============================================================================
def pattern_1_basic_debug() -> dict:
    """最简：开启 strands DEBUG 日志，看所有内部事件。"""
    _reset_root_logger()
    log_buf = io.StringIO()
    handler = logging.StreamHandler(log_buf)
    handler.setFormatter(logging.Formatter("%(levelname)s | %(name)s | %(message)s"))

    strands_logger = logging.getLogger("strands")
    strands_logger.setLevel(logging.DEBUG)
    strands_logger.addHandler(handler)

    from strands import Agent
    from strands_tools import calculator

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent("What is 3 * 4?")

    lines = log_buf.getvalue().strip().split("\n")
    return {
        "total_log_lines": len(lines),
        "loggers_seen": sorted({line.split(" | ")[1] for line in lines if " | " in line}),
        "first_3_lines": lines[:3],
        "last_3_lines": lines[-3:],
    }


# ============================================================================
# Pattern 2: INFO 级别（只看关键事件）
# ============================================================================
def pattern_2_info_level() -> dict:
    """INFO 级别比 DEBUG 少很多噪音，但仍能看到关键节点。"""
    _reset_root_logger()
    log_buf = io.StringIO()
    handler = logging.StreamHandler(log_buf)
    handler.setFormatter(logging.Formatter("%(levelname)s | %(name)s | %(message)s"))

    strands_logger = logging.getLogger("strands")
    strands_logger.setLevel(logging.INFO)
    strands_logger.addHandler(handler)

    from strands import Agent
    from strands_tools import calculator

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent("What is 5 * 5?")

    lines = log_buf.getvalue().strip().split("\n")
    return {
        "total_log_lines": len(lines),
        "loggers_seen": sorted({line.split(" | ")[1] for line in lines if " | " in line}),
        "all_lines": lines[:10],
    }


# ============================================================================
# Pattern 3: 落盘 + 轮转
# ============================================================================
def pattern_3_file_rotation() -> dict:
    """把 DEBUG 日志写到文件，用 RotatingFileHandler 防止磁盘爆。"""
    from logging.handlers import RotatingFileHandler
    _reset_root_logger()

    import tempfile
    log_dir = Path(tempfile.mkdtemp())
    log_path = log_dir / "strands.log"

    handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    ))

    strands_logger = logging.getLogger("strands")
    strands_logger.setLevel(logging.DEBUG)
    strands_logger.addHandler(handler)

    from strands import Agent
    from strands_tools import calculator

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent("What is 6 * 7?")

    handler.flush()
    handler.close()

    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    file_size = log_path.stat().st_size

    return {
        "log_path": str(log_path),
        "log_lines": len(lines),
        "file_size_bytes": file_size,
        "backup_pattern": f"{log_path}.1 ~ {log_path}.3",
        "sample_lines": lines[:3],
    }


# ============================================================================
# Pattern 4: 按 logger 名过滤
# ============================================================================
def pattern_4_named_filter() -> dict:
    """只开特定子 logger（如 event_loop），关掉其他，减少噪音。"""
    _reset_root_logger()
    log_buf = io.StringIO()
    handler = logging.StreamHandler(log_buf)
    handler.setFormatter(logging.Formatter("%(levelname)s | %(name)s | %(message)s"))

    # 只开 strands.event_loop（事件循环内部状态）
    el_logger = logging.getLogger("strands.event_loop")
    el_logger.setLevel(logging.DEBUG)
    el_logger.addHandler(handler)

    # 其他 strands.* 保持 WARNING
    logging.getLogger("strands").setLevel(logging.WARNING)

    from strands import Agent
    from strands_tools import calculator

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent("What is 8 * 8?")

    lines = log_buf.getvalue().strip().split("\n")
    return {
        "only_event_loop_logged": len(lines),
        "sample_lines": lines[:5],
        "explanation": "其他 strands.* 子 logger 保持 WARNING，所以 models/tools 不出现",
    }


# ============================================================================
# Pattern 5: EventLoopMetrics 探索
# ============================================================================
def pattern_5_metrics_explore() -> dict:
    """深入 AgentResult.metrics 的结构。"""
    from strands import Agent
    from strands_tools import calculator

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    result = agent("What is 9 * 9?")

    m = result.metrics

    # 1. 顶层 summary
    summary = m.get_summary()

    # 2. cycle_count
    cycle_count = m.cycle_count

    # 3. cycle_durations
    cycle_durations = list(m.cycle_durations)

    # 4. accumulated_usage
    usage = m.accumulated_usage
    usage_dict = {
        "inputTokens": getattr(usage, "inputTokens", None),
        "outputTokens": getattr(usage, "outputTokens", None),
        "totalTokens": getattr(usage, "totalTokens", None),
    }

    # 5. accumulated_metrics (latency 等)
    am = m.accumulated_metrics
    am_dict = {
        "latencyMs": getattr(am, "latencyMs", None),
    }

    # 6. tool_metrics
    tool_metrics = {}
    for name, stats in m.tool_metrics.items():
        tool_metrics[name] = {
            "call_count": getattr(stats, "call_count", None),
            "success_count": getattr(stats, "success_count", None),
            "error_count": getattr(stats, "error_count", None),
            "total_time": getattr(stats, "total_time", None),
        }

    return {
        "summary": summary,
        "cycle_count": cycle_count,
        "cycle_durations": cycle_durations,
        "accumulated_usage": usage_dict,
        "accumulated_metrics": am_dict,
        "tool_metrics": tool_metrics,
        "agent_invocations_count": len(m.agent_invocations),
    }


# ============================================================================
# Pattern 6: traces 探索
# ============================================================================
def pattern_6_traces_explore() -> dict:
    """深入 result.metrics.traces 的结构（每轮的详细轨迹）。"""
    from strands import Agent
    from strands_tools import calculator

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    result = agent("What is 10 * 10?")

    m = result.metrics
    traces = m.traces

    # 每个 trace 是一个 Trace 对象
    trace_summaries = []
    for i, trace in enumerate(traces):
        summary = {
            "index": i,
            "type": type(trace).__name__,
            "attrs": {},
            "message_count": 0,
            "child_count": 0,
        }
        # 尝试拿关键属性
        for attr in ("name", "id", "parent_id", "start_time", "end_time"):
            if hasattr(trace, attr):
                val = getattr(trace, attr)
                if val is not None:
                    summary["attrs"][attr] = str(val)[:50]
        # 拿 messages
        if hasattr(trace, "messages"):
            summary["message_count"] = len(trace.messages)
        # 拿 children
        if hasattr(trace, "children"):
            summary["child_count"] = len(trace.children)
        trace_summaries.append(summary)

    return {
        "trace_count": len(traces),
        "trace_summaries": trace_summaries,
        "first_trace_attrs": trace_summaries[0]["attrs"] if trace_summaries else None,
    }


# ============================================================================
# Pattern 7: 禁用第三方噪音（urllib3, boto3, httpx）
# ============================================================================
def pattern_7_quiet_third_party() -> dict:
    """strands 依赖 urllib3/boto3/httpx；DEBUG 时它们也会刷屏。"""
    _reset_root_logger()
    log_buf = io.StringIO()
    handler = logging.StreamHandler(log_buf)
    handler.setFormatter(logging.Formatter("%(levelname)s | %(name)s | %(message)s"))

    # 开 strands 全 DEBUG
    logging.getLogger("strands").setLevel(logging.DEBUG)
    logging.getLogger("strands").addHandler(handler)

    # 关闭第三方噪音
    for noisy in ("urllib3", "botocore", "boto3", "httpx", "httpcore",
                  "asyncio", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    from strands import Agent
    from strands_tools import calculator

    agent = Agent(model=_make_model(), tools=[calculator], callback_handler=None)
    agent("What is 11 * 11?")

    lines = [line for line in log_buf.getvalue().strip().split("\n") if line]
    third_party_lines = [line for line in lines if any(n in line for n in ("urllib3", "botocore", "boto3", "httpx", "httpcore"))]
    strands_lines = [line for line in lines if "strands" in line]

    return {
        "total_lines": len(lines),
        "strands_lines": len(strands_lines),
        "third_party_lines": len(third_party_lines),
        "third_party_ratio": f"{len(third_party_lines)}/{len(lines)}",
        "loggers_in_output": sorted({line.split(" | ")[1] for line in lines if " | " in line}),
    }


PATTERNS = {
    1: ("基础 DEBUG 日志", pattern_1_basic_debug),
    2: ("INFO 级别过滤", pattern_2_info_level),
    3: ("落盘 + 轮转", pattern_3_file_rotation),
    4: ("按 logger 名过滤", pattern_4_named_filter),
    5: ("EventLoopMetrics 探索", pattern_5_metrics_explore),
    6: ("traces 探索", pattern_6_traces_explore),
    7: ("禁用第三方噪音", pattern_7_quiet_third_party),
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
    parser = argparse.ArgumentParser(description="Debug 日志 7 种实战")
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
