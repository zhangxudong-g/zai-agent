# 🪟 Context Window 分层堆叠 —— 真实代码 trace

> 系列第五篇:把 Q2 提到的「9 层堆叠」从概念图落到 **真实代码层面**,用 Strands v0.x 源码作为主线 trace,对比 Claude Code 的注入顺序,搞清楚每一层是**谁、在什么时候、怎么进 system_prompt 的**。

---

## 🎯 一句话总结

> **Context Window 的分层堆叠 = 多个 Plugin 在 `BeforeInvocationEvent` 钩子上按 `HookOrder` 优先级依次向 `agent.system_prompt` 追加文本块**,最终在 `stream_messages(...)` 里被 `split_system_prompt()` 拆成 `(str, list[SystemContentBlock])`,再由 `AnthropicModel._format_request()` 拼成 Anthropic API 接受的 `system` 字段。

---

## 🗺️ 一、9 层堆叠的全景回顾(从 Q2 升级版)

> Q2 里那张「9 层图」是概念模型,这里我们把它**对应到真实代码组件**。

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: Anthropic 底层身份 Prompt                          │
│   位置: 模型 API 内部硬编码,客户端不可见                     │
│   Strands: N/A,Anthropic SDK 自己注入                       │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: Strands / Claude Code 行为 Prompt                  │
│   位置: AnthropicModel 内部 / Claude Code CLI 内部          │
│   Strands: `_model_defaults.py` 中的 default system prompt   │
│   Claude Code: `cli.js` 里的 hardcoded system prompt         │
├─────────────────────────────────────────────────────────────┤
│ Layer 3: 环境信息(cwd, OS, git status, 时间戳)              │
│   位置: Strands `Agent.__init__` 时通过 `load_env()` 注入   │
│   Claude Code: `getSystemContext()` 调用链                   │
├─────────────────────────────────────────────────────────────┤
│ Layer 4: CLAUDE.md / AGENTS.md                              │
│   位置: 项目根目录读取,作为 system prompt 块                 │
│   Strands: 无内置,需自己写 Plugin 实现                       │
│   Claude Code: `SessionStart:startup` hook 自动加载          │
├─────────────────────────────────────────────────────────────┤
│ Layer 5: Memory(cross-session 记忆)                         │
│   位置: `~/.claude/memory/` 或 Strands `MemoryManager`      │
│   Strands: `MemoryManager.register_hooks(MessageAddedEvent)`│
│   Claude Code: SessionStorage + 自动注入                      │
├─────────────────────────────────────────────────────────────┤
│ Layer 6: 激活的 Skill 内容                                  │
│   位置: `AgentSkills._on_before_invocation()` 追加 XML       │
│   Strands: 已在 04 篇详述                                     │
├─────────────────────────────────────────────────────────────┤
│ Layer 7: 工具 schema(Bash, Read, Skill, ...)                │
│   位置: Strands `tool_registry.process_tools()`              │
│   格式: JSON Schema,作为 `tools` 参数发给模型                │
├─────────────────────────────────────────────────────────────┤
│ Layer 8: 对话历史 messages[]                                │
│   位置: `agent.messages`,由 conversation_manager 维护        │
│   格式: `[user, assistant, user, ...]`                       │
├─────────────────────────────────────────────────────────────┤
│ Layer 9: 用户最新输入                                       │
│   位置: `agent.invoke_async(prompt)` 的入参                  │
│   格式: 单条 user message                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔬 二、Strands 真实代码 trace

> 跟着用户输入走一遍:从 `Agent(prompt)` 到 HTTP 请求发出的全链路。

### 2.1 入口:`Agent.__call__` / `invoke_async`

```python
# 用户代码
result = agent("帮我审查 src/auth/login.py")
# ↓ 等价于
result = await agent.invoke_async("帮我审查 src/auth/login.py")
```

`Agent.__call__` 调用 `invoke_async`,后者组装好 `invocation_state` 后调用 `_handle_invocation`。在 `_handle_invocation` 内部,**第一件事**就是分发 `BeforeInvocationEvent`:

