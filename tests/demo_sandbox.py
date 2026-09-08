"""Sandbox 体系实战。

Strands 1.53.0 Sandbox 用于隔离工具（特别是 file_editor / shell）执行环境：
  - Sandbox: 抽象基类
  - PosixShellSandbox: 抽象，需子类化（其内置默认实现了文件 I/O 与代码执行）
  - DockerSandbox: 通过 Docker 容器执行
  - SshSandbox: 通过 SSH 远程执行
  - NotASandboxLocalEnvironment: 默认（无隔离，仅主机执行）

涵盖 8 种模式：
  1. 类层级探索
  2. 默认 NotASandboxLocalEnvironment 行为
  3. 自定义 PosixShellSandbox 子类
  4. file_editor 路由到 sandbox
  5. 不同 sandbox 构造对比
  6. sandbox 注入到 Agent
  7. execute_code_streaming：用解释器跑 Python 代码流式输出
  8. 文件 I/O：read_file / write_file / list_files / remove_file

用法：
    python tests/demo_sandbox.py --pattern 1
    python tests/demo_sandbox.py --all
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _reset_logger():
    for name in ("strands", "strands.event_loop", "strands.tools", "strands.sandbox"):
        logging.getLogger(name).setLevel(logging.WARNING)
    # Windows 默认 GBK，无法打印路径异常里的 �；强制 utf-8 兼容
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _bash_available() -> bool:
    """Detect whether a POSIX bash with heredoc + base64 is available.

    On Windows the venv's python.exe defaults to cmd.exe (no heredoc, no
    base64). Detect bash / sh via shutil so we can skip the heavy patterns
    rather than crash on a missing utility.
    """
    import shutil
    return any(
        shutil.which(candidate)
        for candidate in ("bash", "sh", "/usr/bin/bash", "/bin/sh")
    )


def _to_bash_path(path: str) -> str:
    """Convert a native OS path to one WSL bash understands.

    Windows ``bash.exe`` is WSL bash: it can't parse backslash paths (each
    ``\\`` is parsed as an escape) and maps the C: drive as ``/mnt/c``.
    This rewrites ``C:\\Users\\foo`` → ``/mnt/c/Users/foo`` so subprocess
    can locate the temp file. POSIX hosts return the path unchanged.
    """
    if "\\" not in path:
        return path
    drive, rest = path[0], path[2:]  # "C:", drop the colon+backslash
    rest = rest.replace("\\", "/")
    return f"/mnt/{drive.lower()}{rest}"


def _run_bash_via_script_sync(command: str, *, cwd=None, env=None) -> tuple[int, bytes, bytes]:
    """Sync helper — write command to a temp .sh file and run via ``bash``.

    ``PosixShellSandbox`` uses heredocs (``base64 -d << 'EOF' ... EOF``)
    which are easy to mangle through ``bash -c "..."`` quoting on Windows.
    Writing to a temp ``.sh`` and ``bash <file>`` keeps the heredoc intact
    on every host. Returns ``(exit_code, stdout, stderr)``.
    """
    import os
    import subprocess
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".sh", prefix="strands_sbx_")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(command)
        proc = subprocess.run(
            ["bash", _to_bash_path(path)],
            capture_output=True,
            cwd=cwd,
            env=env,
        )
        return proc.returncode or 0, proc.stdout, proc.stderr
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


async def _run_bash_via_script(command: str, *, cwd=None, env=None) -> tuple[int, bytes, bytes]:
    """Async wrapper that offloads the blocking subprocess.run to a thread.

    Callers are inside ``asyncio`` loops already, so spinning up another one
    via ``asyncio.run`` would fail with "RuntimeError: cannot be called from
    a running event loop". Using ``asyncio.to_thread`` keeps the event loop
    responsive while the subprocess runs.
    """
    return await asyncio.to_thread(
        _run_bash_via_script_sync, command, cwd=cwd, env=env,
    )


# ============================================================================
# Pattern 1: 类层级探索
# ============================================================================
def pattern_1_hierarchy() -> dict:
    """探索 Sandbox 类的层级关系和抽象方法。"""
    from strands.sandbox import (
        PosixShellSandbox,
        Sandbox,
    )

    # 1. 继承关系
    hierarchy = []
    for cls in (Sandbox, PosixShellSandbox):
        mro = [c.__name__ for c in cls.__mro__]
        hierarchy.append({"class": cls.__name__, "mro": mro, "module": cls.__module__})

    # 2. 抽象方法
    abstract_methods = []
    for name in dir(PosixShellSandbox):
        attr = getattr(PosixShellSandbox, name, None)
        if getattr(attr, "__isabstractmethod__", False):
            abstract_methods.append(name)

    # 3. 已知具体子类
    concrete = []
    try:
        from strands.sandbox.docker import DockerSandbox
        concrete.append({"class": "DockerSandbox", "module": DockerSandbox.__module__})
    except ImportError:
        pass
    try:
        from strands.sandbox.ssh import SshSandbox
        concrete.append({"class": "SshSandbox", "module": SshSandbox.__module__})
    except ImportError:
        pass

    # 4. 默认 sandbox（无隔离）
    concrete.append({
        "class": "NotASandboxLocalEnvironment",
        "note": "Agent 默认使用，无隔离",
    })

    return {
        "hierarchy": hierarchy,
        "abstract_methods": abstract_methods,
        "concrete_subclasses": concrete,
        "explanation": "Sandbox 是抽象基类；PosixShellSandbox 也抽象；具体实现需实现 execute_streaming",
    }


# ============================================================================
# Pattern 2: 默认 NotASandboxLocalEnvironment 行为
# ============================================================================
def pattern_2_default_behavior() -> dict:
    """不传 sandbox 时，Agent 用 NotASandboxLocalEnvironment（无隔离）。"""
    from strands import Agent
    from strands.sandbox.not_a_sandbox_local_environment import NotASandboxLocalEnvironment
    from strands_tools import calculator

    # 1. Agent 不传 sandbox 时
    agent = Agent(model=None, tools=[calculator], callback_handler=None)
    sandbox_type = type(agent._sandbox).__name__

    # 2. NotASandboxLocalEnvironment 的方法
    methods = [m for m in dir(NotASandboxLocalEnvironment) if not m.startswith("_")]

    return {
        "default_sandbox_type": sandbox_type,
        "agent_sandbox_module": type(agent._sandbox).__module__,
        "sandbox_methods": methods,
        "warning": "NotASandboxLocalEnvironment 没有隔离！生产必须替换",
    }


# ============================================================================
# Pattern 3: 自定义 PosixShellSandbox 子类
# ============================================================================
def pattern_3_custom_sandbox() -> dict:
    """实现一个用 asyncio.subprocess 的本地 sandbox。"""

    from strands.sandbox import ExecutionResult, PosixShellSandbox, StreamChunk

    class LocalSubprocessSandbox(PosixShellSandbox):
        """本地 asyncio.subprocess 实现的 sandbox。"""

        async def execute_streaming(
            self,
            command,
            *,
            timeout=None,
            cwd=None,
            env=None,
            **kwargs,
        ):
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            stdout, stderr = await proc.communicate()
            # StreamType 是 Literal['stdout', 'stderr']，直接传字符串
            yield StreamChunk(stream_type="stdout", data=stdout.decode(errors="replace"))
            if stderr:
                yield StreamChunk(stream_type="stderr", data=stderr.decode(errors="replace"))
            yield ExecutionResult(
                exit_code=proc.returncode,
                stdout=stdout.decode(errors="replace"),
                stderr=stderr.decode(errors="replace"),
            )

    # 实例化（子类实现了抽象方法）
    sandbox = LocalSubprocessSandbox()

    # 实际跑一个命令
    async def run():
        results = []
        async for item in sandbox.execute_streaming("echo hello from custom sandbox"):
            results.append({
                "type": type(item).__name__,
                "data": str(item)[:100],
            })
        return results

    chunks = asyncio.run(run("hello") if False else run())
    return {
        "sandbox_class": type(sandbox).__name__,
        "is_PosixShellSandbox_subclass": isinstance(sandbox, PosixShellSandbox),
        "chunks": chunks,
    }


# ============================================================================
# Pattern 4: file_editor 路由到 sandbox
# ============================================================================
def pattern_4_file_editor_routing() -> dict:
    """把 file_editor 绑定到自定义 sandbox。"""
    from strands.vended_tools.file_editor import file_editor, make_file_editor

    # 1. 默认 file_editor（无 sandbox）
    default_editor = file_editor
    default_spec_name = default_editor.tool_spec["name"]

    # 2. 用 make_file_editor 自定义（可以绑定 sandbox）
    # 不绑定 sandbox：和默认一样
    unbound = make_file_editor()
    unbound_name = unbound.tool_spec["name"]

    # 3. make_file_editor 的关键参数
    import inspect
    sig = inspect.signature(make_file_editor)

    return {
        "default_editor_name": default_spec_name,
        "unbound_make_editor_name": unbound_name,
        "make_file_editor_sig": str(sig),
        "explanation": "make_file_editor(sandbox=None, name='file_editor', description=...)",
    }


# ============================================================================
# Pattern 5: 不同 sandbox 构造对比
# ============================================================================
def pattern_5_construction() -> dict:
    """对比不同 sandbox 的构造参数。"""
    import inspect

    sigs = {}
    # 1. DockerSandbox
    try:
        from strands.sandbox.docker import DockerSandbox
        sigs["DockerSandbox"] = str(inspect.signature(DockerSandbox.__init__))
    except ImportError as e:
        sigs["DockerSandbox_error"] = str(e)

    # 2. SshSandbox
    try:
        from strands.sandbox.ssh import SshSandbox
        sigs["SshSandbox"] = str(inspect.signature(SshSandbox.__init__))
    except ImportError as e:
        sigs["SshSandbox_error"] = str(e)

    # 3. PosixShellSandbox (abstract)
    from strands.sandbox import PosixShellSandbox
    sigs["PosixShellSandbox"] = str(inspect.signature(PosixShellSandbox.__init__))

    # 4. 尝试构造 DockerSandbox（如果 docker 不可用会失败）
    docker_constructible = False
    docker_error = None
    try:
        from strands.sandbox.docker import DockerSandbox
        # 不实际调用（避免阻塞），只看能不能 import + 构造
        # DockerSandbox(container="nonexistent")  # 会调用 docker exec，可能报错
        # 只检查 class 是否可调用
        docker_constructible = callable(DockerSandbox)
    except Exception as e:
        docker_error = str(e)

    return {
        "sandbox_signatures": sigs,
        "docker_constructible": docker_constructible,
        "docker_error": docker_error,
    }


# ============================================================================
# Pattern 6: sandbox 注入到 Agent
# ============================================================================
def pattern_6_agent_injection() -> dict:
    """把 sandbox 注入到 Agent，看 _sandbox 字段。"""

    from strands import Agent
    from strands.sandbox import ExecutionResult, PosixShellSandbox, StreamChunk
    from strands_tools import calculator

    class TestSandbox(PosixShellSandbox):
        async def execute_streaming(self, command, *, timeout=None, cwd=None, env=None, **kwargs):
            yield StreamChunk(stream_type="stdout", data="[TestSandbox output]")
            yield ExecutionResult(exit_code=0, stdout="", stderr="")

    sandbox = TestSandbox()
    agent = Agent(model=None, tools=[calculator], sandbox=sandbox, callback_handler=None)

    return {
        "agent_sandbox_type": type(agent._sandbox).__name__,
        "is_TestSandbox": isinstance(agent._sandbox, TestSandbox),
        "explanation": "Agent(sandbox=sandbox) 后，agent._sandbox 就是你的 sandbox 实例",
    }


# ============================================================================
# Pattern 7: execute_code_streaming（用解释器跑代码）
# ============================================================================
def pattern_7_execute_code_streaming() -> dict:
    """验证 PosixShellSandbox 内置的 execute_code_streaming：用 shell heredoc + base64
    把代码送到目标语言解释器。只需要在子类里实现 execute_streaming。

    源码要点（strands/sandbox/posix_shell.py）：
        command = f"base64 -d << '{eof}' | {language}\\n{encoded}\\n{eof}"
    """

    from strands.sandbox import ExecutionResult, PosixShellSandbox, StreamChunk

    class LocalShellSandbox(PosixShellSandbox):
        async def execute_streaming(
            self,
            command,
            *,
            timeout=None,
            cwd=None,
            env=None,
            **kwargs,
        ):
            # 把 command 写到 .sh 文件再 bash 执行，避免 heredoc 在
            # `bash -c "..."` 包裹后被破坏（见 _run_bash_via_script 注释）。
            exit_code, stdout_b, stderr_b = await _run_bash_via_script(
                command, cwd=cwd, env=env,
            )
            if stdout_b:
                yield StreamChunk(data=stdout_b.decode(errors="replace"), stream_type="stdout")
            if stderr_b:
                yield StreamChunk(data=stderr_b.decode(errors="replace"), stream_type="stderr")
            yield ExecutionResult(
                exit_code=exit_code,
                stdout=stdout_b.decode(errors="replace"),
                stderr=stderr_b.decode(errors="replace"),
            )

    sandbox = LocalShellSandbox()

    async def run():
        # 传入一段任意的 Python 代码，含引号、换行、特殊字符——全部由 base64 编码承载
        code = "import sys\nprint('hello from', sys.executable)\nprint(1 + 2 + 3)"
        events = []
        async for chunk in sandbox.execute_code_streaming(code, "python3"):
            events.append({
                "type": type(chunk).__name__,
                "stream_type": getattr(chunk, "stream_type", None),
                "exit_code": getattr(chunk, "exit_code", None),
                "data_head": (getattr(chunk, "data", "") or "")[:60],
            })
        return events

    if not _bash_available():
        return {
            "skipped": "no POSIX bash on this host",
            "note": "execute_code_streaming 需要 bash + base64 才能跑（见 strands/sandbox/posix_shell.py）",
        }
    events = asyncio.run(run())
    return {
        "explanation": "PosixShellSandbox.execute_code_streaming 通过 base64 + heredoc 把代码送到解释器，避免 shell 注入",
        "language": "python3",
        "events": events,
        "exit_code_seen_at_end": events[-1].get("exit_code") if events else None,
    }


# ============================================================================
# Pattern 8: 文件 I/O 抽象方法（read/write/list/remove）
# ============================================================================
def pattern_8_file_io() -> dict:
    """验证 PosixShellSandbox 内置的 4 个文件 I/O 抽象方法：它们全都通过 shell
    命令（base64、ls -1ap、rm、mkdir -p）路由到 execute_streaming。

    子类只需实现 execute_streaming，文件操作就能"白嫖"——这正是为什么大多数
    自定义后端都继承 PosixShellSandbox 而不是直接继承 Sandbox 抽象基类。
    """
    import tempfile

    from strands.sandbox import ExecutionResult, PosixShellSandbox, StreamChunk

    class LocalShellSandbox(PosixShellSandbox):
        async def execute_streaming(
            self,
            command,
            *,
            timeout=None,
            cwd=None,
            env=None,
            **kwargs,
        ):
            # 同 pattern_7：posix_shell 用 heredoc + base64，必须有 bash 才能跑
            exit_code, stdout_b, stderr_b = await _run_bash_via_script(
                command, cwd=cwd, env=env,
            )
            if stdout_b:
                yield StreamChunk(data=stdout_b.decode(errors="replace"), stream_type="stdout")
            if stderr_b:
                yield StreamChunk(data=stderr_b.decode(errors="replace"), stream_type="stderr")
            yield ExecutionResult(
                exit_code=exit_code,
                stdout=stdout_b.decode(errors="replace"),
                stderr=stderr_b.decode(errors="replace"),
            )

    if not _bash_available():
        return {
            "skipped": "no POSIX bash on this host",
            "note": "PosixShellSandbox 默认实现用 base64 + mkdir -p + ls，需要 bash",
            "alternative": "继承 Sandbox 抽象基类直接实现文件 I/O 即可绕开 shell 依赖",
        }

    async def run():
        sandbox = LocalShellSandbox()
        with tempfile.TemporaryDirectory() as tmp:
            # Windows tempfile returns ``C:\Users\...``; WSL bash doesn't
            # grok backslashes — normalize to ``/mnt/c/Users/...`` so
            # ``test -d`` / ``ls`` / ``mkdir -p`` inside bash see a real path.
            bash_tmp = _to_bash_path(tmp)
            hello_b = bash_tmp + "/hello.txt"
            nested_b = bash_tmp + "/sub/dir/nested.bin"

            # 1) write_file（含二进制字节）
            await sandbox.write_file(hello_b, b"Hello from sandbox!\n")
            await sandbox.write_file(nested_b, b"\x00\x01\x02binary\xff")

            # 2) read_file / read_text
            await sandbox.read_file(hello_b)
            text = await sandbox.read_text(hello_b)

            # 3) list_files
            entries = await sandbox.list_files(bash_tmp)

            # 4) remove_file
            await sandbox.remove_file(hello_b)
            list_after_remove = await sandbox.list_files(bash_tmp)

            # 同时验证：read_file 真读到了相同字节（round-trip）
            read_back = await sandbox.read_file(nested_b)
            return {
                "bash_tmp": bash_tmp,
                "read_text_strip": text.strip(),
                "nested_round_trip_match": read_back == b"\x00\x01\x02binary\xff",
                "list_before_remove": sorted(e.name for e in entries),
                "list_after_remove": sorted(e.name for e in list_after_remove),
            }

    return asyncio.run(run())


PATTERNS = {
    1: ("类层级探索", pattern_1_hierarchy),
    2: ("默认行为", pattern_2_default_behavior),
    3: ("自定义 PosixShellSandbox", pattern_3_custom_sandbox),
    4: ("file_editor 路由", pattern_4_file_editor_routing),
    5: ("不同 sandbox 构造", pattern_5_construction),
    6: ("sandbox 注入 Agent", pattern_6_agent_injection),
    7: ("execute_code_streaming", pattern_7_execute_code_streaming),
    8: ("文件 I/O 抽象方法", pattern_8_file_io),
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
    parser = argparse.ArgumentParser(description="Sandbox 6 种实战")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true")
    group.add_argument("--pattern", type=int, choices=list(PATTERNS.keys()))
    args = parser.parse_args()

    _reset_logger()
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
