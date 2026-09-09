# 📦 Sandbox 体系实战

> 配套代码：`tests/demo_sandbox.py`（6 种模式可一键复现）
> 验证时间：2026-08-26 / strands-agents 1.53.0
> 验证方式：`python tests/demo_sandbox.py --all`

---

## 一、为什么需要 Sandbox

Agent 的工具（特别是 `file_editor` 和 `shell`）默认**直接在主机上执行** —— 等于给 LLM root 权限！

**风险**：
- 模型被诱导 → `rm -rf /`
- 工具 bug → 误删文件
- prompt injection → 远程下载恶意代码

**Sandbox 的作用**：把工具执行隔离到受控环境（容器、远程主机）。

---

## 二、Strands 1.53.0 Sandbox 类层级

```
Sandbox (抽象基类)
├── PosixShellSandbox (抽象，需子类化)
│   ├── DockerSandbox    [from strands.sandbox.docker]
│   └── SshSandbox       [from strands.sandbox.ssh]
└── NotASandboxLocalEnvironment (默认，无隔离！)
```

**关键事实**：
- `Sandbox` 是 `ABC`
- `PosixShellSandbox` 也是 `ABC`，需要实现 `execute_streaming` 抽象方法
- `DockerSandbox` / `SshSandbox` 是现成的具体实现
- `NotASandboxLocalEnvironment` 是默认 fallback（**没隔离**）

---

## 三、6 种模式速查

| 模式 | 用途 | 关键 API |
|------|------|---------|
| 1. 类层级探索 | 看清继承关系 | `Sandbox.__mro__`, `__abstractmethods__` |
| 2. 默认行为 | Agent 不传 sandbox 时 | `agent._sandbox` = `NotASandboxLocalEnvironment` |
| 3. 自定义 PosixShellSandbox | 用 asyncio.subprocess 写一个 | `class MySandbox(PosixShellSandbox)` |
| 4. file_editor 路由 | 把 file_editor 绑到 sandbox | `make_file_editor(sandbox=my_sandbox)` |
| 5. 不同 sandbox 构造 | DockerSandbox / SshSandbox 参数 | `inspect.signature(...)` |
| 6. sandbox 注入 Agent | `Agent(sandbox=...)` | `agent._sandbox` |

---

## 四、demo 运行结果（实测）

```text
[Pattern 1] 类层级探索                PASS (0.7s)
[Pattern 2] 默认行为                  PASS (0.6s)
[Pattern 3] 自定义 PosixShellSandbox  PASS (0.0s)
[Pattern 4] file_editor 路由          PASS (0.0s)
[Pattern 5] 不同 sandbox 构造         PASS (0.0s)
[Pattern 6] sandbox 注入 Agent        PASS (0.1s)
结果：6/6 passed
```

---

## 五、3 个具体 Sandbox 的构造

| 类 | 构造参数 | 用途 |
|----|---------|------|
| `DockerSandbox(container, working_dir=None, user=None)` | 容器内 `docker exec` | 容器化隔离 |
| `SshSandbox(host, username, ...)` | 远程 SSH 执行 | 跨主机隔离 |
| `NotASandboxLocalEnvironment()` | 无 | 默认 fallback |

**示例（DockerSandbox）**：
```python
from strands.sandbox.docker import DockerSandbox

sandbox = DockerSandbox(
    container="my-agent-container",  # 必须已运行
    working_dir="/work",
    user="1000:1000",
)
agent = Agent(sandbox=sandbox)
```

---

## 六、自定义 PosixShellSandbox（核心模式）

要实现一个完整 sandbox，只需继承 `PosixShellSandbox` 并实现 `execute_streaming`：

```python
from strands.sandbox import PosixShellSandbox, StreamChunk, ExecutionResult
import asyncio


class LocalSubprocessSandbox(PosixShellSandbox):
    async def execute_streaming(self, command, *, timeout=None, cwd=None, env=None, **kwargs):
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        stdout, stderr = await proc.communicate()

        # 注意字段名：stream_type 不是 type
        yield StreamChunk(stream_type="stdout", data=stdout.decode(errors="replace"))
        if stderr:
            yield StreamChunk(stream_type="stderr", data=stderr.decode(errors="replace"))

        # 注意字段名：stdout/stderr/exit_code，没有 status/command
        yield ExecutionResult(
            exit_code=proc.returncode,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )


sandbox = LocalSubprocessSandbox()
```

---

## 七、file_editor 路由到 sandbox

```python
from strands.vended_tools.file_editor import make_file_editor, file_editor
from my_sandbox import LocalSubprocessSandbox

# 1. 默认 file_editor（无 sandbox）
default_editor = file_editor  # 直接用本地文件系统

# 2. 绑定到 sandbox
sandbox = LocalSubprocessSandbox()
sandbox_editor = make_file_editor(sandbox=sandbox)

# 3. 注册到 Agent
agent = Agent(tools=[sandbox_editor])
```

**重点**：用 `make_file_editor(sandbox=...)` 显式绑定，而不是直接 `from strands.vended_tools import file_editor`。

