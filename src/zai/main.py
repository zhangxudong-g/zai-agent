"""Zai Agent — CLI entry point.

Usage:
    uv run zai "你的问题"                    # 单次运行
    uv run zai                               # 交互式 REPL
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from .agent import Agent
from .config import get_config
from .llm import _ollama_debug_path, _setup_ollama_debug
from .paths import get_zai_home, get_zai_config_dir, get_zai_env_file, print_zai_info
from .trace import SessionLogger


def generate_session_id() -> str:
    """Generate a ``YYYYMMDD_HHMMSS_XXXX`` session id."""
    now = datetime.now(UTC)
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{now.strftime('%f')[-4:]}"


def print_banner(
    config,
    session_id: str,
    *,
    log_file: Path | None = None,
    interactive: bool = False,
) -> None:
    """Print compact 2-line startup banner.

    Layout::

        ╭─ zai · <model> · <workspace> · session <id>
        ╰─ 📁 ~/.zai  📝 <log-file-path>  ──  /help /clear /exit
    """
    title = f"zai · {config.ollama_model} · {config.agent_workspace} · session {session_id}"
    print(f"╭─ {title}")
    line2_parts: list[str] = []
    
    # Show zai home
    zai_home = get_zai_home()
    line2_parts.append(f"📁 {zai_home}")
    
    if log_file is not None:
        line2_parts.append(f"📝 {log_file}")
    if _ollama_debug_path is not None:
        line2_parts.append(f"🐛 {_ollama_debug_path}")
    if interactive:
        line2_parts.append("/help /clear /exit")
    if line2_parts:
        print(f"╰─ {'  ──  '.join(line2_parts)}")
    print()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Zai Agent CLI")
    p.add_argument("prompt", type=str, nargs="?", default=None,
                   help="Prompt (optional; reads from stdin if omitted).")
    p.add_argument("-i", "--interactive", action="store_true",
                   help="Start interactive REPL mode.")
    p.add_argument("--workspace", type=Path, default=None,
                   help="Workspace directory (defaults to $AGENT_WORKSPACE in .env).")
    p.add_argument("--model", type=str, default=None,
                   help="Ollama model name (default: qwen3.8:27b).")
    p.add_argument("--sync", action="store_true",
                   help="Disable streaming output (default: streaming enabled).")
    p.add_argument("--max-retries", type=int, default=3,
                   help="Max retry attempts on connection errors (default: 3).")
    p.add_argument("--env-file", type=Path, default=".env",
                   help="Path to .env file (default: .env).")
    p.add_argument("--info", action="store_true",
                   help="Show Zai home directory information and exit.")
    p.add_argument("-uninstall", "--uninstall", action="store_true",
                   help="Uninstall zai-agent and remove user data.")

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

    COMMANDS: ClassVar[dict[str, str]] = {
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
        print("╭─ Zai Agent REPL ─────────────────────────")
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

    def run_streaming(self, prompt: str) -> None:
        """Run agent with streaming output (reuses event loop)."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(
            _render_stream_chunks(
                self.agent.run_streaming(prompt, max_retries=self.max_retries),
            )
        )

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


def _harden_stdio() -> None:
    """Make console/pipe output crash-proof on any codepage.

    Keeps the platform encoding (GBK on Chinese Windows, etc.) so text
    still displays correctly, but turns undisplayable characters (e.g.
    U+FFFD from a lossy-decoded tool result) into a replacement char
    instead of raising UnicodeEncodeError and killing the REPL.
    """
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(errors="replace")



async def _render_stream_chunks(chunk_stream, *, show_usage: bool = True) -> None:
    """Render a stream of StreamChunks to stdout (text, thinking, tool calls).

    Shared by the REPL's streaming path and the one-shot ``--stream``
    path in ``_main`` so chunk-handling logic lives in exactly one place.

    Args:
        chunk_stream: Async iterator of StreamChunk from
            ``agent.run_streaming(...)``.
        show_usage: If True, include output-token count in the final
            final line. One-shot mode sets this to False.
    """
    t0 = time.time()
    thinking_buffer = ""
    has_text = False

    async for chunk in chunk_stream:
        if chunk.kind == "text":
            if thinking_buffer:
                print(f"\U0001f914 {thinking_buffer[:80]}...", flush=True)
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
                print(f"  \U0001f914 {thinking_buffer[:60]}...", flush=True)
                thinking_buffer = ""
            print(f"  \U0001f527 {_format_tool_call(chunk.tool_name, chunk.input_args)}", flush=True)
        elif chunk.kind == "tool_end":
            pass  # Tool result is rendered by JsonlTraceHook
        elif chunk.kind == "done":
            if thinking_buffer:
                print(f"\n\U0001f914 {thinking_buffer[:80]}...")
            elapsed = time.time() - t0
            print(f"\n\u2713 {elapsed:.1f}s", flush=True)



