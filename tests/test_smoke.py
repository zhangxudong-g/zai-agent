"""Smoke tests for the Strands PoC.

These tests do **not** require a running Ollama instance or the
strands-agents SDK to be installed. They cover:

1. Config loading from env / .env
2. WorkspaceSandboxHook flags out-of-workspace write/edit actions
3. WorkspaceSandboxHook allows in-workspace actions
4. SessionLogger writes well-formed JSONL lines (matches claude-agent schema)
5. StreamConsumer translates Strands dict events into StreamChunks
6. Strands SDK importability check

The StreamConsumer tests feed synthetic dicts so we can verify the
shape of the consumer without depending on the SDK runtime.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the package importable when pytest is invoked from the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


import asyncio

from strands_poc.config import get_config
from strands_poc.security import WorkspaceSandboxHook
from strands_poc.stream import StreamConsumer
from strands_poc.trace import SessionLogger


# --------------------------------------------------------------------- #
# Mock BeforeToolCallEvent (no SDK dependency)
# --------------------------------------------------------------------- #
class _MockBeforeToolCallEvent:
    """Minimal stand-in for ``strands.hooks.BeforeToolCallEvent``."""

    def __init__(self, tool_use: dict, cancel_tool: str | None = None):
        self.tool_use = tool_use
        self.cancel_tool = cancel_tool


# --------------------------------------------------------------------- #
# 1. Config loading
# --------------------------------------------------------------------- #
def test_config_defaults(tmp_path: Path, monkeypatch) -> None:
    # Earlier tests may have imported strands_poc.agent, which auto-loads
    # .env via telemetry._ensure_dotenv_loaded(); clear any inherited
    # ALLOWED_TOOLS so this test exercises the documented default list.
    monkeypatch.delenv("ALLOWED_TOOLS", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:7b")
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("SESSION_LOG_DIR", str(tmp_path / "sessions"))

    cfg = get_config(env_file=None)
    assert cfg.ollama_base_url == "http://localhost:11434"
    assert cfg.ollama_model == "qwen3:7b"
    assert cfg.allowed_tools == [
        "read", "glob", "grep", "file_tree", "outline", "shell", "write", "edit",
    ]


def test_config_custom_tools(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:7b")
    monkeypatch.setenv("AGENT_WORKSPACE", ".")
    monkeypatch.setenv("SESSION_LOG_DIR", ".")
    monkeypatch.setenv("ALLOWED_TOOLS", "Read,Glob")
    cfg = get_config(env_file=None)
    # Tools are lowercased per Strands convention.
    assert cfg.allowed_tools == ["read", "glob"]


# --------------------------------------------------------------------- #
# 2. WorkspaceSandboxHook
# --------------------------------------------------------------------- #
def test_sandbox_blocks_outside_path(tmp_path: Path) -> None:
    hook = WorkspaceSandboxHook(tmp_path)
    event = _MockBeforeToolCallEvent({"name": "write", "input": {"file_path": "/etc/passwd"}})
    hook.before_tool(event)
    assert event.cancel_tool is not None
    assert "Refusing write" in event.cancel_tool
    assert "/etc/passwd" in event.cancel_tool


def test_sandbox_blocks_edit_outside(tmp_path: Path) -> None:
    hook = WorkspaceSandboxHook(tmp_path)
    event = _MockBeforeToolCallEvent({"name": "edit", "input": {"file_path": "../outside.txt"}})
    hook.before_tool(event)
    assert event.cancel_tool is not None
    assert "Refusing edit" in event.cancel_tool


def test_sandbox_allows_inside_path(tmp_path: Path) -> None:
    hook = WorkspaceSandboxHook(tmp_path)
    event = _MockBeforeToolCallEvent({"name": "write", "input": {"file_path": "ok.txt"}})
    hook.before_tool(event)
    assert event.cancel_tool is None


def test_sandbox_allows_absolute_inside(tmp_path: Path) -> None:
    hook = WorkspaceSandboxHook(tmp_path)
    inside = tmp_path / "ok.txt"
    event = _MockBeforeToolCallEvent({"name": "write", "input": {"file_path": str(inside)}})
    hook.before_tool(event)
    assert event.cancel_tool is None


def test_sandbox_blocks_read_outside_path(tmp_path: Path) -> None:
    """Read-class tools MUST be vetoed when the target is outside workspace.

    Defense-in-depth contract (issue #1 fix): previously read/glob/grep
    bypassed the hook entirely, allowing models to read ``/etc/passwd``
    or ``~/.ssh/id_rsa``. Now they share the same workspace check as
    write/edit.
    """
    hook = WorkspaceSandboxHook(tmp_path)
    for name in ("read", "outline"):
        event = _MockBeforeToolCallEvent(
            {"name": name, "input": {"file_path": "/etc/passwd"}},
        )
        hook.before_tool(event)
        assert event.cancel_tool is not None, f"{name} should veto out-of-ws path"
        assert "path outside workspace" in event.cancel_tool


def test_sandbox_blocks_grep_outside_base(tmp_path: Path) -> None:
    """Grep with ``path`` pointing outside workspace must be vetoed."""
    hook = WorkspaceSandboxHook(tmp_path)
    event = _MockBeforeToolCallEvent(
        {"name": "grep", "input": {"pattern": "x", "path": "/etc"}},
    )
    hook.before_tool(event)
    assert event.cancel_tool is not None
    assert "path outside workspace" in event.cancel_tool


def test_sandbox_blocks_filetree_outside_base(tmp_path: Path) -> None:
    """file_tree with ``path`` outside workspace must be vetoed."""
    hook = WorkspaceSandboxHook(tmp_path)
    event = _MockBeforeToolCallEvent(
        {"name": "file_tree", "input": {"path": "/etc"}},
    )
    hook.before_tool(event)
    assert event.cancel_tool is not None


def test_sandbox_allows_grep_without_base(tmp_path: Path) -> None:
    """Grep without ``path`` defaults to workspace root — safe, don't veto."""
    hook = WorkspaceSandboxHook(tmp_path)
    event = _MockBeforeToolCallEvent(
        {"name": "grep", "input": {"pattern": "x"}},
    )
    hook.before_tool(event)
    assert event.cancel_tool is None


def test_sandbox_case_insensitive_tool_name(tmp_path: Path) -> None:
    """Tool name match must be case-insensitive (issue #5 fix).

    A community tool registered as ``Read`` (capital R) must still
    trigger the sandbox veto.
    """
    hook = WorkspaceSandboxHook(tmp_path)
    event = _MockBeforeToolCallEvent(
        {"name": "Read", "input": {"file_path": "/etc/passwd"}},
    )
    hook.before_tool(event)
    assert event.cancel_tool is not None
    # The cancellation message preserves the original capitalisation.
    assert "Refusing Read" in event.cancel_tool


def test_sandbox_glob_is_anchored(tmp_path: Path) -> None:
    """Glob has no path argument; it is workspace-anchored by design.

    The hook has nothing to inspect, so we must not veto on glob calls
    (the tool itself only walks ``workspace.rglob(pattern)``).
    """
    hook = WorkspaceSandboxHook(tmp_path)
    event = _MockBeforeToolCallEvent(
        {"name": "glob", "input": {"pattern": "../**/*.py"}},
    )
    hook.before_tool(event)
    assert event.cancel_tool is None


def test_sandbox_skips_when_path_empty(tmp_path: Path) -> None:
    """If the model hasn't supplied a path yet (input is empty), don't veto."""
    hook = WorkspaceSandboxHook(tmp_path)
    event = _MockBeforeToolCallEvent({"name": "write", "input": {}})
    hook.before_tool(event)
    assert event.cancel_tool is None


def test_sandbox_register_hooks_smoke(tmp_path: Path) -> None:
    """``register_hooks`` should accept a registry-like object."""
    hook = WorkspaceSandboxHook(tmp_path)

    class _Registry:
        def __init__(self):
            self.callbacks: list = []

        def add_callback(self, event_type, callback):
            self.callbacks.append((event_type, callback))

    registry = _Registry()
    hook.register_hooks(registry)
    assert len(registry.callbacks) == 1


# --------------------------------------------------------------------- #
# 3. SessionLogger JSONL shape
# --------------------------------------------------------------------- #
def test_session_logger_writes_well_formed_jsonl(tmp_path: Path) -> None:
    logger = SessionLogger(session_id="test_sid", log_dir=tmp_path)
    logger.session_start()
    logger.user_prompt("hello")
    logger.agent_start("hello")
    logger.tool_call_start(tool_name="read", tool_call_id="abc", arguments={"file_path": "x"})
    logger.tool_call_end(tool_call_id="abc", result="file contents", tool_name="read")
    logger.result_message(
        subtype="success",
        is_error=False,
        num_turns=2,
        duration_ms=123,
        stop_reason="end_turn",
    )
    logger.agent_end("done")
    logger.session_summary(duration_ms=123, model="x", tokens={"input": 1, "output": 2, "total": 3})
    logger.session_end()

    lines = (tmp_path / "test_sid.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 9
    parsed = [json.loads(line) for line in lines]
    events = [r["event"] for r in parsed]
    assert events == [
        "session_start",
        "user_prompt",
        "agent_start",
        "tool_call_start",
        "tool_call_end",
        "result",
        "agent_end",
        "session_summary",
        "session_end",
    ]
    assert [r["sequence"] for r in parsed] == list(range(1, 10))
    assert {r["session_id"] for r in parsed} == {"test_sid"}


def test_session_logger_writes_error(tmp_path: Path) -> None:
    logger = SessionLogger(session_id="err_sid", log_dir=tmp_path)
    logger.session_start()
    logger.log_error(error="boom", context={"phase": "test"})
    logger.session_end()
    events = [
        json.loads(line) for line in (tmp_path / "err_sid.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    err = next(e for e in events if e["event"] == "error")
    assert err["error"] == "boom"
    assert err["context"] == {"phase": "test"}


def test_session_logger_writes_message(tmp_path: Path) -> None:
    logger = SessionLogger(session_id="msg_sid", log_dir=tmp_path)
    logger.session_start()
    logger._write_message("[compaction] some summary")
    logger.session_end()
    events = [
        json.loads(line) for line in (tmp_path / "msg_sid.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    msg = next(e for e in events if e["event"] == "message")
    assert "compaction" in msg["content"]


# --------------------------------------------------------------------- #
# 4. StreamConsumer — Strands dict event translation
# --------------------------------------------------------------------- #
def test_consumer_emits_text_for_data_event() -> None:
    consumer = StreamConsumer()
    chunks = consumer.feed({"data": "hello "})
    chunks += consumer.feed({"data": "world"})
    text_chunks = [c for c in chunks if c.kind == "text"]
    assert [c.text for c in text_chunks] == ["hello ", "world"]


def test_consumer_emits_thinking_for_reasoning_text() -> None:
    consumer = StreamConsumer()
    chunks = consumer.feed({"reasoningText": "let me think"})
    assert len(chunks) == 1
    assert chunks[0].kind == "thinking"
    assert chunks[0].thinking == "let me think"


def test_consumer_emits_tool_start_then_input() -> None:
    consumer = StreamConsumer()
    # First time we see the tool → tool_start
    chunks = consumer.feed({"current_tool_use": {
        "toolUseId": "t1", "name": "read", "input": {"file_path": "a.txt"}
    }})
    assert len(chunks) == 1
    assert chunks[0].kind == "tool_start"
    assert chunks[0].tool_name == "read"
    assert chunks[0].tool_use_id == "t1"

    # Subsequent update → tool_input
    chunks = consumer.feed({"current_tool_use": {
        "toolUseId": "t1", "name": "read", "input": {"file_path": "a.txt", "encoding": "utf-8"}
    }})
    assert len(chunks) == 1
    assert chunks[0].kind == "tool_input"
    assert chunks[0].input_args == {"file_path": "a.txt", "encoding": "utf-8"}


def test_consumer_emits_done_for_result_event() -> None:
    consumer = StreamConsumer()

    class _MockResult:
        def __init__(self):
            self.message = "final answer"
            self.metrics = None
            self.stop_reason = "end_turn"

    chunks = consumer.feed({"result": _MockResult()})
    assert len(chunks) == 1
    assert chunks[0].kind == "done"
    assert chunks[0].result == "final answer"
    assert chunks[0].is_error is False
    assert chunks[0].stop_reason == "end_turn"
    assert consumer.done_emitted is True


def test_consumer_emits_done_only_once() -> None:
    consumer = StreamConsumer()
    event = {"result": type("R", (), {"message": "x", "metrics": None, "stop_reason": "end_turn"})()}
    chunks1 = consumer.feed(event)
    chunks2 = consumer.feed(event)
    assert len(chunks1) == 1
    assert chunks2 == []  # second time → no done chunk


def test_consumer_emits_force_stop() -> None:
    consumer = StreamConsumer()
    chunks = consumer.feed({"force_stop": True, "force_stop_reason": "max_turns"})
    assert len(chunks) == 1
    assert chunks[0].kind == "done"
    assert chunks[0].is_error is True
    assert chunks[0].stop_reason == "force_stop"
    assert "max_turns" in chunks[0].result


def test_consumer_handles_tool_stream_event() -> None:
    consumer = StreamConsumer()
    # First register the tool
    consumer.feed({"current_tool_use": {
        "toolUseId": "t2", "name": "grep", "input": {"pattern": "x"}
    }})
    # Now feed tool_stream_event with updated input
    chunks = consumer.feed({"tool_stream_event": {
        "tool_use": {"toolUseId": "t2", "name": "grep", "input": {"pattern": "x", "path": "src"}}
    }})
    assert len(chunks) == 1
    assert chunks[0].kind == "tool_input"
    assert chunks[0].input_args == {"pattern": "x", "path": "src"}


# --------------------------------------------------------------------- #
# 5. Strands SDK importability check
# --------------------------------------------------------------------- #
def test_strands_sdk_importable() -> None:
    """Sanity check that the Strands SDK is importable in this environment.

    Skipped when the SDK is not installed yet.
    """
    strands = pytest.importorskip("strands", reason="strands-agents not installed")
    assert hasattr(strands, "Agent")

    # Ollama model class is in strands.models.ollama (either OllamaModel or Ollama).
    ollama_mod = pytest.importorskip("strands.models.ollama", reason="ollama model not available")
    assert hasattr(ollama_mod, "OllamaModel") or hasattr(ollama_mod, "Ollama")


def test_strands_hooks_importable() -> None:
    """Sanity check for the hooks module."""
    hooks = pytest.importorskip("strands.hooks", reason="strands.hooks not available")
    assert hasattr(hooks, "BeforeToolCallEvent")
    assert hasattr(hooks, "AfterToolCallEvent")
    assert hasattr(hooks, "HookProvider")


def test_strands_tool_decorator_importable() -> None:
    """Sanity check for the @tool decorator."""
    strands = pytest.importorskip("strands", reason="strands-agents not installed")
    assert hasattr(strands, "tool")


# --------------------------------------------------------------------- #
# 7. _resolve_within_sandbox (tool-layer path guard, issue #1 fix)
# --------------------------------------------------------------------- #
from strands_poc.tools import _resolve_within_sandbox


def test_resolve_within_sandbox_relative(tmp_path: Path) -> None:
    """Relative paths resolve against workspace and stay inside."""
    result = _resolve_within_sandbox(tmp_path, "src/main.py")
    assert isinstance(result, Path)
    assert result == (tmp_path / "src" / "main.py").resolve()


def test_resolve_within_sandbox_absolute_inside(tmp_path: Path) -> None:
    """Absolute path that points inside workspace is allowed."""
    inside = (tmp_path / "data.txt").resolve()
    result = _resolve_within_sandbox(tmp_path, str(inside))
    assert isinstance(result, Path)
    assert result == inside


def test_resolve_within_sandbox_absolute_outside(tmp_path: Path) -> None:
    """Absolute path outside workspace returns an error string."""
    result = _resolve_within_sandbox(tmp_path, "/etc/passwd")
    assert isinstance(result, str)
    assert "[ERROR]" in result
    assert "outside workspace" in result


def test_resolve_within_sandbox_traversal(tmp_path: Path) -> None:
    """``../`` traversal that escapes workspace must be rejected."""
    result = _resolve_within_sandbox(tmp_path, "../../etc/passwd")
    assert isinstance(result, str)
    assert "[ERROR]" in result


def test_resolve_within_sandbox_empty(tmp_path: Path) -> None:
    """Empty path argument is rejected (not silently passed through)."""
    result = _resolve_within_sandbox(tmp_path, "", label="file_path")
    assert isinstance(result, str)
    assert "empty" in result


def test_resolve_within_sandbox_symlink_escape(tmp_path: Path) -> None:
    """Symlink inside workspace that points outside must be caught.

    ``Path.resolve()`` follows symlinks by default; if a malicious
    symlink lives at ``workspace/link_to_etc -> /etc``, then
    ``read("link_to_etc/passwd")`` resolves to ``/etc/passwd`` and
    ``relative_to(workspace)`` raises ``ValueError``.
    """
    link = tmp_path / "link_to_etc"
    try:
        link.symlink_to("/etc")
    except (OSError, NotImplementedError):
        pytest.skip("symlink not supported on this filesystem")

    result = _resolve_within_sandbox(tmp_path, str(link / "passwd"))
    assert isinstance(result, str)
    assert "[ERROR]" in result


# --------------------------------------------------------------------- #
# 8. build_sandbox (SDK Sandbox integration, issue #2 fix)
# --------------------------------------------------------------------- #
def test_build_sandbox_host_default(tmp_path: Path, monkeypatch) -> None:
    """Default mode 'host' returns NotASandboxLocalEnvironment."""
    sandbox_mod = pytest.importorskip(
        "strands.sandbox", reason="strands.sandbox not available",
    )
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("EXECUTION_SANDBOX", "host")
    monkeypatch.delenv("SANDBOX_CONTAINER", raising=False)
    monkeypatch.delenv("SANDBOX_SSH_HOST", raising=False)

    from strands_poc.config import get_config
    from strands_poc.sandbox import build_sandbox

    cfg = get_config(env_file=None)
    cfg.execution_sandbox = "host"
    sandbox = build_sandbox(cfg)

    from strands.sandbox.not_a_sandbox_local_environment import (
        NotASandboxLocalEnvironment,
    )
    assert isinstance(sandbox, NotASandboxLocalEnvironment)
    assert sandbox_mod.Sandbox in type(sandbox).__mro__


def test_build_sandbox_unknown_mode_raises(tmp_path: Path, monkeypatch) -> None:
    """Unknown mode name must raise ValueError (no silent fallback)."""
    pytest.importorskip("strands.sandbox", reason="strands.sandbox not available")
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))

    from strands_poc.config import get_config
    from strands_poc.sandbox import build_sandbox

    cfg = get_config(env_file=None)
    cfg.execution_sandbox = "no_such_mode"
    with pytest.raises(ValueError, match="unknown execution_sandbox mode"):
        build_sandbox(cfg)


def test_build_sandbox_docker_missing_container(tmp_path: Path, monkeypatch) -> None:
    """docker mode without SANDBOX_CONTAINER must raise with a clear message."""
    pytest.importorskip("strands.sandbox", reason="strands.sandbox not available")
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))

    from strands_poc.config import get_config
    from strands_poc.sandbox import build_sandbox

    cfg = get_config(env_file=None)
    cfg.execution_sandbox = "docker"
    cfg.sandbox_container = ""
    with pytest.raises(ValueError, match="SANDBOX_CONTAINER"):
        build_sandbox(cfg)


def test_build_sandbox_ssh_missing_host(tmp_path: Path, monkeypatch) -> None:
    """ssh mode without SANDBOX_SSH_HOST must raise with a clear message."""
    pytest.importorskip("strands.sandbox", reason="strands.sandbox not available")
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))

    from strands_poc.config import get_config
    from strands_poc.sandbox import build_sandbox

    cfg = get_config(env_file=None)
    cfg.execution_sandbox = "ssh"
    cfg.sandbox_ssh_host = ""
    with pytest.raises(ValueError, match="SANDBOX_SSH_HOST"):
        build_sandbox(cfg)