**或者**：用 `Agent(sandbox=...)` 让 Agent 自动注入 file_editor（依赖 sandbox 的 `get_tools()` 方法）。

---

## 八、Sandbox 注入到 Agent

```python
from strands import Agent
from strands.sandbox.docker import DockerSandbox

# 方式 1：构造时传
agent = Agent(
    model=model,
    tools=[calculator],
    sandbox=DockerSandbox(container="agent-container"),
)

# 方式 2：不传（默认）
agent = Agent(model=model, tools=[calculator])
# agent._sandbox = NotASandboxLocalEnvironment()
```

Agent 内部会：
1. 检查 `sandbox` 是否是 `Sandbox` 实例（不是则报错）
2. 把 sandbox 存到 `self._sandbox`
3. 调用 `self._sandbox.get_tools()` 自动注入 file_editor / shell

---

## 九、踩坑记录

### 🔴 坑 1：PosixShellSandbox 是抽象类

```python
# ❌ 报错：Can't instantiate abstract class
sandbox = PosixShellSandbox()
# TypeError: Can't instantiate abstract class PosixShellSandbox
#             without an implementation for abstract method 'execute_streaming'

# ✅ 必须子类化
class MySandbox(PosixShellSandbox):
    async def execute_streaming(self, command, *, timeout=None, ...):
        ...
```

### 🔴 坑 2：StreamChunk 字段名是 `stream_type`，不是 `type`

```python
# ❌ 报错
yield StreamChunk(type="stdout", data=...)
# TypeError: StreamChunk.__init__() got an unexpected keyword argument 'type'

# ✅ 正确
yield StreamChunk(stream_type="stdout", data=...)
```

### 🔴 坑 3：StreamType 不是 Enum，是 Literal

```python
# ❌ StreamType.STDERR 不存在
from strands.sandbox import StreamType
yield StreamChunk(stream_type=StreamType.STDERR, ...)  # AttributeError

# ✅ 用字符串
yield StreamChunk(stream_type="stderr", ...)
```

### 🔴 坑 4：ExecutionResult 字段只有 `exit_code` / `stdout` / `stderr`

```python
# ❌ 没有 status / command 字段
yield ExecutionResult(command=cmd, exit_code=0, status="success")  # TypeError

# ✅ 用 stdout / stderr 字段
yield ExecutionResult(
    exit_code=0,
    stdout="...",
    stderr="",
)
```

### 🟡 坑 5：DockerSandbox 必须在子模块导入

```python
# ❌ 不可用
from strands.sandbox import DockerSandbox  # 不在 __all__

# ✅ 从子模块
from strands.sandbox.docker import DockerSandbox
```

### 🟡 坑 6：DockerSandbox 需要 Docker 守护进程

```python
DockerSandbox(container="...")
# 如果 docker daemon 没运行，会失败
# 错误：docker.errors.DockerException: Error while fetching server API version
```

### 🟡 坑 7：默认 sandbox 是无隔离的

```python
# ⚠️ 不传 sandbox = 没有任何保护
agent = Agent(model=model, tools=[shell])  # 危险！

# ✅ 必须传 sandbox（生产）
agent = Agent(model=model, tools=[shell], sandbox=DockerSandbox(...))
```

---

## 十、生产最佳实践

### 10.1 永远用 DockerSandbox（或 SshSandbox）

```python
from strands.sandbox.docker import DockerSandbox

sandbox = DockerSandbox(container="agent-sandbox")
```

### 10.2 容器权限最小化

```python
sandbox = DockerSandbox(
    container="agent",
    working_dir="/workspace",  # 限制工作目录
    user="1000:1000",  # 非 root
)
```

### 10.3 file_editor 一定要绑 sandbox

```python
agent = Agent(
    tools=[make_file_editor(sandbox=sandbox)],  # ✅
    # tools=[file_editor],                        # ❌ 默认无隔离
)
```

### 10.4 沙箱外的工具独立审阅

```python
# 只读工具可以不放沙箱
agent = Agent(
    tools=[
        file_editor_sandboxed,
        calculator,  # 无副作用
        # shell 不要加，用 sandbox 内的 shell 替代
    ],
    sandbox=sandbox,
)
```

### 10.5 自定义 sandbox 时要严格限制环境

```python
class MySandbox(PosixShellSandbox):
    async def execute_streaming(self, command, *, timeout=None, cwd=None, env=None, **kwargs):
        # ⚠️ 严格限制 env，不要透传主机的所有环境变量
        safe_env = {"PATH": "/usr/bin:/bin", "LANG": "C"}
        ...
```

---

## 十一、参考

- 源码：
  - `strands/sandbox/base.py`
  - `strands/sandbox/posix_shell.py`
  - `strands/sandbox/docker.py`
  - `strands/sandbox/ssh.py`
  - `strands/sandbox/not_a_sandbox_local_environment.py`
  - `strands/vended_tools/file_editor/__init__.py`
- 官方文档：<https://strandsagents.com/docs/user-guide/concepts/sandbox/>
- 本地验证：`tests/demo_sandbox.py --all`