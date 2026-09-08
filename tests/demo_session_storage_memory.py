"""Session / Storage / Memory 实战。

Strands 1.53.0 三层持久化：
  - Storage: 字节级 key-value 存储（最底层）
  - Session: 跨调用的 agent 对话持久化（中间层）
  - Memory: 跨 session 的语义记忆（最高层）

涵盖 6 种模式：
  1. InMemoryStorage 字节读写
  2. LocalFileStorage 持久化到磁盘
  3. FileSessionManager 持久化 session
  4. SnapshotSessionManager 版本化快照
  5. MemoryManager 配置 + 注入
  6. MemoryStore 增删查

用法：
    python tests/demo_session_storage_memory.py --pattern 1
    python tests/demo_session_storage_memory.py --all
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _reset_logger():
    for name in ("strands", "strands.event_loop", "strands.session",
                 "strands.storage", "strands.memory"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _make_model():
    import os

    from strands.models.ollama import OllamaModel
    return OllamaModel(
        host=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
        model_id=os.getenv("OLLAMA_MODEL", "qwen3.8:27b"),
    )


# ============================================================================
# Pattern 1: InMemoryStorage 字节读写
# ============================================================================
async def pattern_1_inmemory_storage() -> dict:
    """最简单的 storage：dict-backed，进程内。"""
    from strands.storage import InMemoryStorage

    storage = InMemoryStorage()

    # 写入
    await storage.write("session-1/msg-001", b"hello world")
    await storage.write("session-1/msg-002", b"second message")
    await storage.write("session-2/msg-001", b"different session")

    # 读取
    data1 = await storage.read("session-1/msg-001")

    # 列出 keys
    all_session_1 = await storage.list("session-1")
    all_keys = await storage.list()

    # 删除
    await storage.delete("session-1/msg-002")

    return {
        "read_session_1_msg_001": data1.decode() if data1 else None,
        "list_session_1_prefix": all_session_1,
        "list_all_keys": all_keys,
        "after_delete": await storage.list("session-1"),
    }


# ============================================================================
# Pattern 2: LocalFileStorage 持久化到磁盘
# ============================================================================
async def pattern_2_local_file_storage() -> dict:
    """持久化到磁盘，跨进程可用。"""
    import tempfile

    from strands.storage import LocalFileStorage

    base_dir = Path(tempfile.mkdtemp(prefix="strands_storage_demo_"))
    storage = LocalFileStorage(str(base_dir))

    # 写入
    await storage.write("users/alice.json",
                         json.dumps({"name": "Alice", "role": "admin"}).encode())
    await storage.write("users/bob.json",
                         json.dumps({"name": "Bob", "role": "user"}).encode())

    # 读取
    alice_bytes = await storage.read("users/alice.json")

    # 列出
    all_users = await storage.list("users")

    # 文件系统视角
    files_created = sorted(p.name for p in (base_dir / "users").iterdir())

    return {
        "base_dir": str(base_dir),
        "alice_data": json.loads(alice_bytes.decode()) if alice_bytes else None,
        "list_users": all_users,
        "files_on_disk": files_created,
        "explanation": "Key 中 '/' 当目录分隔符；写入原子（先临时文件再 rename）",
    }


# ============================================================================
# Pattern 3: FileSessionManager 持久化 session
# ============================================================================
async def pattern_3_file_session_manager() -> dict:
    """用 FileSessionManager 让 Agent 的对话自动持久化。"""
    import tempfile

    from strands import Agent
    from strands.session import FileSessionManager
    from strands.storage import LocalFileStorage
    from strands_tools import calculator

    base_dir = Path(tempfile.mkdtemp(prefix="strands_session_demo_"))
    storage = LocalFileStorage(str(base_dir))
    session_manager = FileSessionManager(storage=storage, session_id="demo-session-001")

    # 第一次调用
    agent = Agent(
        model=_make_model(),
        tools=[calculator],
        session_manager=session_manager,
        callback_handler=None,
    )
    result1 = agent("What is 1+1?")

    # 第二次调用：会恢复上次的 messages
    result2 = agent("What did I just ask you?")

    return {
        "session_id": "demo-session-001",
        "storage_dir": str(base_dir),
        "result1_stop_reason": result1.stop_reason,
        "result2_stop_reason": result2.stop_reason,
        "agent_messages_count": len(agent.messages),
        "files_created": sorted(p.name for p in base_dir.rglob("*") if p.is_file()),
        "explanation": "FileSessionManager 自动通过 hooks 持久化 messages/state",
    }


# ============================================================================
# Pattern 4: SnapshotSessionManager 版本化快照
# ============================================================================
async def pattern_4_snapshot_session() -> dict:
    """SnapshotSessionManager 支持版本化和 checkpoint 回滚。"""
    import tempfile

    from strands import Agent
    from strands.session import SnapshotSessionManager
    from strands.storage import LocalFileStorage
    from strands_tools import calculator

    base_dir = Path(tempfile.mkdtemp(prefix="strands_snapshot_demo_"))
    storage = LocalFileStorage(str(base_dir))

    session_manager = SnapshotSessionManager(
        storage=storage,
        session_id="snap-001",
        save_latest_on="message",   # Literal: 'message' / 'invocation' / 'trigger'
    )

    agent = Agent(
        model=_make_model(),
        tools=[calculator],
        session_manager=session_manager,
        callback_handler=None,
    )
    agent("What is 2+2?")

    return {
        "session_id": "snap-001",
        "storage_dir": str(base_dir),
        "files": sorted(p.relative_to(base_dir).as_posix()
                        for p in base_dir.rglob("*") if p.is_file()),
        "explanation": "SnapshotSessionManager 用快照而非消息流，支持 time-travel",
    }


# ============================================================================
# Pattern 5: MemoryManager 配置
# ============================================================================
async def pattern_5_memory_manager() -> dict:
    """MemoryManager 配置：包含 memory_store、injection 设置等。"""
    from strands.memory import MemoryManager, MemoryManagerConfig

    # MemoryManager 需要至少一个 store；这里用一个 in-memory 的（如果有的话）
    # 1.53.0 的 MemoryManager(stores=...) 要求具体 store 类型
    config = MemoryManagerConfig(
        stores=[],          # 空 store 列表（演示用）
        search_tool_config=True,
        add_tool_config=False,
        injection=True,
    )

    # 看看 MemoryManager 接受什么
    import inspect
    sig = inspect.signature(MemoryManager.__init__)

    return {
        "config_keys": list(config.keys()),
        "memory_manager_sig": str(sig),
        "explanation": "stores 是 list[MemoryStore]，需自己实现一个具体的 store",
    }


# ============================================================================
# Pattern 6: Agent + SessionManager 跨调用
# ============================================================================
async def pattern_6_persistent_agent() -> dict:
    """演示：同一个 session_id 创建两个 Agent 实例，第二个能继承第一个的消息。"""
    import tempfile

    from strands import Agent
    from strands.session import FileSessionManager
    from strands.storage import LocalFileStorage
    from strands_tools import calculator

    base_dir = Path(tempfile.mkdtemp(prefix="strands_persist_demo_"))
    storage = LocalFileStorage(str(base_dir))
    session_manager = FileSessionManager(storage=storage, session_id="persistent-001")

    # Agent A：第一次调用
    agent_a = Agent(
        model=_make_model(),
        tools=[calculator],
        session_manager=session_manager,
        agent_id="agent-A",
        callback_handler=None,
    )
    agent_a("My favorite number is 42. Remember this.")

    messages_a = len(agent_a.messages)

    # Agent B：新实例，同 session_id 但不同 agent_id
    agent_b = Agent(
        model=_make_model(),
        tools=[calculator],
        session_manager=session_manager,
        agent_id="agent-B",
        callback_handler=None,
    )
    messages_b = len(agent_b.messages)

    # 验证：B 拿到了 A 的历史
    restored = messages_b > 0

    return {
        "session_id": "persistent-001",
        "agent_a_messages": messages_a,
        "agent_b_messages": messages_b,
        "history_restored": restored,
        "explanation": "同一 session_id 创建的 Agent 自动恢复历史",
    }


PATTERNS = {
    1: ("InMemoryStorage", pattern_1_inmemory_storage),
    2: ("LocalFileStorage", pattern_2_local_file_storage),
    3: ("FileSessionManager", pattern_3_file_session_manager),
    4: ("SnapshotSessionManager", pattern_4_snapshot_session),
    5: ("MemoryManager 配置", pattern_5_memory_manager),
    6: ("跨 Agent 持久化", pattern_6_persistent_agent),
}


def run_one(n: int) -> tuple[bool, dict]:
    name, fn = PATTERNS[n]
    print(f"\n{'=' * 70}")
    print(f"[Pattern {n}] {name}")
    print("=" * 70)
    start = time.time()
    try:
        payload = asyncio.run(fn())
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
    parser = argparse.ArgumentParser(description="Session/Storage/Memory 6 种实战")
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
