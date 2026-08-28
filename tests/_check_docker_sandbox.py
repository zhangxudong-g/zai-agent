"""Docker Sandbox 连通性自检脚本。

不依赖 PoC 业务代码，直接用 SDK 的 DockerSandbox 验证 5 件事：

  1. docker CLI 在 PATH
  2. docker daemon 可达（docker ps 不报错）
  3. 目标容器在跑（docker ps --filter）
  4. /workspace 在容器内存在（docker exec ... ls）
  5. SDK DockerSandbox 能真正执行命令（端到端）

用法：
    python tests/_check_docker_sandbox.py
    python tests/_check_docker_sandbox.py --container my-other-name --workdir /tmp

退出码：0 = 全过；非 0 = 失败步骤数（适合 CI / shell wrapper）。
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path


def check(name: str, ok: bool, detail: str = "") -> bool:
    """Print one check result and return ok."""
    mark = "✅" if ok else "❌"
    print(f"{mark}  {name}")
    if detail:
        for line in detail.splitlines():
            print(f"      {line}")
    return ok


def step1_docker_in_path() -> bool:
    """Is ``docker`` on PATH?"""
    path = shutil.which("docker")
    if not path:
        return check("docker CLI 在 PATH", False, "shutil.which('docker') = None")
    # Also confirm it's actually executable and reports a version
    try:
        out = subprocess.run(
            ["docker", "--version"], capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return check("docker CLI 在 PATH", False, f"无法执行: {e}")
    return check("docker CLI 在 PATH", True, f"{path}\n      {out.stdout.strip()}")


def step2_daemon_reachable() -> bool:
    """Can we talk to the Docker daemon?"""
    try:
        out = subprocess.run(
            ["docker", "ps"], capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return check("Docker daemon 可达", False, str(e))
    if out.returncode != 0:
        return check(
            "Docker daemon 可达", False,
            f"exit={out.returncode}\n      stderr: {out.stderr.strip()[:300]}",
        )
    return check("Docker daemon 可达", True, f"{len(out.stdout.splitlines()) - 1} 个运行中容器")


def step3_container_running(container: str) -> bool:
    """Is the target container up?"""
    try:
        out = subprocess.run(
            ["docker", "ps", "--filter", f"name={container}", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return check(f"容器 {container!r} 在跑", False, str(e))
    lines = [l for l in out.stdout.strip().splitlines() if l]
    if not lines:
        return check(
            f"容器 {container!r} 在跑", False,
            "容器不存在或已停止。需要先：\n"
            f"      docker run -d --name {container} --restart unless-stopped "
            "alpine:3.19 sleep infinity",
        )
    # First line should match container name (or a truncated form)
    first = lines[0].split("\t")
    if first[0] != container and not container.startswith(first[0]):
        return check(f"容器 {container!r} 在跑", False, f"找到的是 {first}")
    return check(f"容器 {container!r} 在跑", True, first[1])


def step4_workdir_exists(container: str, workdir: str) -> bool:
    """Does ``workdir`` exist inside the container?"""
    try:
        out = subprocess.run(
            ["docker", "exec", container, "ls", "-ld", workdir],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return check(f"工作目录 {workdir!r} 存在于容器内", False, str(e))
    if out.returncode != 0:
        return check(
            f"工作目录 {workdir!r} 存在于容器内", False,
            f"stderr: {out.stderr.strip()[:200]}\n"
            f"      修复: docker exec {container} mkdir -p {workdir}",
        )
    return check(f"工作目录 {workdir!r} 存在于容器内", True, out.stdout.strip())


async def step5_sdk_e2e(container: str, workdir: str) -> bool:
    """End-to-end: build SDK DockerSandbox and execute one command."""
    try:
        from strands.sandbox.docker import DockerSandbox
    except ImportError as e:
        return check("SDK DockerSandbox 可用", False, str(e))

    sb = DockerSandbox(container=container, working_dir=workdir)
    try:
        chunks = []
        async for chunk in sb.execute_streaming("echo hello-from-container && pwd"):
            chunks.append(chunk)
    except Exception as e:
        return check("SDK DockerSandbox 端到端", False, f"{type(e).__name__}: {e}")

    # Last chunk must be ExecutionResult with exit_code 0
    from strands.sandbox import ExecutionResult
    last = chunks[-1]
    if not isinstance(last, ExecutionResult):
        return check("SDK DockerSandbox 端到端", False, f"最后一项不是 ExecutionResult: {type(last)}")
    if last.exit_code != 0:
        return check(
            "SDK DockerSandbox 端到端", False,
            f"exit_code={last.exit_code}\n      stderr: {last.stderr[:200]}",
        )
    return check(
        "SDK DockerSandbox 端到端", True,
        f"exit_code=0\n      stdout: {last.stdout.strip()[:200]}",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Docker Sandbox 连通性自检")
    p.add_argument("--container", default="strands-poc-sandbox",
                   help="容器名 (默认: strands-poc-sandbox)")
    p.add_argument("--workdir", default="/workspace",
                   help="容器内工作目录 (默认: /workspace)")
    args = p.parse_args(argv)

    print("=" * 60)
    print(f"Docker Sandbox 连通性自检")
    print(f"  容器:   {args.container}")
    print(f"  工作目录: {args.workdir}")
    print("=" * 60)

    results = [
        step1_docker_in_path(),
        step2_daemon_reachable(),
        step3_container_running(args.container),
        step4_workdir_exists(args.container, args.workdir),
    ]

    # Only run e2e if all preconditions passed
    if all(results):
        results.append(asyncio.run(step5_sdk_e2e(args.container, args.workdir)))
    else:
        print("\n⏭️   跳过 SDK 端到端测试（前置条件未满足）")

    failed = sum(1 for r in results if not r)
    print("\n" + "=" * 60)
    if failed == 0:
        print(f"✅ 全部 {len(results)} 项通过")
    else:
        print(f"❌ {failed}/{len(results)} 项失败")
    print("=" * 60)
    return failed


if __name__ == "__main__":
    sys.exit(main())
