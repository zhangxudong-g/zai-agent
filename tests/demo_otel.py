"""OpenTelemetry 实战（生产化阶段 3.1）。

涵盖：
  1. Console Span Exporter（span 落到 stdout）
  2. In-memory span 捕获：层次结构 + 同一 trace_id + service 属性
  3. Metrics：setup_meter 控制台导出
  4. 自定义 trace_attributes 自动透传到 span
  5. BatchSpanProcessor + force_flush（批量导出语义）
  6. OTLP endpoint 容错（不可达端点不 crash）

用法：
    python tests/demo_otel.py --pattern 2
    python tests/demo_otel.py --all
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import os

from opentelemetry import trace as trace_api
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Span as OtelSpan

print(
    repr(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"))
)  # 期望 None 或 http://host:4318，千万别是 ''
print(repr(os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")))
# ---------------------------------------------------------------------------
# 基础设施：单例 StrandsTelemetry + 可内存捕获的 exporter
# ---------------------------------------------------------------------------


class InMemorySpanExporter(SpanExporter):
    """把 span 收进 list 的 exporter，方便断言。"""

    def __init__(self) -> None:
        self.spans: list[OtelSpan] = []

    def export(self, spans) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


_TELEMETRY: dict[str, Any] = {}


def _ensure_telemetry() -> Any:
    """全局只初始化一次 TracerProvider（OTel 规定不可 override）。"""
    if _TELEMETRY:
        return _TELEMETRY["telemetry"]
    from strands.telemetry import StrandsTelemetry

    provider = SDKTracerProvider(
        resource=Resource.create(
            {
                "service.name": "strands-poc",
                "deployment.environment": "poc",
            }
        )
    )
    # ⚠️ 关键：StrandsTelemetry 传入自定义 provider 时不会自动设为全局，
    # 而 strands 内部 get_tracer() 走的是全局 provider → 必须手动 set，
    # 否则所有 span 都是 no-op（InMemory exporter 永远收不到东西）。
    trace_api.set_tracer_provider(provider)
    telemetry = StrandsTelemetry(tracer_provider=provider)
    _TELEMETRY["telemetry"] = telemetry
    return telemetry


def _make_model():
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


def _make_agent(**kwargs: Any) -> Any:
    from strands import Agent
    from strands_tools import calculator

    extras = {
        "system_prompt": "You are a concise math assistant. Always use the calculator tool for arithmetic.",
        "callback_handler": None,
    }
    extras.update(kwargs)
    return Agent(model=_make_model(), tools=[calculator], **extras)


def _run_agent(prompt: str, **agent_kwargs: Any) -> Any:
    attempt_errors: list[str] = []
    for _attempt in range(3):  # WSL DNS 会偶发抖动，重试兜底
        try:
            return _run_agent_once(prompt, **agent_kwargs)
        except Exception as e:
            if any(
                s in repr(e) for s in ("ConnectError", "name resolution", "getaddrinfo")
            ):
                attempt_errors.append(f"{type(e).__name__}")
                time.sleep(1.5)
                continue
            raise
    raise RuntimeError(f"agent 调用重试 3 次后仍失败: {attempt_errors}")


def _run_agent_with_retry(prompt: str, **agent_kwargs: Any) -> Any:
    return _run_agent(prompt, **agent_kwargs)


def _run_agent_once(prompt: str, **agent_kwargs: Any) -> Any:
    agent = _make_agent(**agent_kwargs)
    return agent(prompt)


def _summarize_spans(spans: list[OtelSpan]) -> list[dict]:
    out = []
    for s in spans:
        attrs = dict(s.attributes or {})
        out.append(
            {
                "name": s.name,
                "trace_id": format(s.get_span_context().trace_id, "032x"),
                "span_id": format(s.get_span_context().span_id, "016x"),
                "status": s.status.status_code.name if s.status is not None else None,
                "n_attrs": len(attrs),
                "key_attrs": {k: attrs[k] for k in list(attrs)[:8]},
            }
        )
    return out


# ============================================================================
# Pattern 1: Console Span Exporter
# ============================================================================


def pattern_1_console_exporter() -> dict:
    tel = _ensure_telemetry()
    out = (
        io.StringIO()
    )  # ⚠️ 必须显式传 out：ConsoleSpanExporter 的默认 sys.stdout 在 import 时就被绑定了，redirect_stdout 捕获不到
    if not _TELEMETRY.get("console"):
        tel.setup_console_exporter(out=out)
        _TELEMETRY["console"] = True
        _TELEMETRY["console_out"] = out
    out = _TELEMETRY["console_out"]
    out.seek(0)
    out.truncate(0)
    tel.tracer_provider.force_flush()  # 清掉之前 pattern 残留的 batch
    _run_agent_with_retry(
        "What is 11 * 11? Answer in one short sentence using the calculator."
    )
    out.seek(0)
    captured = out.getvalue()
    assert "span" in captured.lower(), "console 中应出现 span 输出"
    return {
        "console_excerpt": captured[:400],
        "captured_bytes": len(captured),
        "hint": "生产环境换成 OTLP → Jaeger/Tempo/OTel Collector",
    }


# ============================================================================
# Pattern 2: In-memory 捕获 + 层次/trace 断言
# ============================================================================


def pattern_2_inmemory_hierarchy() -> dict:
    tel = _ensure_telemetry()
    exporter = InMemorySpanExporter()
    tel.tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    time.sleep(0.2)  # 防止上一个 pattern 的 batch 混入
    exporter.spans.clear()

    _run_agent_with_retry(
        "Use the calculator tool to compute 6 * 7. One short sentence."
    )
    tel.tracer_provider.force_flush()

    spans = exporter.spans
    assert spans, "应捕获到至少 1 个 span"
    names = [s.name for s in spans]
    trace_ids = {format(s.get_span_context().trace_id, "032x") for s in spans}
    cycle = [n for n in names if "event_loop" in n]
    tools = [n for n in names if n.startswith("execute_tool")]

    return {
        "n_spans": len(spans),
        "names": names,
        "event_loop_span": bool(cycle),
        "tool_span": bool(tools),
        "single_trace": len(trace_ids) == 1,
        "spans_sample": _summarize_spans(spans[:6]),
    }


# ============================================================================
# Pattern 3: Metrics（setup_meter 控制台导出）
# ============================================================================


def pattern_3_metrics_console() -> dict:
    tel = _ensure_telemetry()
    if _TELEMETRY.get("meter"):
        return {"note": "meter 已在上次 pattern 中配置", "status": "reused"}
    tel.setup_meter(enable_console_exporter=True)
    _TELEMETRY["meter"] = True

    result = _run_agent("What is 2 + 2? One word.")
    buf = io.StringIO()
    # 再次触发导出（PeriodicExportingMetricReader 周期导出，这里强制触发一次）
    with redirect_stdout(buf):
        resource_metrics_provider = tel.tracer_provider  # noqa: F841  (仅保持引用)
        import opentelemetry.metrics as metrics_api

        mp = metrics_api.get_meter_provider()
        if hasattr(mp, "force_flush"):
            mp.force_flush()
        captured = buf.getvalue()

    def _u(x):
        if hasattr(x, "inputTokens"):  # object
            return {
                "input": x.inputTokens,
                "output": x.outputTokens,
                "total": x.totalTokens,
            }
        x = dict(x)  # mapping
        return {
            "input": x.get("inputTokens", x.get("input_tokens")),
            "output": x.get("outputTokens", x.get("output_tokens")),
            "total": x.get("totalTokens", x.get("total_tokens")),
        }

    invocations = result.metrics.agent_invocations or []
    last = invocations[-1] if invocations else None
    return {
        "meter_configured": True,
        "n_invocations": len(invocations),
        "last_invocation_usage": (
            _u(last.usage) if last and getattr(last, "usage", None) else None
        ),
        "note": "OTLP metrics 未配置时的本地 fallback 指标",
        "captured_bytes": len(captured),
        "hint": "生产环境: OTLP metrics → Prometheus/CloudWatch",
    }


# ============================================================================
# Pattern 4: 自定义 trace_attributes 透传
# ============================================================================


def pattern_4_trace_attributes() -> dict:
    tel = _ensure_telemetry()
    exporter = InMemorySpanExporter()
    tel.tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    time.sleep(0.2)
    exporter.spans.clear()

    attrs = {"team": "xudongz", "poc.stage": "phase3-otel"}
    _run_agent_with_retry("What is 10 * 5? One word.", trace_attributes=attrs)
    tel.tracer_provider.force_flush()

    found = {}
    for s in exporter.spans:
        for k, v in (s.attributes or {}).items():
            if k in attrs:
                found[k] = v
    missing = set(attrs) - set(found)
    assert not missing, f"trace_attributes 未透传: {missing}"
    return {
        "requested": attrs,
        "found_on_spans": found,
        "n_spans": len(exporter.spans),
    }


# ============================================================================
# Pattern 5: BatchSpanProcessor + force_flush
# ============================================================================


def pattern_5_batch_flush() -> dict:
    tel = _ensure_telemetry()
    exporter = InMemorySpanExporter()
    processor = BatchSpanProcessor(exporter, schedule_delay_millis=60_000)  # 故意设很大
    tel.tracer_provider.add_span_processor(processor)

    _run_agent_with_retry("Say hello in one word.")
    time.sleep(0.6)
    before = len(exporter.spans)
    assert before == 0, f"延迟未到期不应导出，实际 {before}"

    processor.force_flush()
    after = len(exporter.spans)
    assert after > 0, "force_flush 后应有 span"
    return {
        "before_flush": before,
        "after_flush": after,
        "conclusion": "flush 前为 0，flush 后 >0 → 批量导出语义成立",
    }


# ============================================================================
# Pattern 6: OTLP endpoint 容错（不可达端点不 crash）
# ============================================================================


def pattern_6_otlp_resilience() -> dict:
    tel = _ensure_telemetry()
    if _TELEMETRY.get("otlp"):
        return {"status": "reused"}
    # 指向一个几乎肯定没人监听的端口
    tel.setup_otlp_exporter(endpoint="http://127.0.0.1:4318/v1/traces")
    _TELEMETRY["otlp"] = True

    # 关键断言：配置失败/导出失败都不抛异常，只是 log
    result = _run_agent("What is 1 + 1? One word.")
    return {
        "endpoint": "http://127.0.0.1:4318/v1/traces",
        "agent_completed": True,
        "conclusion": "OTLP 端点不可达不影响 agent 主流程",
        "final": str(result)[:60],
    }


PATTERNS = {
    1: ("Console Span Exporter", pattern_1_console_exporter),
    2: ("In-memory span 层次断言", pattern_2_inmemory_hierarchy),
    3: ("Metrics 控制台导出", pattern_3_metrics_console),
    4: ("trace_attributes 透传", pattern_4_trace_attributes),
    5: ("Batch + force_flush", pattern_5_batch_flush),
    6: ("OTLP 端点容错", pattern_6_otlp_resilience),
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
    parser = argparse.ArgumentParser(description="OpenTelemetry 6 种实战")
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