```python
# strands/agent/agent.py (伪代码)
async def _handle_invocation(self, prompt, ...):
    # 1. 组装 invocation_state + 把 prompt 加到 messages
    self.messages.append({"role": "user", "content": prompt})
    
    # 2. ⭐ 触发 BeforeInvocationEvent —— 所有"想往 system_prompt 加东西"的 hook 都在这一刻运行
    before_event = BeforeInvocationEvent(
        agent=self,
        invocation_state=...,
        messages=self.messages,
    )
    await self.hook_registry.invoke_callbacks_async(before_event)
    
    # 3. 进入 event_loop_cycle 跑模型
    async for evt in event_loop_cycle(self, invocation_state, ...):
        yield evt
```

**关键洞察**:`BeforeInvocationEvent` 是 system prompt 注入的**唯一时间窗口**。所有想在「每次用户输入」时改 system_prompt 的 Plugin 都要 hook 这个事件。

### 2.2 钩子优先级 `HookOrder`

`strands/hooks/registry.py:36-44` 定义了**6 个命名优先级**(数字越小越先执行):

```python
class HookOrder:
    SDK_FIRST: int = -100  # 框架内置 hook(如 _ModelPlugin)
    INTERVENTION_OUTPUT: int = -90  # 输出干预(过滤 / 重写模型输出)
    DEFAULT: int = 0  # 用户默认(Strands AgentSkills 在这层)
    MODEL_ROUTING: int = 50  # 模型路由
    INTERVENTION_INPUT: int = 90  # 输入干预(过滤 / 重写用户输入)
    SDK_LAST: int = 100  # 框架收尾
```

调用顺序:用 `bisect.insort` 按 `order` 升序排序,同优先级按**注册顺序**保留。

**实际分层时,典型的执行顺序**:

```
HookOrder.SDK_FIRST (-100):  框架内置(_ModelPlugin 之类)
        ↓
HookOrder.INTERVENTION_OUTPUT (-90):
        ↓
HookOrder.DEFAULT (0):       ← AgentSkills._on_before_invocation 在这
                              ← MemoryManager.register_hooks() 也在这
                              ← 你的自定义 SkillPlugin 也在这
        ↓
HookOrder.MODEL_ROUTING (50):
        ↓
HookOrder.INTERVENTION_INPUT (90):
        ↓
HookOrder.SDK_LAST (100):
```

### 2.3 Plugin 注册流程:`_PluginRegistry.add_and_init`

`strands/plugins/registry.py:68-99`:

```python
def add_and_init(self, plugin: Plugin) -> None:
    # 1. 调 plugin.init_agent(agent) —— 做 plugin 自己的初始化
    call_init_method(plugin.init_agent, self._agent)

    # 2. 自动注册 plugin 里的 @hook 装饰方法
    self._register_hooks(plugin)

    # 3. 自动注册 plugin 里的 @tool 装饰方法
    self._register_tools(plugin)
```

**所以 Plugin 的标准写法**(以 AgentSkills 为例)就是:

```python
class AgentSkills(Plugin):
    name = "agent_skills"

    @hook  # ← 自动注册为 BeforeInvocationEvent 回调
    async def _on_before_invocation(self, event: BeforeInvocationEvent) -> None:
        # 在这改 system_prompt
        skills_xml = self._generate_skills_xml(event.agent)
        event.agent.system_prompt = ...

    @tool(context=True)  # ← 自动注册为可用工具
    async def skills(self, skill_name: str, tool_context: ToolContext) -> str:
        # 在这返回 tool_result
        ...
```

> 💡 **Plugin 是 system_prompt 分层堆叠的核心抽象**。每加一层就写一个 Plugin,约定 `@hook BeforeInvocationEvent` 时往 `event.agent.system_prompt` 追加文本。

### 2.4 AgentSkills 实际追加 system_prompt

`agent_skills.py:187-238` 的 `_on_before_invocation` 在 `HookOrder.DEFAULT` 触发,它做的事:

