"""Sandbox factory — connect to Strands SDK's native ``Sandbox`` system.

This module is **separate from** ``WorkspaceSandboxHook`` (security.py) by
design. Two different enforcement layers:

  �────────────────────────┬──────────────────────────────────────────────┐
  │ WorkspaceSandboxHook   │ Sandbox (this module)                         │
  │ (security.py)          │                                              │
  ├────────────────────────┼──────────────────────────────────────────────┤
  │ Path-level enforcement │ Execution-level enforcement                  │
  │ BeforeToolCallEvent    │ Runtime context for code/shell/file ops      │
  │ Veto out-of-workspace  │ Docker / SSH / local subprocess isolation    │
  │ write/edit/read calls  │                                              │
  │                        │                                              │
  │ Strands hook API       │ Strands Sandbox API                          │
  │ hooks=[...]            │ Agent(sandbox=...)                           │
  └────────────────────────┴──────────────────────────────────────────────┘

Both are wired into ``StrandsAgent`` — they are complementary, not
mutually exclusive. The hook vetoes bad *intent* (model asks for an
out-of-workspace path); the sandbox confines bad *execution* (a buggy
or malicious tool can't escape into the host).

Modes (``config.execution_sandbox``):

  - ``"host"`` (default): ``NotASandboxLocalEnvironment`` — zero isolation,
                          file I/O directly on host FS. Path-level safety
                          relies entirely on the hook layer.
  - ``"posix"``:           Local bash subprocess (PosixShellSandbox subclass).
                          Useful when the host has bash + base64; provides
                          a streaming command interface without Docker.
  - ``"docker"``:          DockerSandbox (requires a running container).
  - ``"ssh"``:             SshSandbox (requires SSH host config).

The Docker / SSH modes need runtime configuration (``container=``,
``host=``, etc.) — see ``.env.example``. If misconfigured, the factory
raises a clear ``ValueError`` instead of silently falling back.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import sys
from typing import TYPE_CHECKING

from strands.sandbox import Sandbox

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)


# Supported sandbox modes — kept as a module constant so callers (CLI,
# tests, README) can introspect without importing Config.
SANDBOX_MODES: tuple[str, ...] = ("host", "posix", "docker", "ssh")


def build_sandbox(config: Config) -> Sandbox:
    """Build a Strands ``Sandbox`` instance from ``Config``.

    Args:
        config: Agent configuration. Reads ``execution_sandbox`` (mode) plus
            mode-specific fields:

              - ``sandbox_container``      (docker mode)
              - ``sandbox_container_workdir``  (docker mode, optional)
              - ``sandbox_container_user``     (docker mode, optional)
              - ``sandbox_ssh_host``       (ssh mode)
              - ``sandbox_ssh_user``       (ssh mode, optional)
              - ``sandbox_ssh_port``       (ssh mode, optional)

    Returns:
        A concrete ``Sandbox`` instance. Never returns ``None`` — the SDK
        itself defaults to ``NotASandboxLocalEnvironment`` when no sandbox
        is passed, so we mirror that explicit-defaults policy here.

    Raises:
        ValueError: If the configured mode is unknown, or required mode
            parameters are missing.
        ImportError: If the SDK's optional submodules (``docker``, ``ssh``)
            are not installed in this environment.
    """
    mode = (getattr(config, "execution_sandbox", "host") or "host").strip().lower()

    if mode == "host":
        from strands.sandbox.not_a_sandbox_local_environment import (
            NotASandboxLocalEnvironment,
        )
        logger.info("Sandbox: host (NotASandboxLocalEnvironment, no isolation)")
        return NotASandboxLocalEnvironment()

    if mode == "posix":
        sandbox = _build_posix_sandbox(config)
        logger.info("Sandbox: posix (LocalBashSandbox, host subprocess)")
        return sandbox

    if mode == "docker":
        sandbox = _build_docker_sandbox(config)
        logger.info("Sandbox: docker (container=%s)", config.sandbox_container)
        return sandbox

    if mode == "ssh":
        sandbox = _build_ssh_sandbox(config)
        logger.info(
            "Sandbox: ssh (host=%s, user=%s)",
            config.sandbox_ssh_host,
            config.sandbox_ssh_user or "<default>",
        )
        return sandbox

    raise ValueError(
        f"unknown execution_sandbox mode {mode!r}; must be one of {SANDBOX_MODES}"
    )


# --------------------------------------------------------------------- #
# Mode implementations
# --------------------------------------------------------------------- #
def _build_posix_sandbox(config: Config) -> Sandbox:
    """Build a ``PosixShellSandbox`` backed by the local ``bash``.

    The SDK's ``PosixShellSandbox`` is abstract — it implements file I/O
    and code execution via ``execute_streaming`` (which itself uses
    heredoc + base64). We subclass with an asyncio.subprocess backend so
    the result runs without external dependencies beyond ``bash``.
    """

    from strands.sandbox import ExecutionResult, PosixShellSandbox, StreamChunk

    class LocalBashSandbox(PosixShellSandbox):
        """``PosixShellSandbox`` backed by local ``bash`` via asyncio.subprocess.

        Inherits ``read_file`` / ``write_file`` / ``list_files`` /
        ``remove_file`` from ``PosixShellSandbox`` (which routes them
        through ``execute_streaming`` shell commands). Only
        ``execute_streaming`` is implemented here.

        Platform note: on Windows, ``bash.exe`` is the WSL launcher binary
        (NOT git-bash / msys2 bash). We invoke it via
        ``create_subprocess_exec(['bash', '-c', cmd])`` — NOT
        ``create_subprocess_shell(cmd)`` — because the latter routes
        through ``cmd.exe`` on Windows, which mangles bash-only syntax
        (``<<``, ``<``, ``$(...)``, ``/tmp`` paths, etc.).
        """

        @staticmethod
        def _maybe_translate(path: str) -> str:
            """Translate a Windows path to WSL/Linux path on Windows.

            Identity on POSIX. Returns the input unchanged if translation
            is not applicable (so callers can still pass POSIX paths
            like ``/tmp/foo`` which we never want to mangle).
            """
            import sys as _sys
            if _sys.platform != "win32":
                return path
            translated = _wslpath_windows_to_linux(path)
            return translated if translated is not None else path

        async def read_file(self, path: str, **kwargs):
            return await super().read_file(self._maybe_translate(path), **kwargs)

        async def write_file(self, path: str, content: bytes, **kwargs):
            return await super().write_file(
                self._maybe_translate(path), content, **kwargs,
            )

        async def remove_file(self, path: str, **kwargs):
            return await super().remove_file(self._maybe_translate(path), **kwargs)

        async def list_files(self, path: str, **kwargs):
            return await super().list_files(self._maybe_translate(path), **kwargs)

        async def execute_streaming(
            self,
            command: str,
            *,
            timeout: float | None = None,
            cwd: str | None = None,
            env: dict[str, str] | None = None,
            **kwargs,
        ):
            # Resolve the target cwd. On Windows + WSL bash, native
            # ``os.getcwd()`` returns a Windows path (``D:\\foo``) which
            # bash cannot ``cd`` to — we must translate to ``/mnt/d/foo``.
            if cwd is not None:
                target_cwd = cwd
            else:
                target_cwd = os.getcwd()
                if sys.platform == "win32":
                    target_cwd = _wslpath_windows_to_linux(target_cwd) or target_cwd

            env_prefix = ""
            if env:
                # Build ``export K=V;`` prefixes; quote V via shlex.quote
                # so values with spaces/special chars survive the shell.
                parts = [
                    f"export {k}={shlex.quote(str(v))}" for k, v in env.items()
                ]
                env_prefix = " ".join(parts) + " "

            full_command = f"cd {shlex.quote(target_cwd)} && {env_prefix}{command}"

            # On Windows, asyncio.create_subprocess_shell() routes through
            # cmd.exe, which interprets bash syntax as Windows commands and
            # returns confusing "文件名、目录名或卷标语法不正确" errors.
            # create_subprocess_exec(['bash', '-c', cmd]) bypasses cmd and
            # invokes bash directly. On POSIX the two are equivalent.
            if sys.platform == "win32":
                proc = await asyncio.create_subprocess_exec(
                    "bash", "-c", full_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    full_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except TimeoutError as e:
                proc.kill()
                await proc.wait()
                from strands.sandbox.errors import SandboxTimeoutError
                raise SandboxTimeoutError(command, timeout or 0.0) from e

            # Always decode as utf-8 with errors='replace'. The subprocess
            # inherits the console codepage which on Windows may be GBK /
            # cp936 / cp1252 — but hardcoding any of those breaks the
            # other locales (e.g. a Japanese cp932 console, or a UTF-8
            # ``chcp 65001`` session). utf-8 + replace is the only
            # safe choice that never raises and never silently corrupts
            # output for callers who pipe it into another tool.
            encoding = "utf-8"

            if stdout_b:
                yield StreamChunk(
                    data=stdout_b.decode(encoding, errors="replace"),
                    stream_type="stdout",
                )
            if stderr_b:
                yield StreamChunk(
                    data=stderr_b.decode(encoding, errors="replace"),
                    stream_type="stderr",
                )
            yield ExecutionResult(
                exit_code=proc.returncode or 0,
                stdout=stdout_b.decode(encoding, errors="replace"),
                stderr=stderr_b.decode(encoding, errors="replace"),
            )

    return LocalBashSandbox()


# --------------------------------------------------------------------- #
# Windows ↔ WSL path translation helper
# --------------------------------------------------------------------- #
def _wslpath_windows_to_linux(win_path: str) -> str | None:
    """Translate a Windows path to a WSL/Linux path.

    On Windows the host Python's ``os.getcwd()`` returns ``D:\\foo``
    style paths which WSL bash cannot ``cd`` to. The WSL convention
    is ``/mnt/<drive-lowercase>/foo``.

    We translate in-process (no subprocess) for speed: drive letters
    become ``/mnt/<lowercase>`` and ``\\`` becomes ``/``. If the path
    does not look like an absolute Windows path (``C:\\`` / ``D:\\`` /
    ``\\ UNC``) we return ``None`` so the caller can fall back.

    Args:
        win_path: Absolute Windows path (``C:\\...``, ``D:\\...``,
            or UNC ``\\\\server\\share``).

    Returns:
        The POSIX equivalent (``/mnt/c/...``), or ``None`` if the
        input doesn't look like a Windows absolute path.
    """
    if not win_path:
        return None
    # UNC path \\server\share\... → not handled (return None, let bash error)
    if win_path.startswith("\\\\"):
        return None
    # Drive letter path C:\foo or C:/foo → /mnt/c/foo
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", win_path)
    if not m:
        return None
    drive = m.group(1).lower()
    rest = m.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def _build_docker_sandbox(config: Config) -> Sandbox:
    container = (getattr(config, "sandbox_container", "") or "").strip()
    if not container:
        raise ValueError(
            "execution_sandbox='docker' requires SANDBOX_CONTAINER in .env "
            "(the running container's ID or name)."
        )
    try:
        from strands.sandbox.docker import DockerSandbox
    except ImportError as e:
        raise ImportError(
            "execution_sandbox='docker' requested but DockerSandbox is not "
            "importable. Install strands-agents with docker support."
        ) from e

    working_dir = (getattr(config, "sandbox_container_workdir", "") or "").strip() or None
    user = (getattr(config, "sandbox_container_user", "") or "").strip() or None
    return DockerSandbox(
        container=container,
        working_dir=working_dir,
        user=user,
    )


def _build_ssh_sandbox(config: Config) -> Sandbox:
    host = (getattr(config, "sandbox_ssh_host", "") or "").strip()
    if not host:
        raise ValueError(
            "execution_sandbox='ssh' requires SANDBOX_SSH_HOST in .env."
        )
    try:
        from strands.sandbox.ssh import SshSandbox
    except ImportError as e:
        raise ImportError(
            "execution_sandbox='ssh' requested but SshSandbox is not importable."
        ) from e

    user = (getattr(config, "sandbox_ssh_user", "") or "").strip() or None
    port = getattr(config, "sandbox_ssh_port", None)
    return SshSandbox(host=host, user=user, port=port)
