"""Strands Agent — CLI entry point.

Usage:
    uv run agent "你的问题"
    uv run agent "分析并发问题" --stream
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from .agent import Agent
from .config import get_config
from .trace import SessionLogger


def generate_session_id() -> str:
    """Generate a ``YYYYMMDD_HHMMSS_XXXX`` session id (same as claude-agent)."""
    now = datetime.now(UTC)
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{now.strftime('%f')[-4:]}"


def print_banner(config, session_id: str) -> None:
    print("=" * 60)
    print("Strands Agent + Ollama")
    print("=" * 60)
    print(f"Model:     {config.ollama_model}")
    print(f"URL:       {config.ollama_base_url}")
    print(f"Workspace: {config.agent_workspace}")
    print(f"Session:   {session_id}")
    print(f"Log:       {config.session_log_dir / (session_id + '.jsonl')}")
    print("=" * 60)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Strands Agent CLI")
    p.add_argument("prompt", type=str, nargs="?", default=None,
                   help="Prompt (optional; reads from stdin if omitted).")
    p.add_argument("--workspace", type=Path, default=None,
                   help="Workspace directory (defaults to $AGENT_WORKSPACE in .env).")
    p.add_argument("--stream", action="store_true",
                   help="Enable streaming output.")
    p.add_argument("--env-file", type=Path, default=".env",
                   help="Path to .env file (default: .env).")

    return p


def parse_result_content(result_str: str) -> tuple[str, bool]:
    """Parse tool result from JSON string format."""
    try:
        data = json.loads(result_str)
        if isinstance(data, dict):
            content = data.get("text", "")
            is_error = data.get("status") == "error"
            return content, is_error
    except (json.JSONDecodeError, TypeError):
        pass
    return result_str, False


def display_tool_results_from_log(log_file: Path) -> None:
    """Read and display tool results from JSONL log file.

    Only used in the non-streaming CLI branch. The streaming branch
    renders tool results inline via ``JsonlTraceHook`` →
    ``StreamConsumer`` → ``run_streaming`` drain, so re-printing from
    the JSONL would duplicate the entire tool trace.
    """
    if not log_file.exists():
        return

    tool_calls = []

    with log_file.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line.strip())
                event = record.get("event", "")

                if event == "tool_call_start":
                    tool_calls.append({
                        "tool_call_id": record.get("tool_call_id", ""),
                        "tool_name": record.get("tool_name", ""),
                        "arguments": record.get("arguments", {}),
                        "start_time": record.get("timestamp", ""),
                        "result": None,
                        "result_time": None,
                    })
                elif event == "tool_call_end":
                    tool_call_id = record.get("tool_call_id", "")
                    result_str = record.get("result", "")
                    result_content, is_error = parse_result_content(result_str)

                    # Find matching tool call
                    for tc in tool_calls:
                        if tc["tool_call_id"] == tool_call_id and tc["result"] is None:
                            tc["result"] = result_content
                            tc["is_error"] = is_error
                            tc["result_time"] = record.get("timestamp", "")
                            break
            except (json.JSONDecodeError, KeyError):
                continue

    # Display tool results
    for tc in tool_calls:
        print(f"\n{'='*60}")
        print(f"🔧 TOOL: {tc['tool_name']}")
        print(f"{'='*60}")
        print(f"   ID: {tc['tool_call_id']}")
        print(f"   Start: {tc['start_time']}")

        if tc["arguments"]:
            print("   Arguments:")
            for k, v in tc["arguments"].items():
                v_str = str(v)
                if len(v_str) > 200:
                    v_str = v_str[:200] + "..."
                print(f"     {k}: {v_str}")

        if tc["result"] is not None:
            print(f"   Result ({len(tc['result'])} chars):")
            result_lines = tc["result"].split("\n")
            for line in result_lines[:30]:
                if len(line) > 150:
                    line = line[:150] + "..."
                print(f"     {line}")
            if len(result_lines) > 30:
                print(f"     ... ({len(result_lines)} total lines)")

            if tc.get("is_error"):
                print("   ❌ ERROR")

        print(f"   End: {tc['result_time']}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config = get_config(env_file=args.env_file)
    if args.workspace is not None:
        config.agent_workspace = args.workspace.resolve()

    if args.prompt is not None:
        prompt = args.prompt
    else:
        print("Reading prompt from stdin (Ctrl+D to finish):", file=sys.stderr)
        prompt = sys.stdin.read()
    prompt = prompt.strip()
    if not prompt:
        print("[ERROR] empty prompt", file=sys.stderr)
        return 2

    session_id = generate_session_id()
    print_banner(config, session_id)

    # Print tool info
    print(f"Using tools: {list(config.allowed_tools)}")

    if not config.agent_workspace.exists():
        print(f"[WARN] workspace does not exist: {config.agent_workspace}", file=sys.stderr)

    logger = SessionLogger(session_id=session_id, log_dir=config.session_log_dir)

    # Create agent
    agent = Agent(config=config, logger=logger)

    if args.stream:
        async def run_stream():
            t0 = time.time()
            async for chunk in agent.run_streaming(prompt):
                if chunk.kind == "text":
                    print(chunk.text, end="", flush=True)
                elif chunk.kind == "thinking":
                    # Render the model's reasoning inline so the user can
                    # follow its decision-making. Prefixed so it doesn't
                    # blend in with the final answer.
                    print(f"\n[thinking] {chunk.thinking}", flush=True)
                elif chunk.kind == "tool_start":
                    print(chunk.format_tool_start(), flush=True)
                elif chunk.kind == "tool_input":
                    print(f"\n   ↳ Args updated: {chunk.input_args}", flush=True)
                elif chunk.kind == "tool_end":
                    # Tool result arrives live via JsonlTraceHook → consumer
                    # queue → run_streaming drain. No need to re-print from
                    # the JSONL log later — that would duplicate output.
                    print(chunk.format_tool_end(), flush=True)
                elif chunk.kind == "done":
                    print()
                    print("=" * 60)
                    print("✅ AGENT COMPLETED")
                    print(f"   Duration: {time.time() - t0:.1f}s")
                    print(f"   Result length: {len(chunk.result)} chars")
                    print(f"   Stop reason: {chunk.stop_reason}")
                    if chunk.is_error:
                        print("   ❌ Error occurred")
                        # Connection-level failures are the most common
                        # cause of an aborted session (DNS blip, host
                        # offline, firewall). Surface a hint so the
                        # user knows what to check instead of staring
                        # at a raw traceback.
                        if chunk.stop_reason == "force_stop":
                            print("   ↳ The agent was force-stopped. Common causes:")
                            print("     - OLLAMA_BASE_URL in .env is unreachable (host down / DNS / firewall)")
                            print("     - OpenTelemetry OTLP endpoint (Langfuse/Jaeger) is not running")
                            print("     - A community tool made an outbound call that failed")
                            print("   ↳ Check the traceback below and the session JSONL for the exact site.")
                    if chunk.usage:
                        print(f"   Tokens: input={chunk.usage.get('input', 'N/A')}, "
                              f"output={chunk.usage.get('output', 'N/A')}")
            print()
            # NOTE: do NOT call display_tool_results_from_log here — the
            # streaming loop above already rendered every tool call's
            # start/input/end inline. Re-printing from the JSONL would
            # duplicate the entire tool trace.
        asyncio.run(run_stream())
    else:
        t0 = time.time()
        result = agent.run(prompt)
        print("=" * 60)
        print(result)
        print("=" * 60)
        print(f"[Done] elapsed={time.time() - t0:.1f}s")

        # Display tool results from log
        print("\n" + "="*60)
        print("📋 TOOL CALLS SUMMARY")
        print("="*60)
        display_tool_results_from_log(logger.log_file)

    print(f"\n[Session log] {logger.log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