```python
@hook
async def _on_before_invocation(self, event: BeforeInvocationEvent) -> None:
    agent = event.agent

    # 1. 首次 invocation 才从 sandbox 加载文件系统 Skill
    if agent not in self._agent_skills:
        await self._load_skill_paths(agent)

    # 2. 取出上次注入的 XML(精确替换,避免堆叠)
    state_data = agent.state.get(self._state_key)
    last_injected_xml = state_data.get("last_injected_xml")

    # 3. 生成新的 XML 块
    skills_xml = self._generate_skills_xml(agent)

    # 4. 追加到 system_prompt —— ⭐ 这里是分层堆叠的实际写入点
    content = agent.system_prompt_content
    if content is not None:
        # 结构化 system_prompt:逐 block 操作,保留 cache_control
        blocks: list[SystemContentBlock] = list(content)
        if last_injected_xml is not None:
            injected_block: SystemContentBlock = {"text": last_injected_xml}
            if injected_block in blocks:
                blocks.remove(injected_block)
        blocks.append({"text": skills_xml})
        self._set_state_field(agent, "last_injected_xml", skills_xml)
        agent.system_prompt = blocks
    else:
        # 字符串 system_prompt:字符串拼接
        current_prompt = agent.system_prompt or ""
        if last_injected_xml in current_prompt:
            current_prompt = current_prompt.replace(last_injected_xml, "")
        new_prompt = f"{current_prompt}\n\n{skills_xml}" if current_prompt else skills_xml
        agent.system_prompt = new_prompt
```

### 2.5 system_prompt 从 Agent 到 HTTP 请求的转换链

整个调用链:

```
Agent.system_prompt (str | list[SystemContentBlock])
    │
    │ ① split_system_prompt()
    ▼
(system_prompt_str, system_prompt_content)
    │
    │ ② AnthropicModel.format_request()
    ▼
{
  "model": "claude-opus-5",
  "system": "...",            ← 单字符串模式
  // 或
  "system": [
    {"type": "text", "text": "...", "cache_control": {"type": "ephemeral"}}
  ],                          ← 结构化模式(支持 cache)
  "messages": [...],
  "tools": [...]
}
```

#### 步骤 ①:`split_system_prompt`

`strands/types/content.py:125-141`:

```python
def split_system_prompt(system_prompt):
    """把统一 system_prompt 拆成两个字段,适配不同模型 provider。"""
    if isinstance(system_prompt, str):
        # 字符串输入:(str, [{"text": str}])
        return system_prompt, [{"text": system_prompt}]
    elif isinstance(system_prompt, list):
        # 列表输入:把所有 text 块拼成 str,blocks 原样保留
        text_parts = [block["text"] for block in system_prompt if "text" in block]
        system_prompt_str = "\n".join(text_parts) if text_parts else None
        return system_prompt_str, system_prompt
    else:
        return None, None
```

**为何要拆成两个字段?**
- `system_prompt_str`:给**老式 provider**(OpenAI 兼容接口)用的纯字符串
- `system_prompt_content`:给**新一代 provider**(Anthropic / Bedrock)用的结构化列表,**支持 cache_control**

#### 步骤 ②:`AnthropicModel._format_system_prompt`

`strands/models/anthropic.py:445-471`:

```python
def _format_system_prompt(self, system_prompt, system_prompt_content):
    """Format the system prompt for the Anthropic API, auto-injecting a cache point at its end."""
    cache_config = self.config.get("cache_config")
    managed_ttl = cache_config.ttl if cache_config else None

    if system_prompt_content is None:
        if not system_prompt:
            return None
        # 单字符串模式:自动在末尾加 cache_control(如果开了 cache)
        return [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": self._format_cache_control(managed_ttl),
            }
        ]

    # 结构化模式:逐 block 转换,保留 cache_control 元数据
    system_prompt_blocks = []
    for block in system_prompt_content:
        if block.get("cachePoint"):
            system_prompt_blocks.append(
                {
                    "type": "text",
                    "text": block["text"],
                    "cache_control": {
                        "type": "ephemeral",
                        "ttl": block["cachePoint"].get("ttl") or managed_ttl,
                    },
                }
            )
        elif "text" in block:
            system_prompt_blocks.append({"type": "text", "text": block["text"]})
    return system_prompt_blocks
```

### 2.6 完整调用链 ASCII 图

