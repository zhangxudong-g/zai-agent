# 💾 Session / Storage / Memory 实战

> 配套代码：`tests/demo_session_storage_memory.py`（6 种模式可一键复现）
> 验证时间：2026-08-26 / strands-agents 1.53.0
> 验证方式：`python tests/demo_session_storage_memory.py --all`

---

## 一、三层持久化架构

```
┌─────────────────────────────────────────────────┐
│  Memory（跨 session 的语义记忆）                   │  ← MemoryManager
│  - 自动提取关键信息                                │
│  - 检索 + 注入上下文                              │
└─────────────────────────────────────────────────┘
                          ↓ 存到
┌─────────────────────────────────────────────────┐
│  Session（单个 agent 的对话历史）                  │  ← SessionManager
│  - messages + state 持久化                        │
│  - 跨调用恢复                                     │
└─────────────────────────────────────────────────┘
                          ↓ 存到
┌─────────────────────────────────────────────────┐
│  Storage（字节级 key-value 后端）                  │  ← Storage Protocol
│  - LocalFileStorage / S3Storage / InMemoryStorage │
└─────────────────────────────────────────────────┘
```

**经验法则**：
- 想跨进程持久对话 → **Storage + SessionManager**
- 想跨 session 记住用户偏好 → **MemoryManager**

---

## 二、6 种模式速查

| 模式 | 用途 | 关键类 |
|------|------|-------|
| 1. InMemoryStorage | 进程内字节读写 | `InMemoryStorage()` |
| 2. LocalFileStorage | 持久化到磁盘 | `LocalFileStorage(dir)` |
| 3. FileSessionManager | 持久化 session | `FileSessionManager(storage, session_id)` |
| 4. SnapshotSessionManager | 版本化快照 | `SnapshotSessionManager(storage, session_id, save_latest_on=...)` |
| 5. MemoryManager 配置 | 配置 memory | `MemoryManager(stores=[...], injection=...)` |
| 6. 跨 Agent 持久化 | 同 session 不同 agent 共享 | 不同 `agent_id` |

---

## 三、demo 运行结果（实测）

```text
[Pattern 1] InMemoryStorage           PASS (0.6s)
[Pattern 2] LocalFileStorage          PASS (0.0s)
[Pattern 3] FileSessionManager        PASS (3.9s)
[Pattern 4] SnapshotSessionManager    PASS (1.7s)
[Pattern 5] MemoryManager 配置        PASS (0.0s)
[Pattern 6] 跨 Agent 持久化           PASS (2.4s)
结果：6/6 passed
```

---

## 四、Storage 层详解

### 4.1 `Storage` 接口

```python
class Storage(Protocol):
    async def write(key: str, data: bytes) -> None
    async def read(key: str) -> bytes
    async def delete(key: str) -> None
    async def list(prefix: str = "") -> list[str]
```

### 4.2 三种实现

| 类 | 用途 | 构造 |
|----|------|------|
| `InMemoryStorage()` | 进程内，dict | 无参数 |
| `LocalFileStorage(directory)` | 持久化到本地 | 目录路径 |
| `S3Storage(bucket, prefix, ...)` | AWS S3 | bucket 名 |

### 4.3 Key 规则

`/` 当目录分隔符：
```python
await storage.write("users/alice.json", data)
# → LocalFileStorage 写入 {base}/users/alice.json
# → InMemoryStorage 用 "users/alice.json" 作 dict key
```

---

## 五、Session 层详解

### 5.1 `SessionManager` 接口

```python
class SessionManager(HookProvider, ABC):
    def register_hooks(self, registry: HookRegistry) -> None:
        """自动注册到 Agent 的 hooks（MessageAddedEvent 等）"""
```

**关键是 SessionManager 通过 hooks 工作** —— Agent 加 message 时自动调用 SessionManager 持久化。

### 5.2 四种实现

| 类 | 用途 | 存储位置 |
|----|------|---------|
| `FileSessionManager(storage, session_id)` | 单文件，磁盘 | Storage 后端 |
| `S3SessionManager(bucket, session_id)` | S3 | AWS |
| `RepositorySessionManager(repo)` | 通用 Repository | 任意 backend |
| `SnapshotSessionManager(storage, session_id, save_latest_on=...)` | 版本化快照 | Storage 后端 |

### 5.3 SnapshotSessionManager 的关键参数

```python
SnapshotSessionManager(
    storage=storage,
    session_id="snap-001",
    save_latest_on="message",  # Literal: 'message' / 'invocation' / 'trigger'
)
```

`save_latest_on` 控制何时保存：
- `"message"`：每次新增 message 时
- `"invocation"`：每次 `agent(prompt)` 调用结束时
- `"trigger"`：手动触发

---

## 六、Memory 层详解

### 6.1 `MemoryManager`

```python
class MemoryManager(Plugin):
    def __init__(
        self,
        stores: list[MemoryStore],   # 必填：一个或多个具体 store
        search_tool_config: bool | MemoryToolConfig = True,
        add_tool_config: bool | MemoryToolConfig = False,  # opt-in（写权限）
        injection: bool | MemoryInjectionConfig = True,
        **kwargs,
    ):
```

**核心组件**：
- `stores`: 实际存记忆的 store（需要自己实现一个 MemoryStore）
- `search_tool_config`: 是否给 LLM 提供 search 工具
- `add_tool_config`: 是否给 LLM 提供 add 工具（默认 False，谨慎开启）
- `injection`: 是否在每轮自动注入相关记忆到上下文

