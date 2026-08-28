"""Posix Sandbox 连通性自检脚本。

验证本地 POSIX 工具链 + SDK PosixShellSandbox 端到端：

  1. bash 在 PATH（PosixShellSandbox 假设 bash 或兼容 shell）
  2. base64 可用（SDK 用 base64 编码传输文件内容）
  3. mkdir -p 可用（SDK 创建父目录）
  4. ls -1ap 可用（SDK 列表目录）
  5. heredoc 正常工作（SDK 传二进制内容）
  6. python3 在 PATH（可选，验证 execute_code_streaming）
  7. SDK LocalBashSandbox 端到端（5 项 SDK 操作全跑）

用法：
    python tests/_check_posix_sandbox.py
    python tests/_check_posix_sandbox.py --skip-python   # 没 python3 也能跑

退出码：0 = 全过；非 0 = 失败数。
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys


def check(name: str, ok: bool, detail: str = "") -> bool:
    """Print one check result and return ok."""
    mark = "✅" if ok else "❌"
    print(f"{mark}  {name}")
    if detail:
        for line in detail.splitlines():
            print(f"      {line}")
    return ok


def step1_bash() -> bool:
    path = shutil.which("bash")
    if not path:
        return check("bash 在 PATH", False, "shutil.which('bash') = None")
    out = subprocess.run(["bash", "--version"], capture_output=True, text=True, timeout=5)
    ver = out.stdout.splitlines()[0]
    return check("bash 在 PATH", True, f"{path}\n      {ver}")


def step2_base64() -> bool:
    if not shutil.which("base64"):
        return check("base64 可用", False, "")
    # Round-trip a binary blob
    import base64 as b64
    payload = b"\x00\x01\xfe\xff binary \n test"
    encoded = b64.b64encode(payload).decode()
    proc = subprocess.run(
        ["bash", "-c", f"echo '{encoded}' | base64 -d"],
        capture_output=True, timeout=5,
    )
    if proc.returncode != 0 or proc.stdout != payload:
        return check(
            "base64 可用", False,
            f"round-trip 失败: exit={proc.returncode}, "
            f"got {proc.stdout!r} expected {payload!r}",
        )
    return check("base64 可用", True, f"round-trip OK ({len(payload)} bytes)")


def step3_mkdir() -> bool:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        nested = f"{td}/a/b/c"
        proc = subprocess.run(
            ["bash", "-c", f"mkdir -p {nested} && test -d {nested}"],
            capture_output=True, timeout=5,
        )
        ok = proc.returncode == 0
    return check("mkdir -p 可用", ok)


def step4_ls() -> bool:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        # Create two files + one dir to test ls -1ap parsing
        (Path := __import__("pathlib").Path(td) / "file.txt").write_text("x")
        (Path := __import__("pathlib").Path(td) / "subdir").mkdir()
        proc = subprocess.run(
            ["bash", "-c", f"ls -1ap {td}"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            return check("ls -1ap 可用", False, proc.stderr[:200])
        names = [
            l.rstrip("/") for l in proc.stdout.splitlines()
            if l and l not in ("./", "../")
        ]
        ok = set(names) >= {"file.txt", "subdir"}
    return check(
        "ls -1ap 可用", ok,
        f"列出 {len(names)} 项（期望至少包含 file.txt + subdir）",
    )


def step5_heredoc() -> bool:
    """Quoted heredoc can carry arbitrary content (the SDK relies on this)."""
    import tempfile
    payload = b"line1 with 'quotes' and \"double\"\nline2\twith\ttabs\n\x00\x01"
    import base64 as b64
    encoded = b64.b64encode(payload).decode()
    eof = "HEREDOC_TEST_EOF_42"
    with tempfile.NamedTemporaryFile(suffix=".out", delete=False) as f:
        out_path = f.name
    try:
        cmd = f"base64 -d << '{eof}' > {out_path}\n{encoded}\n{eof}"
        proc = subprocess.run(["bash", "-c", cmd], capture_output=True, timeout=5)
        if proc.returncode != 0:
            return check("heredoc + base64 写入", False, proc.stderr.decode(errors="replace")[:200])
        with open(out_path, "rb") as f:
            got = f.read()
        ok = got == payload
    finally:
        try:
            __import__("os").unlink(out_path)
        except OSError:
            pass
    return check("heredoc + base64 写入", ok, f"round-trip {'OK' if ok else 'MISMATCH'}")


def step6_python() -> bool:
    p = shutil.which("python3") or shutil.which("python")
    if not p:
        return check("python3 在 PATH（可选）", False, "execute_code_streaming 用不了")
    out = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=5)
    return check(
        "python3 在 PATH（可选）", True,
        f"{p}\n      {out.stdout.strip() or out.stderr.strip()}",
    )


async def step7_sdk_e2e() -> bool:
    """Build LocalBashSandbox via build_sandbox() and run all 6 SDK ops."""
    try:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))
        from strands_poc.config import get_config
        from strands_poc.sandbox import build_sandbox
    except ImportError as e:
        return check("SDK LocalBashSandbox 端到端", False, str(e))

    cfg = get_config()
    cfg.execution_sandbox = "posix"
    sb = build_sandbox(cfg)

    from strands.sandbox import ExecutionResult, PosixShellSandbox
    assert isinstance(sb, PosixShellSandbox), f"got {type(sb).__name__}"

    try:
        # 1. execute_streaming
        chunks = []
        async for c in sb.execute_streaming("echo ok && pwd"):
            chunks.append(c)
        last = chunks[-1]
        assert isinstance(last, ExecutionResult) and last.exit_code == 0

        # 2-5. file ops round-trip
        await sb.write_file("/tmp/posix_e2e.bin", b"\x00\x01binary\xff")
        data = await sb.read_file("/tmp/posix_e2e.bin")
        assert data == b"\x00\x01binary\xff"

        files = await sb.list_files("/tmp")
        names = [f.name for f in files]
        assert "posix_e2e.bin" in names

        await sb.remove_file("/tmp/posix_e2e.bin")
        files_after = await sb.list_files("/tmp")
        assert "posix_e2e.bin" not in [f.name for f in files_after]

        # 6. execute_code_streaming
        if shutil.which("python3"):
            code_chunks = []
            async for c in sb.execute_code_streaming("print(1+2+3)", "python3"):
                code_chunks.append(c)
            code_last = code_chunks[-1]
            assert isinstance(code_last, ExecutionResult) and code_last.exit_code == 0
            assert "6" in code_last.stdout

    except Exception as e:
        return check(
            "SDK LocalBashSandbox 端到端", False,
            f"{type(e).__name__}: {e}",
        )

    return check(
        "SDK LocalBashSandbox 端到端", True,
        "execute / write / read / list / remove / execute_code 全部通过",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Posix Sandbox 连通性自检")
    p.add_argument("--skip-python", action="store_true",
                   help="跳过 python3 检查（execute_code_streaming 不可用）")
    args = p.parse_args(argv)

    print("=" * 60)
    print("Posix Sandbox 连通性自检")
    print("=" * 60)

    results = [
        step1_bash(),
        step2_base64(),
        step3_mkdir(),
        step4_ls(),
        step5_heredoc(),
    ]
    if not args.skip_python:
        results.append(step6_python())

    # SDK e2e always runs (file ops don't need python3)
    results.append(asyncio.run(step7_sdk_e2e()))

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
