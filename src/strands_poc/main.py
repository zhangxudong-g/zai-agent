"""Strands Agent — CLI entry point.

Usage:
    uv run agent "你的问题"                    # 单次运行
    uv run agent                               # 交互式 REPL
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
    print("╭─ Strands Agent ──────────────────────────────")
    print(f"│ Model:     {config.ollama_model}")
    print(f"│ Workspace: {config.agent_workspace}")
    print(f"│ Session:   {session_id}")
    if interactive:
        print("│ Mode:      REPL (连续对话)")
    print("╰────────────────────────────────────────────")
    print()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Strands Agent CLI")
    p.add_argument("prompt", type=str, nargs="?", default=None,
                   help="Prompt (optional; reads from stdin if omitted).")
    p.add_argument("-i", "--interactive", action="store_true",
                   help="Start interactive REPL mode.")
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
        print(f"\n🔧 {tc['tool_name']}")
        if tc["result"]:
            result_lines = tc["result"].split("\n")
            for line in result_lines[:10]:
                if len(line) > 120:
                    line = line[:120] + "..."
                print(f"   {line}")
            if len(result_lines) > 10:
                print(f"   ... ({len(result_lines)} lines)")


class REPL:
    """Interactive REPL for continuous agent conversations."""

    COMMANDS = {
        "/exit": "退出",
        "/quit": "退出",
        "/q": "退出",
        "/help": "帮助",
        "/clear": "清屏",
    }

    def __init__(self, agent: Agent, logger: SessionLogger, stream: bool = True, max_retries: int = 3):
        self.agent = agent
        self.logger = logger
        self.stream = stream
        self.max_retries = max_retries
        self.message_count = 0
        self._loop = None

    def print_welcome(self) -> None:
        print()
        print("╭─ Strands Agent REPL ─────────────────────────")
        print("│ /help   显示帮助")
        print("│ /clear  清屏")
        print("│ /exit   退出")
        print("╰─────────────────────────────────────────────")
        print()

    def print_help(self) -> None:
        print()
        print("╭─ 帮助 ──────────────────────────────────────")
        print("│ 输入问题，Agent 会记住上下文")
        print("│ /clear - 清屏")
        print("│ /exit  - 退出")
        print("╰─────────────────────────────────────────────")
        print()

    def clear_screen(self) -> None:
        print("\033[2J\033[H", end="")
        sys.stdout.flush()

    def print_prompt(self) -> None:
        self.message_count += 1
        print(f"\n[{self.message_count}] > ", end="", flush=True)

    async def _run_streaming_async(self, prompt: str) -> None:
        """Run agent with streaming output."""
        t0 = time.time()
        tool_buffer = []
        thinking_buffer = ""
        has_text = False

        async for chunk in self.agent.run_streaming(prompt, max_retries=self.max_retries):
            if chunk.kind == "text":
                # Show thinking before text if any
                if thinking_buffer:
                    print(f"🤔 {thinking_buffer[:80]}...", flush=True)
                    thinking_buffer = ""
                print(chunk.text, end="", flush=True)
                has_text = True
            elif chunk.kind == "thinking":
                thinking_buffer = chunk.thinking
            elif chunk.kind == "tool_start":
                # Flush text before tool
                if has_text:
                    print()
                    has_text = False
                # Show thinking during tool execution
                if thinking_buffer:
                    print(f"  🤔 {thinking_buffer[:60]}...", flush=True)
                    thinking_buffer = ""
                # Format and print tool call immediately
                if chunk.input_args:
                    args_list = []
                    for k, v in chunk.input_args.items():
                        v_str = str(v)
                        if len(v_str) > 60:
                            v_str = v_str[:60] + "..."
                        args_list.append(f"{k}={v_str!r}")
                    args_str = "(" + ", ".join(args_list) + ")"
                    print(f"  🔧 {chunk.tool_name}{args_str}", flush=True)
                else:
                    print(f"  🔧 {chunk.tool_name}", flush=True)
            elif chunk.kind == "tool_end":
                pass  # Tool result handled by JsonlTraceHook
            elif chunk.kind == "done":
                # Print any remaining thinking
                if thinking_buffer:
                    print(f"\n🤔 {thinking_buffer[:80]}...")
                elapsed = time.time() - t0
                if chunk.usage:
                    tokens = chunk.usage.get("output", 0)
                    print(f"\n✓ ({elapsed:.1f}s, {tokens} tokens)", flush=True)
                else:
                    print(f"\n✓ ({elapsed:.1f}s)", flush=True)

    def run_streaming(self, prompt: str) -> None:
        """Run agent with streaming output (reuses event loop)."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._run_streaming_async(prompt))

    def run_sync(self, prompt: str) -> None:
        """Run agent synchronously."""
        t0 = time.time()
        result = self.agent.run(prompt)
        print()
        print(result)
        print(f"\n✓ ({time.time() - t0:.1f}s)")

    def run(self) -> None:
        """Start the REPL loop."""
        self.print_welcome()
        self.print_prompt()

        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break

                prompt = line.strip()

                # Handle commands
                if prompt.lower() in self.COMMANDS:
                    cmd = prompt.lower()
                    if cmd in ("/exit", "/quit", "/q"):
                        break
                    elif cmd == "/help":
                        self.print_help()
                        self.print_prompt()
                        continue
                    elif cmd == "/clear":
                        self.clear_screen()
                        self.print_prompt()
                        continue

                if not prompt:
                    self.print_prompt()
                    continue

                try:
                    if self.stream:
                        self.run_streaming(prompt)
                    else:
                        self.run_sync(prompt)
                except KeyboardInterrupt:
                    print("\n[中断]")
                except Exception as e:
                    print(f"\n❌ {e}")

                self.print_prompt()

            except KeyboardInterrupt:
                break

        # Cleanup
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()