```
用户: agent("帮我审查 login.py")
  │
  ▼
Agent.invoke_async(prompt)
  │  self.messages.append({role:user, content:prompt})
  │
  ▼
hook_registry.invoke_callbacks_async(BeforeInvocationEvent)
  │
  ├─ [HookOrder.SDK_FIRST] _ModelPlugin
  │     └─ 记录 invocation state
  │
  ├─ [HookOrder.DEFAULT] AgentSkills._on_before_invocation
  │     └─ 追加 <available_skills>...</available_skills> 到 system_prompt
  │
  ├─ [HookOrder.DEFAULT] MemoryManager._on_message_added
  │     └─ 把"用户偏好"插到 system_prompt(基于历史 messages)
  │
  ├─ [HookOrder.INTERVENTION_INPUT] 用户自定义输入过滤器
  │     └─ 检查 / 改写 prompt
  │
  ▼
event_loop_cycle()
  │
  ├─ _handle_model_execution() → _make_invoke_model_terminal()
  │
  ├─ stream_messages(model, system_prompt_str, messages, tool_specs,
  │                  system_prompt_content=...)
  │
  ▼
AnthropicModel._format_request()
  │  self._format_system_prompt(system_prompt, system_prompt_content)
  │  self._format_request_messages(messages, ...)
  │  self._format_tools(tool_specs, ...)
  │
  ▼
{
  "model": "claude-opus-5",
  "system": [
    {"type": "text", "text": "你是一个 Python 助手", "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "<available_skills>...</available_skills>", "cache_control": {"type": "ephemeral"}},
  ],
  "messages": [
    {"role": "user", "content": "帮我审查 login.py"}
  ],
  "tools": [
    {"name": "skills", "description": "...", "input_schema": {...}},
    {"name": "bash",   "description": "...", "input_schema": {...}},
    ...
  ]
}
  │
  ▼
HTTP POST https://api.anthropic.com/v1/messages
```

---

## 🧩 三、9 层堆叠 vs Strands 实际能配的层

> 不是所有 9 层都在 Strands 里"开箱即用",有的是框架提供、有的是约定俗成、有的是要自己写 Plugin。

| Layer | 来源 | Strands 是否内置 | 怎么实现 |
|---|---|---|---|
| 1. 底层身份 Prompt | 模型 API 内部 | N/A | 不可控 |
| 2. 行为 Prompt | `AnthropicModel._defaults` | ⚠️ 部分 | 通过 `system_prompt="..."` 入参传入 |
| 3. 环境信息 | 框架运行时注入 | ❌ 需自实现 | 写 Plugin 读 `os.environ` / `cwd` / `git status` |
| 4. CLAUDE.md / AGENTS.md | 项目级指令 | ❌ 需自实现 | 写 Plugin 读 `AGENTS.md`,加进 system_prompt |
| 5. Memory | 跨会话记忆 | ✅ 内置 | `MemoryManager` Plugin,自动注册工具 |
| 6. 激活的 Skill | 一次性加载的领域知识 | ✅ 内置 | `AgentSkills` Plugin |
| 7. 工具 schema | `@tool` 装饰器 + tool_registry | ✅ 内置 | 写 `@tool def my_tool(): ...` |
| 8. 对话历史 | `conversation_manager` | ✅ 内置 | 由 `SlidingWindowConversationManager` 等维护 |
| 9. 用户输入 | `invoke_async(prompt)` | ✅ 内置 | 框架自动追加 |

### 3.1 自己实现"环境信息"Layer

```python
from strands import Agent
from strands.plugins import Plugin
from strands.hooks.events import BeforeInvocationEvent
from strands.hooks.registry import HookOrder
from hooks import hook
import os, subprocess, datetime


class EnvContextPlugin(Plugin):
    name = "env_context"

    @hook
    def inject_env(self, event: BeforeInvocationEvent) -> None:
        agent = event.agent

        # 构造环境信息块
        env_block = f"""<environment>
cwd: {os.getcwd()}
os: {os.uname().sysname}
git_branch: {self._get_git_branch()}
time: {datetime.datetime.now().isoformat()}
</environment>"""

        # 追加到 system_prompt
        if isinstance(agent.system_prompt, list):
            agent.system_prompt = agent.system_prompt + [{"text": env_block}]
        else:
            agent.system_prompt = (agent.system_prompt or "") + "\n\n" + env_block

    @staticmethod
    def _get_git_branch() -> str:
        try:
            return (
                subprocess.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL
                )
                .decode()
                .strip()
            )
        except Exception:
            return "N/A"


agent = Agent(
    system_prompt="你是代码审查助手",
    plugins=[
        AgentSkills(skills=[...]),
        EnvContextPlugin(),  # ← Layer 3
    ],
)
```

### 3.2 自己实现"AGENTS.md"Layer