### 6.2 自定义 MemoryStore

Strands 没有自带的具体 store，需要自己实现：

```python
from strands.memory import MemoryStore


class InMemoryStore(MemoryStore):
    def __init__(self):
        self.entries = []

    async def add(self, entry):
        self.entries.append(entry)

    async def search(self, query, limit=10): ...
    async def list(self): ...
    async def get(self, entry_id): ...
```

### 6.3 与 Agent 集成

```python
agent = Agent(
    model=model,
    memory_manager=MemoryManager(stores=[my_store]),
)
agent("Remember I prefer dark mode")  # → 提取为 memory entry
agent("What theme do I like?")  # → 自动 search 并注入
```

---

## 七、跨调用持久化示例

```python
import tempfile
from strands import Agent
from strands.storage import LocalFileStorage
from strands.session import FileSessionManager

storage = LocalFileStorage("./.sessions/")
session_manager = FileSessionManager(storage=storage, session_id="user-42")

# 第一次：自动保存
agent1 = Agent(model=model, session_manager=session_manager, agent_id="a1")
agent1("My name is Alice")

# 第二次（不同进程）：自动恢复
agent2 = Agent(model=model, session_manager=session_manager, agent_id="a1")
# agent2.messages 已经包含 agent1 的历史
```

⚠️ **同 session_id 下 agent_id 必须唯一**（否则报 SessionException）。

---

## 八、踩坑记录

### 🔴 坑 1：SnapshotSessionManager 没有 `save_strategy` 参数

```python
# ❌ 报错：Cannot instantiate typing.Literal
SnapshotSessionManager(storage=..., session_id=..., save_strategy=SaveLatestStrategy())

# ✅ 用 save_latest_on="message"/"invocation"/"trigger"
SnapshotSessionManager(storage=..., session_id=..., save_latest_on="message")
```

### 🔴 坑 2：同 session 下 agent_id 必须唯一

```python
# ❌ SessionException: The agent_id of an agent must be unique in a session
agent_a = Agent(session_manager=sm, agent_id="a")
agent_b = Agent(session_manager=sm, agent_id="a")  # 同 ID 报错

# ✅ 不同 agent_id
agent_a = Agent(session_manager=sm, agent_id="agent-A")
agent_b = Agent(session_manager=sm, agent_id="agent-B")
```

### 🔴 坑 3：Storage 操作是 async，必须 await

```python
# ❌ 不会执行
storage.write("k", b"v")

# ✅ 必须 await
await storage.write("k", b"v")
```

### 🟡 坑 4：MemoryManager 不带任何具体 store，需自己实现

```python
# ❌ stores=[] 会报"必须至少有一个 store"
MemoryManager(stores=[])


# ✅ 实现一个 MemoryStore 子类
class MyStore(MemoryStore):
    async def add(self, entry): ...
    async def search(self, query): ...


MemoryManager(stores=[MyStore()])
```

### 🟡 坑 5：LocalFileStorage 写入是原子的

```python
# 内部用 temp file + rename，不会有半写文件
await storage.write("big_file.json", large_bytes)  # 安全
```

### 🟡 坑 6：`add_tool_config=False` 是默认

LLM 默认**不能**写 memory，要显式开启（防止 LLM 写入垃圾）。

---

## 九、生产最佳实践

### 9.1 Storage 后端选择

```
单机 + 小数据   → LocalFileStorage
单机 + 中数据   → SQLite 或 Redis（自己包一层 Storage）
云原生         → S3Storage
临时 / 测试    → InMemoryStorage
```

### 9.2 Session 与 Agent 的关系

```python
# 一个用户 → 一个 session_id
# 一个 Agent 实例 → 一个 agent_id
# 用户多 Agent 协作 → 一个 session_id 下多个 agent_id

session_manager = FileSessionManager(storage=storage, session_id="user-42")
researcher = Agent(session_manager=session_manager, agent_id="researcher", ...)
writer = Agent(session_manager=session_manager, agent_id="writer", ...)
```

### 9.3 Memory 写入要谨慎

```python
# ❌ 默认 add_tool_config=False（安全）
MemoryManager(stores=[store])

# ⚠️ 开启写入（需审计）
MemoryManager(stores=[store], add_tool_config=True)

# ✅ 限制写入：只允许特定 store
MemoryManager(
    stores=[read_only_store, writable_store],
    add_tool_config=MemoryAddToolConfig(stores=[writable_store]),
)
```

### 9.4 SnapshotSessionManager 用于长任务恢复

```python
SnapshotSessionManager(
    storage=S3Storage(...),
    session_id="long-task-001",
    save_latest_on="message",  # 每次消息都存
)
# 进程崩溃后，重新创建 Agent 用同 session_id 自动恢复
```

---

## 十、参考

- 源码：
  - `strands/storage/storage.py`
  - `strands/storage/{local_file,in_memory,s3}_storage.py`
  - `strands/session/session_manager.py`
  - `strands/session/{file,s3,repository,snapshot}_session_manager.py`
  - `strands/memory/memory_manager.py`
  - `strands/memory/types.py`
- 官方文档：<https://strandsagents.com/docs/user-guide/concepts/sessions/>
- 本地验证：`tests/demo_session_storage_memory.py --all`