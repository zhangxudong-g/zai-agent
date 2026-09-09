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

from .agent import Agent
from .config import get_config
from .llm import _ollama_debug_path, _setup_ollama_debug
from .paths import (
    get_zai_config_dir,
    get_zai_env_file,
    get_zai_home,
    init_zai_config,
    print_zai_info,
)
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
    from .tui import print_banner_v2

    zai_home = get_zai_home()
    extra_parts: list[str] = []
    if _ollama_debug_path is not None:
        extra_parts.append(f"🐛 {_ollama_debug_path}")

    print_banner_v2(
        model=config.ollama_model,
        workspace=config.agent_workspace,
        session_id=session_id,
        log_file=log_file,
        zai_home=zai_home,
        interactive=interactive,
        extra_parts=extra_parts,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Zai Agent CLI")
    p.add_argument(
        "prompt",
        type=str,
        nargs="?",
        default=None,
        help="Prompt (optional; reads from stdin if omitted).",
    )
    p.add_argument("-i", "--interactive", action="store_true", help="Start interactive REPL mode.")
    p.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace directory (defaults to $AGENT_WORKSPACE in .env).",
    )
    p.add_argument(
        "--model", type=str, default=None, help="Ollama model name (default: qwen3.8:27b)."
    )
    p.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Maximum tokens for single response (default: model default).",
    )
    p.add_argument(
        "--sync", action="store_true", help="Disable streaming output (default: streaming enabled)."
    )
    p.add_argument(
        "--no-interventions",
        action="store_true",
        help="Disable safety interventions (dangerous commands, sensitive files).",
    )
    p.add_argument(
        "--skills",
        action="store_true",
        help="Enable skill plugin system (loads skills from ~/.zai/plugins/skills/).",
    )
    p.add_argument(
        "--concurrent-tools",
        action="store_true",
        help="Run tool calls concurrently (faster but may affect ordering).",
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retry attempts on connection errors (default: 3).",
    )
    p.add_argument(
        "--env-file", type=Path, default=".env", help="Path to .env file (default: .env)."
    )
    p.add_argument(
        "--info", action="store_true", help="Show Zai home directory information and exit."
    )
    p.add_argument(
        "-uninstall",
        "--uninstall",
        action="store_true",
        help="Uninstall zai-agent and remove user data.",
    )

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
                    tool_calls.append(
                        {
                            "tool_call_id": record.get("tool_call_id", ""),
                            "tool_name": record.get("tool_name", ""),
                            "arguments": record.get("arguments", {}),
                            "start_time": record.get("timestamp", ""),
                            "result": None,
                            "result_time": None,
                        }
                    )
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
        from .tui import print_tool_result

        print()
        print_tool_result(
            name=tc["tool_name"],
            result=tc["result"] or "",
            is_error=tc.get("is_error", False),
        )


class REPL:
    """Backwards-compatible alias for EnhancedREPL."""

    def __new__(cls, *args, **kwargs):
        from .repl import EnhancedREPL

        return EnhancedREPL(*args, **kwargs)


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
    from .tui import print_done, print_thinking, print_tool_start

    t0 = time.time()
    thinking_buffer = ""
    has_text = False

    async for chunk in chunk_stream:
        if chunk.kind == "text":
            if thinking_buffer:
                print_thinking(thinking_buffer)
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
                print_thinking(thinking_buffer, max_chars=60)
                thinking_buffer = ""
            print_tool_start(chunk.tool_name, chunk.input_args)
        elif chunk.kind == "tool_end":
            pass  # Tool result is rendered by JsonlTraceHook
        elif chunk.kind == "done":
            if thinking_buffer:
                print_thinking(thinking_buffer)
            # Fallback: if no streaming text was displayed (e.g. the whole
            # reply arrived in the final result event, or a small model
            # returned it without incremental chunks), print the result text
            # so the user never sees an empty response.
            if not has_text and chunk.result:
                print(chunk.result, end="", flush=True)
                has_text = True
            elapsed = time.time() - t0
            print_done(elapsed)


def run_cli(argv: list[str] | None = None) -> int:
    """CLI entry point for global installation.

    Automatically uses current directory as workspace when --workspace is not specified.
    """
    args = build_parser().parse_args(argv)

    # Handle -uninstall flag
    if args.uninstall:
        import subprocess
        import tempfile

        from .paths import get_zai_home

        zai_home = str(get_zai_home())

        print("Uninstalling zai-agent...")
        print()

        # Ask for confirmation
        try:
            response = input(f"Delete user data {zai_home}? [y/N]: ").strip().lower()
            if response not in ("y", "yes"):
                print("Cancelled")
                return 0
        except EOFError:
            pass

        # Create a temp script to do actual uninstall (zai.exe gets deleted during pip uninstall)
        script = f'''
import subprocess, sys, shutil, os, time
zai_home = r"{zai_home}"
time.sleep(0.3)
subprocess.run([sys.executable, "-m", "pip", "uninstall", "zai-agent", "-y"], capture_output=True)
print("[OK] pip package removed")
if os.path.exists(zai_home):
    print(f"Deleting {{zai_home}}...")
    shutil.rmtree(zai_home)
    print("[OK] user data removed")
else:
    print("[OK] user data already gone")
print()
print("Uninstall complete!")
'''

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(script)
            script_path = f.name

        # Start uninstall script in new console and exit
        if sys.platform == "win32":
            subprocess.Popen(
                ["cmd", "/c", "python", script_path, "&&", "pause"],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        else:
            subprocess.Popen([sys.executable, script_path])
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
    if args.max_tokens is not None:
        config.max_tokens = args.max_tokens

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
    agent = Agent(
        config=config,
        logger=logger,
        enable_interventions=not args.no_interventions,
        enable_skills=args.skills,
        tool_executor_mode="concurrent" if args.concurrent_tools else "sequential",
    )

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