def test_build_sandbox_posix_constructible(tmp_path: Path, monkeypatch) -> None:
    """posix mode produces a PosixShellSandbox subclass instance."""
    pytest.importorskip("strands.sandbox", reason="strands.sandbox not available")
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))

    from strands_poc.config import get_config
    from strands_poc.sandbox import build_sandbox

    cfg = get_config(env_file=None)
    cfg.execution_sandbox = "posix"
    sandbox = build_sandbox(cfg)

    from strands.sandbox import PosixShellSandbox
    assert isinstance(sandbox, PosixShellSandbox)
    # The sandbox inherits execute_streaming-only from our LocalBashSandbox.
    assert hasattr(sandbox, "execute_streaming")


def test_wslpath_translation_unit() -> None:
    """Unit-test _wslpath_windows_to_linux (no subprocess needed)."""
    from strands_poc.sandbox import _wslpath_windows_to_linux

    # Common Windows drive letters
    assert _wslpath_windows_to_linux(r"C:\Users\foo") == "/mnt/c/Users/foo"
    assert _wslpath_windows_to_linux(r"d:\agent\ws") == "/mnt/d/agent/ws"
    assert _wslpath_windows_to_linux(r"E:/mixed/slashes") == "/mnt/e/mixed/slashes"

    # Non-Windows paths must be left alone (return None, caller falls back)
    assert _wslpath_windows_to_linux("/tmp/foo") is None
    assert _wslpath_windows_to_linux("relative/path") is None
    assert _wslpath_windows_to_linux("") is None

    # UNC paths \\server\share are not handled (return None)
    assert _wslpath_windows_to_linux(r"\\server\share") is None


