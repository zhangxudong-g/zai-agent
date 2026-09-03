"""Strands Agent — CLI entry point.

Usage:
    uv run agent "你的问题"                    # 单次运行
    uv run agent --interactive               # 交互式 REPL
    uv run agent -i "你的问题" --stream      # 带初始 prompt 的交互模式
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
    """Generate a ``YYYYMMDD_HHMMSS_XXXX`` session id."""
    now = datetime.now(UTC)
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{now.strftime('%f')[-4:]}"


def print_banner(config, session_id: str, interactive: bool = False) -> None:
    print("=" * 60)
    print("Strands Agent + Ollama")
    print("=" * 60)
    print(f"Model:     {config.ollama_model}")
    print(f"URL:       {config.ollama_base_url}")
    print(f"Workspace: {config.agent_workspace}")
    print(f"Session:   {session_id}")
    print(f"Log:       {config.session_log_dir / (session_id + '.jsonl')}")
    if interactive:
        print("Mode:      Interactive (REPL)")
    print("=" * 60)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Strands Agent CLI")
    p.add_argument("prompt", type=str, nargs="?", default=None,
                   help="Prompt (optional; reads from stdin if omitted).")
    p.add_argument("-i", "--interactive", action="store_true",
                   help="Start interactive REPL mode (default when no prompt).")
    p.add_argument("--workspace", type=Path, default=None,
                   help="Workspace directory (defaults to $AGENT_WORKSPACE in .env).")
    p.add_argument("--sync", action="store_true",
                   help="Disable streaming output (default: streaming enabled).")
    p.add_argument("--max-retries", type=int, default=3,
                   help="Max retry attempts on connection errors (default: 3).")
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
    """Read and display tool results from JSONL log file."""
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

                    for tc in tool_calls:
                        if tc["tool_call_id"] == tool_call_id and tc["result"] is None:
                            tc["result"] = result_content
                            tc["is_error"] = is_error
                            tc["result_time"] = record.get("timestamp", "")
                            break
            except (json.JSONDecodeError, KeyError):
                continue

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


class REPL:
    """Interactive REPL for continuous agent conversations."""

    COMMANDS = {
        "/exit": "退出",
        "/quit": "退出",
        "/q": "退出",
        "/help": "显示帮助",
        "/clear": "清屏",
    }

    def __init__(self, agent: Agent, logger: SessionLogger, stream: bool = False, max_retries: int = 3):
        self.agent = agent
        self.logger = logger
        self.stream = stream
        self.max_retries = max_retries
        self.message_count = 0

    def print_welcome(self) -> None:
        print("\n" + "=" * 60)
        print("Strands Agent REPL - 连续对话模式")
        print("=" * 60)
        print("命令:")
        print("  /exit, /quit, /q  - 退出")
        print("  /help              - 显示帮助")
        print("  /clear             - 清屏")
        print("=" * 60)
        print()

    def print_help(self) -> None:
        print("\n" + "=" * 60)
        print("Strands Agent REPL 帮助")
        print("=" * 60)
        print("- 直接输入问题，与 Agent 连续对话")
        print("- Agent 会记住当前会话的上下文")
        print("- 使用 /exit 或 Ctrl+C 退出")
        print("- 使用 /clear 清屏")
        print("=" * 60)
        print()

    def clear_screen(self) -> None:
        print("\033[2J\033[H", end="")  # ANSI clear screen
        sys.stdout.flush()

    def print_prompt(self) -> None:
        self.message_count += 1
        print(f"\n[{self.message_count}] 你: ", end="", flush=True)

    async def run_streaming(self, prompt: str) -> None:
        """Run agent with streaming output."""
        print()
        t0 = time.time()
        async for chunk in self.agent.run_streaming(prompt, max_retries=self.max_retries):
            if chunk.kind == "text":
                print(chunk.text, end="", flush=True)
            elif chunk.kind == "thinking":
                print(f"\n[thinking] {chunk.thinking}", flush=True)
            elif chunk.kind == "tool_start":
                print(chunk.format_tool_start(), flush=True)
            elif chunk.kind == "tool_end":
                print(chunk.format_tool_end(), flush=True)
            elif chunk.kind == "done":
                print()
                print("=" * 60)
                print(f"✅ 完成 ({time.time() - t0:.1f}s)")
                if chunk.usage:
                    print(f"   Tokens: input={chunk.usage.get('input', 'N/A')}, "
                          f"output={chunk.usage.get('output', 'N/A')}")
        print()

    def run_sync(self, prompt: str) -> None:
        """Run agent synchronously."""
        print()
        t0 = time.time()
        result = self.agent.run(prompt)
        print("=" * 60)
        print(result)
        print("=" * 60)
        print(f"[完成] 耗时: {time.time() - t0:.1f}s")
        print()

    def run(self) -> None:
        """Start the REPL loop."""
        self.print_welcome()
        self.print_prompt()

        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    # EOF (Ctrl+D)
                    break

                prompt = line.strip()

                # Handle commands
                if prompt.lower() in self.COMMANDS:
                    if prompt.lower() in ("/exit", "/quit", "/q"):
                        print("\n再见!")
                        break
                    elif prompt.lower() == "/help":
                        self.print_help()
                        self.print_prompt()
                        continue
                    elif prompt.lower() == "/clear":
                        self.clear_screen()
                        self.print_prompt()
                        continue

                # Skip empty input
                if not prompt:
                    self.print_prompt()
                    continue

                # Run agent
                try:
                    if self.stream:
                        asyncio.run(self.run_streaming(prompt))
                    else:
                        self.run_sync(prompt)
                except KeyboardInterrupt:
                    print("\n[中断]")
                except Exception as e:
                    print(f"\n❌ 错误: {e}")

                self.print_prompt()

            except KeyboardInterrupt:
                print("\n\n再见!")
                break

        print(f"\n[Session log] {self.logger.log_file}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config = get_config(env_file=args.env_file)
    if args.workspace is not None:
        config.agent_workspace = args.workspace.resolve()

    session_id = generate_session_id()
    print_banner(config, session_id, interactive=args.interactive or sys.stdin.isatty())

    if not config.agent_workspace.exists():
        print(f"[WARN] workspace does not exist: {config.agent_workspace}", file=sys.stderr)

    print(f"Using tools: {list(config.allowed_tools)}")
    print()

    logger = SessionLogger(session_id=session_id, log_dir=config.session_log_dir)
    agent = Agent(config=config, logger=logger)

    # Determine if we should run REPL
    is_interactive = args.interactive or (args.prompt is None and sys.stdin.isatty())
    # Streaming is the default; --sync disables it
    use_stream = not args.sync

    if is_interactive:
        # REPL mode
        repl = REPL(agent=agent, logger=logger, stream=use_stream, max_retries=args.max_retries)
        repl.run()
    elif args.prompt is not None:
        # Single prompt mode
        prompt = args.prompt.strip()
        if not prompt:
            print("[ERROR] empty prompt", file=sys.stderr)
            return 2

        if use_stream:
            async def run_stream():
                t0 = time.time()
                async for chunk in agent.run_streaming(prompt, max_retries=args.max_retries):
                    if chunk.kind == "text":
                        print(chunk.text, end="", flush=True)
                    elif chunk.kind == "thinking":
                        print(f"\n[thinking] {chunk.thinking}", flush=True)
                    elif chunk.kind == "tool_start":
                        print(chunk.format_tool_start(), flush=True)
                    elif chunk.kind == "tool_end":
                        print(chunk.format_tool_end(), flush=True)
                    elif chunk.kind == "done":
                        print()
                        print("=" * 60)
                        print("✅ AGENT COMPLETED")
                        print(f"   Duration: {time.time() - t0:.1f}s")
                        print(f"   Result length: {len(chunk.result)} chars")
                        if chunk.usage:
                            print(f"   Tokens: input={chunk.usage.get('input', 'N/A')}, "
                                  f"output={chunk.usage.get('output', 'N/A')}")
                print()
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
    else:
        print("Reading prompt from stdin (Ctrl+D to finish):", file=sys.stderr)
        prompt = sys.stdin.read().strip()
        if not prompt:
            print("[ERROR] empty prompt", file=sys.stderr)
            return 2

        t0 = time.time()
        result = agent.run(prompt)
        print("=" * 60)
        print(result)
        print("=" * 60)
        print(f"[Done] elapsed={time.time() - t0:.1f}s")

    print(f"\n[Session log] {logger.log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
