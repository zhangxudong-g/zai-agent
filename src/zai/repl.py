"""Enhanced REPL for Zai Agent using prompt_toolkit.

Features:
- Persistent history (~/.zai/history)
- Up/Down arrow keys to navigate history
- Ctrl+R reverse history search
- Ctrl+C cancels current operation without exiting
- Ctrl+D exits the REPL
- Ctrl+L clears the screen
- Friendly error messages with hints
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, ClassVar

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout

from .paths import get_zai_home
from .trace import SessionLogger

if TYPE_CHECKING:
    from .agent import Agent


def _create_session() -> PromptSession[str]:
    """Create a PromptSession with persistent history."""
    history_file = get_zai_home() / "history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    return PromptSession(
        history=FileHistory(str(history_file)),
        enable_suspend=True,
    )


def _create_key_bindings() -> KeyBindings:
    """Create custom key bindings."""
    kb = KeyBindings()

    @kb.add("c-l")
    def _clear_screen(event):
        """Ctrl+L: clear screen."""
        event.app.renderer.clear()

    return kb


class EnhancedREPL:
    """Interactive REPL with prompt_toolkit features."""

    COMMANDS: ClassVar[set[str]] = {
        "/exit",
        "/quit",
        "/q",
        "/help",
        "/clear",
        "/info",
    }

    def __init__(self, agent, logger: SessionLogger, stream: bool = True, max_retries: int = 3):

        self.agent: Agent = agent
        self.logger = logger
        self.stream = stream
        self.max_retries = max_retries
        self.message_count = 0
        self.session = _create_session()
        self.kb = _create_key_bindings()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cancel_requested = False

    def print_welcome(self) -> None:
        from .tui import print_box

        print()
        print_box(
            "Zai Agent REPL",
            [
                "/help   显示帮助",
                "/clear  清屏",
                "/info   查看配置",
                "/exit   退出",
                "Ctrl+R  搜索历史",
                "Ctrl+C  取消/退出",
            ],
            color="cyan",
            width=45,
        )
        print()

    def print_help(self) -> None:
        from .tui import print_box

        print()
        print_box(
            "帮助",
            [
                "输入问题，Agent 会记住上下文",
                "",
                "/help   - 显示此帮助",
                "/clear  - 清屏",
                "/info   - 显示配置信息",
                "/exit   - 退出",
                "",
                "快捷键：",
                "  ↑/↓  历史记录",
                "  Ctrl+R  历史搜索",
                "  Ctrl+C  取消当前操作",
                "  Ctrl+L  清屏",
                "  Ctrl+D  退出",
            ],
            color="cyan",
            width=50,
        )
        print()

    def clear_screen(self) -> None:
        from prompt_toolkit.shortcuts import clear

        clear()

    def print_info(self) -> None:
        from .paths import print_zai_info

        print()
        print_zai_info()
        print()

    def print_prompt_indicator(self) -> None:
        self.message_count += 1
        # Just print the indicator without trailing newline;
        # prompt_toolkit will handle the actual prompt
        print()  # blank line before each prompt

    async def run_streaming_async(self, prompt: str) -> None:
        """Async streaming implementation (placeholder)."""
        # The cancellation hook is intentionally not wired through the strands
        # event loop here; users should prefer the sync run_streaming wrapper.
        raise NotImplementedError("Async streaming is not yet supported; use run_streaming().")

    def run_streaming(self, prompt: str) -> None:
        """Run agent with streaming output."""
        from .main import _render_stream_chunks

        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

        self._cancel_requested = False
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
        from .tui import print_done

        print_done(time.time() - t0)

    def run(self) -> None:
        """Start the REPL loop."""
        self.print_welcome()
        self.message_count = 0
        is_first_prompt = True

        while True:
            self.message_count += 1
            try:
                # prompt_toolkit handles ^C by raising KeyboardInterrupt,
                # but we use try/except to distinguish idle vs running.
                # patch_stdout ensures background tasks don't garble the prompt.
                with patch_stdout():
                    # Add a blank line before each prompt (except the first one)
                    if is_first_prompt:
                        is_first_prompt = False
                        prompt_str = f"[{self.message_count}] > "
                    else:
                        prompt_str = f"\n[{self.message_count}] > "

                    prompt_text = self.session.prompt(
                        prompt_str,
                        key_bindings=self.kb,
                    )
            except KeyboardInterrupt:
                # Idle ^C: exit gracefully
                print()
                print("Bye!")
                break
            except EOFError:
                # Ctrl+D: exit
                print()
                print("Bye!")
                break

            prompt_text = prompt_text.strip()

            # Handle commands
            if prompt_text.lower() in self.COMMANDS:
                cmd = prompt_text.lower()
                if cmd in ("/exit", "/quit", "/q"):
                    break
                elif cmd == "/help":
                    self.print_help()
                    continue
                elif cmd == "/clear":
                    self.clear_screen()
                    continue
                elif cmd == "/info":
                    self.print_info()
                    continue
                elif cmd == "/clear":
                    self.clear_screen()
                    continue

            if not prompt_text:
                continue

            # Run the agent
            try:
                if self.stream:
                    self.run_streaming(prompt_text)
                else:
                    self.run_sync(prompt_text)
            except KeyboardInterrupt:
                # ^C during run: cancel current, return to prompt
                self._cancel_requested = True
                from .tui import print_warn

                print_warn("已取消")
            except Exception as e:
                self._handle_error(e)

        # Cleanup
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()

    def _handle_error(self, e: Exception) -> None:
        """Display a friendly error message based on exception type."""
        from .tui import print_error, print_info

        err_str = str(e).lower()

        # Connection errors
        if "connection" in err_str or "refused" in err_str or "timed out" in err_str:
            print_error(f"Ollama 连接失败: {e}")
            print_info("提示：")
            print("  1. 检查 Ollama 是否在运行")
            print("     → 运行: ollama serve")
            print("  2. 检查 OLLAMA_BASE_URL 配置")
            print("     → 当前: " + str(self.agent.config.ollama_base_url))
            print("  3. 检查模型是否已下载")
            print("     → 运行: ollama pull " + self.agent.config.ollama_model)
            return

        # Model not found
        if "model" in err_str and ("not found" in err_str or "404" in err_str):
            print_error(f"模型未找到: {e}")
            print_info("提示：")
            print(f"  当前模型: {self.agent.config.ollama_model}")
            print("  → 下载: ollama pull " + self.agent.config.ollama_model)
            return

        # Module not found
        if "modulenotfounderror" in err_str or "no module named" in err_str:
            print_error(f"依赖缺失: {e}")
            print_info("提示：")
            print("  → 重新安装依赖: pip install --force-reinstall zai-agent")
            return

        # Generic error
        print_error(f"{type(e).__name__}: {e}")
        import traceback

        print()
        if "--debug" in str(e):
            traceback.print_exc()


# Helper for use in main.py
def run_interactive_repl(
    agent,
    logger: SessionLogger,
    *,
    stream: bool = True,
    max_retries: int = 3,
) -> int:
    """Convenience entry point used by ``_main``."""
    repl = EnhancedREPL(
        agent=agent,
        logger=logger,
        stream=stream,
        max_retries=max_retries,
    )
    repl.run()
    return 0