@pytest.mark.skipif(
    "sys.platform != 'win32'",
    reason="Windows-specific: verifies WSL bash works from native Windows Python",
)
def test_posix_mode_works_from_native_windows() -> None:
    """End-to-end: posix sandbox usable from Windows-native Python (not WSL).

    Runs the full SDK surface (read/write/list/remove/execute/execute_code)
    via ``bash.exe`` (the WSL launcher). This is the case where the
    original implementation broke: ``asyncio.create_subprocess_shell``
    routed through ``cmd.exe`` on Windows, mangling bash syntax.

    The fix uses ``create_subprocess_exec(['bash', '-c', cmd])`` and
    translates Windows paths (``D:\\foo``) to WSL paths (``/mnt/d/foo``).
    """
    pytest.importorskip("strands.sandbox", reason="strands.sandbox not available")

    import shutil
    import subprocess
    import tempfile

    # bash.exe (WSL launcher) must be on PATH
    if not shutil.which("bash"):
        pytest.skip("bash.exe not on PATH (WSL not installed/enabled)")

    # Create a probe file via bash in WSL's /tmp
    subprocess.run(
        ["bash", "-c", "echo posix-win-probe > /tmp/posix_win_probe.txt"],
        check=True, timeout=10,
    )

    from strands_poc.sandbox import _wslpath_windows_to_linux, build_sandbox

    # Build a sandbox pointed at a Windows-style workspace
    with tempfile.TemporaryDirectory() as td:
        # td is a Windows path like C:\Users\...\Temp\...
        cfg = type("Cfg", (), {
            "execution_sandbox": "posix",
            "agent_workspace": __import__("pathlib").Path(td),
            "ollama_base_url": "http://localhost:11434",
            "ollama_model": "x",
            "ollama_auth_token": "x",
            "session_log_dir": __import__("pathlib").Path(td),
            "allowed_tools": [],
            "sandbox_container": "", "sandbox_container_workdir": "",
            "sandbox_container_user": "", "sandbox_ssh_host": "",
            "sandbox_ssh_user": "", "sandbox_ssh_port": None,
        })()
        sb = build_sandbox(cfg)

        async def go():
            # 1. read_file POSIX path
            data = await sb.read_file("/tmp/posix_win_probe.txt")
            assert data == b"posix-win-probe\n"

            # 2. read_file Windows path (auto-translated)
            # Create the file first — WSL needs its own path form (/mnt/<drive>/...),
            # not a bare Windows path (C:/...), so reuse the project's translator.
            win_target = __import__("pathlib").Path(td) / "_win_probe.txt"
            wsl_target = _wslpath_windows_to_linux(str(win_target))
            assert wsl_target is not None, f"could not translate {win_target}"
            subprocess.run(
                ["bash", "-c", f"echo win-translated > {wsl_target}"],
                check=True, timeout=10,
            )
            data = await sb.read_file(str(win_target))
            assert data == b"win-translated\n"

            # 3. execute_streaming (basic echo + uname)
            chunks = []
            async for c in sb.execute_streaming("echo from-win-posix"):
                chunks.append(c)
            last = chunks[-1]
            assert hasattr(last, "exit_code") and last.exit_code == 0
            assert "from-win-posix" in last.stdout

        asyncio.run(go())

        # cleanup
        subprocess.run(["bash", "-c", "rm -f /tmp/posix_win_probe.txt"], timeout=5)


# --------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------- #
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