def run_cli(argv: list[str] | None = None) -> int:
    """CLI entry point for global installation.

    Automatically uses current directory as workspace when --workspace is not specified.
    This function wraps main() with workspace defaults to current directory.
    """
    args = build_parser().parse_args(argv)

    # Use current directory as default workspace if not specified
    if args.workspace is None:
        args.workspace = Path.cwd()

    # Delegate to main() for all the existing logic
    return main(argv)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config = get_config(env_file=args.env_file)
    if args.workspace is not None:
        config.agent_workspace = args.workspace.resolve()

    session_id = generate_session_id()
    print_banner(config, session_id, interactive=args.interactive or (args.prompt is None))

    if not config.agent_workspace.exists():
        print(f"[WARN] workspace does not exist: {config.agent_workspace}", file=sys.stderr)

    logger = SessionLogger(session_id=session_id, log_dir=config.session_log_dir)
    agent = Agent(config=config, logger=logger)

    # REPL mode when no prompt given
    is_interactive = args.interactive or (args.prompt is None)
    use_stream = not args.sync

    if is_interactive:
        repl = REPL(agent=agent, logger=logger, stream=use_stream, max_retries=args.max_retries)
        repl.run()
    elif args.prompt is not None:
        prompt = args.prompt.strip()
        if not prompt:
            print("[ERROR] empty prompt", file=sys.stderr)
            return 2

        if use_stream:
            async def run_stream():
                t0 = time.time()
                thinking_buffer = ""
                has_text = False
                async for chunk in agent.run_streaming(prompt, max_retries=args.max_retries):
                    if chunk.kind == "text":
                        if thinking_buffer:
                            print(f"🤔 {thinking_buffer[:80]}...")
                            thinking_buffer = ""
                        print(chunk.text, end="", flush=True)
                        has_text = True
                    elif chunk.kind == "thinking":
                        thinking_buffer = chunk.thinking
                    elif chunk.kind == "tool_start":
                        if has_text:
                            print()
                            has_text = False
                        if thinking_buffer:
                            print(f"  🤔 {thinking_buffer[:60]}...", flush=True)
                            thinking_buffer = ""
                        if chunk.input_args:
                            args_list = []
                            for k, v in chunk.input_args.items():
                                v_str = str(v)
                                if len(v_str) > 60:
                                    v_str = v_str[:60] + "..."
                                args_list.append(f"{k}={v_str!r}")
                            args_str = "(" + ", ".join(args_list) + ")"
                            print(f"  🔧 {chunk.tool_name}{args_str}", flush=True)
                        else:
                            print(f"  🔧 {chunk.tool_name}", flush=True)
                    elif chunk.kind == "done":
                        if thinking_buffer:
                            print(f"\n🤔 {thinking_buffer[:80]}...")
                        print(f"\n✓ ({time.time() - t0:.1f}s)", flush=True)
                print()
            asyncio.run(run_stream())
        else:
            t0 = time.time()
            result = agent.run(prompt)
            print(result)
            print(f"\n✓ ({time.time() - t0:.1f}s)")
            print()
            display_tool_results_from_log(logger.log_file)
    else:
        print("Reading from stdin...", file=sys.stderr)
        prompt = sys.stdin.read().strip()
        if not prompt:
            print("[ERROR] empty prompt", file=sys.stderr)
            return 2

        t0 = time.time()
        result = agent.run(prompt)
        print(result)
        print(f"\n✓ ({time.time() - t0:.1f}s)")

    print(f"\n📝 {logger.log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