```python
class AgentsMdPlugin(Plugin):
    name = "agents_md"
    
    def __init__(self, path: str = "AGENTS.md"):
        self.path = Path(path)
    
    @hook
    def load_agents_md(self, event: BeforeInvocationEvent) -> None:
        if not self.path.is_file():
            return
        content = self.path.read_text(encoding="utf-8")
        block = f"<project_rules>\n{content}\n</project_rules>"
        
        if isinstance(event.agent.system_prompt, list):
            event.agent.system_prompt = event.agent.system_prompt + [{"text": block}]
        else:
            event.agent.system_prompt = (event.agent.system_prompt or "") + "\n\n" + block
```

### 3.3 自己实现"Memory"Layer

> Strands 已有 MemoryManager,但它的实现是**"用 search/add 工具主动管理"**,而不是"自动追加到 system_prompt"。如果你想要的是后者(类似 Claude Code 那样自动从 memory 加载偏好),需要自己写:

```python
import json
from pathlib import Path


class PersistentMemoryPlugin(Plugin):
    name = "persistent_memory"

    def __init__(self, memory_file: str = "~/.my_agent_memory.json"):
        self.path = Path(memory_file).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load_memory(self) -> dict:
        if self.path.is_file():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {"user_preferences": [], "past_actions": []}

    @hook
    def inject_memory(self, event: BeforeInvocationEvent) -> None:
        mem = self._load_memory()
        block = f"<memory>\n"
        block += "User preferences:\n"
        for pref in mem["user_preferences"][-10:]:  # 只取最近 10 条
            block += f"- {pref}\n"
        block += "\nPast actions:\n"
        for act in mem["past_actions"][-5:]:
            block += f"- {act}\n"
        block += "</memory>"

        prompt = event.agent.system_prompt or ""
        event.agent.system_prompt = prompt + ("\n\n" if prompt else "") + block
```

### 3.4 完整组合示例

```python
agent = Agent(
    system_prompt=[
        # Layer 2:行为 Prompt(显式声明,放在最前)
        {"type": "text", "text": "你是 Python 代码审查助手"},
        # Layer 4:项目规则(AGENTS.md)
        # (由 AgentsMdPlugin 自动追加)
    ],
    plugins=[
        EnvContextPlugin(),  # Layer 3:环境信息
        AgentsMdPlugin("AGENTS.md"),  # Layer 4:项目规则
        PersistentMemoryPlugin(),  # Layer 5:跨会话记忆
        AgentSkills(skills=[...]),  # Layer 6:Skill 元数据
        # Layer 7:工具由 @tool 装饰器提供
    ],
)
# 一次 invocation 后,system_prompt 实际内容大致是:
# ┌── Layer 2:你是 Python 代码审查助手 ─────────────────┐
# ├── Layer 3:<environment>...</environment>            │
# ├── Layer 4:<project_rules>...</project_rules>        │
# ├── Layer 5:<memory>...</memory>                      │
# └── Layer 6:<available_skills>...</available_skills>  │
```

---

## 🆚 四、对比:Claude Code 的分层堆叠

| 维度 | Strands | Claude Code |
|---|---|---|
| **入口** | `BeforeInvocationEvent` hook | `SessionStart:startup` hook + `UserPromptSubmit` hook |
| **优先级** | `HookOrder` 6 个级别 | 不显式,按 hook 注册顺序 |
| **Plugin 抽象** | `Plugin` 基类 + `@hook` + `@tool` | 没 Plugin 概念,直接在 `cli.js` 里挂 hook 回调 |
| **结构化 system_prompt** | ✅ `list[SystemContentBlock]`,支持 cache_control | ✅ `system` 字段是数组,显式 cache |
| **AGENTS.md / CLAUDE.md** | ❌ 需自实现 | ✅ 内置 + 自动加载 |
| **Memory** | ⚠️ MemoryManager 是工具式 | ✅ 自动从 `~/.claude/memory/` 加载 |
| **Skills** | ✅ `AgentSkills` Plugin | ✅ SKILL.md + `Skill` tool |
| **环境信息** | ❌ 需自实现 | ✅ 内置 |
| **可观测性** | OTel tracing + hook event 流 | 内部日志为主 |

### 4.1 Claude Code 的注入顺序(社区 reverse-engineered)