def _format_tool_call(tool_name: str, input_args: dict | None) -> str:
    """Format a tool call for compact single-line display (Option A).

    Display rules (see docs/tui_mockup.md):
      - ``shell``:  skip tool name, show only the command (it's self-evident)
      - single-arg tools (``read``/``write``/``edit``/``outline`` etc.):
        ``<tool> <arg-value>``
      - multi-arg tools (``grep`` etc.): ``<tool> key=val key=val`` (each val truncated)
      - Long values truncated with ``\u2026`` (ellipsis) instead of ``...``.
    """
    if not input_args:
        return tool_name
    if tool_name == "shell":
        cmd = str(input_args.get("command", ""))
        if len(cmd) > 100:
            cmd = cmd[:98] + "\u2026"
        return cmd
    if len(input_args) == 1:
        v_str = str(next(iter(input_args.values())))
        if len(v_str) > 100:
            v_str = v_str[:98] + "\u2026"
        return f"{tool_name} {v_str}"
    parts: list[str] = []
    for k, v in input_args.items():
        v_str = str(v)
        if len(v_str) > 40:
            v_str = v_str[:38] + "\u2026"
        parts.append(f"{k}={v_str!r}")
    return f"{tool_name} " + " ".join(parts)


def run_cli(argv: list[str] | None = None) -> int:
    """CLI entry point for global installation.

    Automatically uses current directory as workspace when --workspace is not specified.
    """
    args = build_parser().parse_args(argv)

    # Handle -uninstall flag
    if args.uninstall:
        import shutil
        import subprocess
        import tempfile
        
        print("Uninstalling zai-agent...")
        print()
        
        # Get zai home directory
        from .paths import get_zai_home
        zai_home = str(get_zai_home())
        
        # Write uninstall script to temp file
        uninstall_script = '''
import subprocess, sys, shutil, os, time
zai_home = sys.argv[1]

print("Delete user data at " + zai_home + "? [y/N]: ", end="", flush=True)
try:
    response = input().strip().lower()
    if response not in ("y", "yes"):
        print("Cancelled")
        sys.exit(0)
except EOFError:
    pass

time.sleep(0.5)

print("Uninstalling pip package...")
subprocess.run([sys.executable, "-m", "pip", "uninstall", "zai-agent", "-y"], capture_output=True)

if os.path.exists(zai_home):
    print("Deleting " + zai_home + "...")
    shutil.rmtree(zai_home)

print()
print("Uninstall complete!")
'''
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(uninstall_script)
            script_path = f.name
        
        # Start uninstall in background and exit
        subprocess.Popen([sys.executable, script_path, zai_home])
        return 0

    # Use current directory as default workspace if not specified
    if args.workspace is None:
        args.workspace = Path.cwd()

    return _main(args)


def _main(args: argparse.Namespace) -> int:
    """Core implementation shared by main() and run_cli()."""
    _harden_stdio()
    
    # Handle --info flag
    if args.info:
        print_zai_info()
        print()
        print("Config file:", get_zai_config_dir() / ".env")
        print()
        print("To configure, edit:", get_zai_env_file())
        return 0
    
    # Initialize default config if not exists
    init_zai_config()
    
    config = get_config(env_file=args.env_file)
    
    # Setup ollama debug if enabled
    _setup_ollama_debug(log_dir=config.session_log_dir)
    if args.workspace is not None:
        config.agent_workspace = args.workspace.resolve()
    if args.model is not None:
        config.ollama_model = args.model

    session_id = generate_session_id()
    logger = SessionLogger(session_id=session_id, log_dir=config.session_log_dir)
    print_banner(
        config,
        session_id,
        log_file=logger.log_file,
        interactive=args.interactive or (args.prompt is None),
    )

    if not config.agent_workspace.exists():
        print(f"[WARN] workspace does not exist: {config.agent_workspace}", file=sys.stderr)
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
            asyncio.run(
                _render_stream_chunks(
                    agent.run_streaming(prompt, max_retries=args.max_retries),
                    show_usage=False,
                )
            )
            print()
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

    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