```
1. Anthropic 底层身份     (client-side 不可见)
2. Claude Code 行为       (cli.js 硬编码)
3. 环境信息               (getSystemContext())
4. CLAUDE.md              (项目根目录)
5. Memory                 (~/.claude/memory/*.md)
6. Skills                 (~/.claude/skills/<name>/SKILL.md)
7. 工具 schema            (内置 tools + 用户 MCP)
8. 对话历史               (SessionStorage)
9. 用户输入               (UserPromptSubmit hook 触发)
```

**关键差异**:Claude Code 一次启动时就把 1–7 层**全部加载**,存在内存里;**只有 Memory 和 Skills 是动态的**(每次 UserPromptSubmit 重新加载)。

---

## 🧮 五、9 层总 token 成本估算

> 假设一个典型 Python 项目 agent,每次 invocation 的平均消耗:

| Layer | 内容 | Token(估) | 是否可缓存 |
|---|---|---|---|
| 1 | Anthropic 身份 | 不可见 | ✅ 永远命中 |
| 2 | 行为 Prompt | 500–1500 | ✅ 高频命中 |
| 3 | 环境信息 | 100–300 | ❌ 每次变 |
| 4 | AGENTS.md | 1000–3000 | ✅ 极少变 |
| 5 | Memory | 500–2000 | ⚠️ 偶尔变 |
| 6 | Skill 元数据 | 200–1000 (取决于 Skill 数) | ✅ Skill 列表不变就命中 |
| 7 | 工具 schema | 2000–8000 (取决于工具数) | ✅ 工具定义不变就命中 |
| 8 | 对话历史 | 5000–50000 (随对话增长) | ⚠️ 滑窗 / 摘要后变 |
| 9 | 用户输入 | 50–500 | ❌ 每次都新 |
| **合计** | — | **~10K–66K** | — |

**没有 Prompt Caching 时**:每次 invocation 都按这个总量计费。

**开启 Prompt Caching 后**(Anthropic 5min TTL,1.25x write / 0.1x read):

- Layer 1, 2, 4, 6, 7:命中率 90%+,成本降为 0.1x
- Layer 5, 8:命中率 30–50%,成本约 0.5x
- Layer 3, 9:命中率 0%,按原价

**实测节省**:典型 invocation 的 input token 成本可降 **50–70%**。

---

## 🚨 六、常见陷阱

### 6.1 顺序错乱:Skill 内容出现在 CLAUDE.md 之前

> Hook 在 `HookOrder.DEFAULT` 都按注册顺序,如果你先注册 Skill Plugin 再注册 AgentsMdPlugin,Skill 会"覆盖"AgentsMd 的语义优先级。

**修复**:用 `order` 参数指定优先级:

```python
agent.hook_registry.add_callback(
    BeforeInvocationEvent,
    agents_md_callback,
    order=HookOrder.INTERVENTION_INPUT,  # 90,放最后
)
agent.hook_registry.add_callback(
    BeforeInvocationEvent,
    skill_callback,
    order=HookOrder.DEFAULT,  # 0,放前面
)
```

### 6.2 多 Plugin 重复注入

> 多个 Plugin 都往 system_prompt 加同一类内容(如都加 `<memory>`),会重复。

**修复**:每个 Plugin 用唯一的 marker(如 `<skill_block uuid="...">`),每次注入前先 remove 旧 block。AgentSkills 的 `last_injected_xml` 就是这个套路。

### 6.3 结构化 system_prompt 误用

> 用户传 `system_prompt="str"`,但 Plugin 把它当 list 改写,会变成 `str + [{"text": ...}]` 的奇怪类型。

**修复**:AgentSkills 的 `_on_before_invocation` 显式分两条路径处理,这是**正确范式**:

```python
if content is not None:  # list 路径
    blocks = list(content)
    blocks.append({"text": new_block})
    agent.system_prompt = blocks
else:  # str 路径
    agent.system_prompt = (current_prompt or "") + "\n\n" + new_block
```

### 6.4 结构化 system_prompt 丢失 cache_control

> Plugin 用 `agent.system_prompt = agent.system_prompt + [{"text": ...}]` 追加,**但中间那步 Python 列表 concat 会让 cache_control 失效**(Anthropic API 校验更严)。

**修复**:用 `agent.system_prompt = list(agent.system_prompt) + [...]`,**不修改原 block**,只追加新 block(新 block 自己打 cache_control)。

### 6.5 BeforeInvocationEvent 中触发 LLM 调用

> 想在「追加 Skill XML 之前先问问 LLM 这个任务适不适合用 Skill」,用 `BeforeInvocationEvent` 调 `agent.model.stream(...)`,**会死锁** —— event loop 还没启动,model 正在等你触发 BeforeInvocation。

**修复**:用 `BeforeModelCallEvent`(在 model 真正被调之前),**或**直接把所有决策交给模型自己(这是 Claude Code 的做法)。

---

## 🧪 七、调试工具箱

### 7.1 打印完整 system_prompt

```python
import json

# 字符串模式
print(agent.system_prompt)

# 结构化模式(包含 cache_control 等元数据)
print(json.dumps(agent.system_prompt, ensure_ascii=False, indent=2))
```

### 7.2 拦截所有 hook 事件

```python
from strands.hooks.events import BeforeInvocationEvent


@agent.hooks.before_invocation
def trace_invoke(event: BeforeInvocationEvent):
    print(f"[BeforeInvocationEvent] system_prompt chars: {len(event.agent.system_prompt or '')}")
    print(f"  messages count: {len(event.messages or [])}")


# 触发
agent("hello")
# 输出:
# [BeforeInvocationEvent] system_prompt chars: 4523
#   messages count: 1
```

### 7.3 拦截 HTTP 请求(看最终 payload)

```python
import anthropic

original_create = anthropic.Anthropic().messages.create


def traced_create(*args, **kwargs):
    print("=== HTTP payload ===")
    print(f"model: {kwargs.get('model')}")
    print(f"system blocks: {len(kwargs.get('system', []))}")
    for i, block in enumerate(kwargs.get("system", [])):
        text_preview = block.get("text", "")[:80] if isinstance(block, dict) else str(block)[:80]
        print(
            f"  [{i}] {block.get('type', '?')} | {block.get('cache_control', {})} | {text_preview}..."
        )
    print(f"messages: {len(kwargs.get('messages', []))}")
    print(f"tools: {len(kwargs.get('tools', []))}")
    return original_create(*args, **kwargs)


anthropic.Anthropic().messages.create = traced_create
```

### 7.4 注入前后的 diff

```python
before_prompt = json.dumps(agent.system_prompt, ensure_ascii=False)

# 手动触发 hook
event = BeforeInvocationEvent(agent=agent, messages=agent.messages)
await agent.hook_registry.invoke_callbacks_async(event)

after_prompt = json.dumps(agent.system_prompt, ensure_ascii=False)

# 用 difflib 看新增了什么
import difflib

for line in difflib.unified_diff(
    before_prompt.splitlines(), after_prompt.splitlines(), lineterm=""
):
    print(line)
```

---

## 🧩 关键 Takeaway

> **Context Window 分层堆叠 = 多个 Plugin 在 `BeforeInvocationEvent` 上按优先级追加 system_prompt 块,最终在 `stream_messages` 里被 `split_system_prompt` 拆字段,再由 provider 的 `_format_request` 转成 HTTP payload。**
>
> 1. **时间窗口唯一**:`BeforeInvocationEvent` 是改 system_prompt 的唯一时间点
> 2. **顺序由 HookOrder 控制**:`SDK_FIRST → DEFAULT → SDK_LAST`,相同优先级按注册顺序
> 3. **支持结构化 system_prompt**:`list[SystemContentBlock]` 让 Prompt Caching 命中
> 4. **每层是独立 Plugin**:可插拔、可观测、可单测
> 5. **调试靠拦截**:hook event 流 + HTTP payload 拦截

---

## 🔗 关联文档

- [01_LLM如何理解Skill.md](./01_LLM如何理解Skill.md) — Skill 三步循环
- [02_Prompt注入机制.md](./02_Prompt注入机制.md) — 9 层堆叠的概念图
- [03_Skill_Schema设计最佳实践.md](./03_Skill_Schema设计最佳实践.md) — Schema 怎么写
- [04_Meta_Prompt两级调用工程实现.md](./04_Meta_Prompt两级调用工程实现.md) — 两级调用的 event loop
- [README.md](./README.md) — 系列索引
