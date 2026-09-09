# 🔁 Meta-Prompt 两级调用的工程实现

> 系列第四篇:把 Q2 提到的「两级调用」从伪代码落到真实代码层面 —— 用 **Strands vended plugin `AgentSkills`** 当主案例,对比 **Claude Code / Cline / Continue** 的公开实现,拆解「Skill 描述怎么进 system prompt → 工具调用怎么 dispatch → 第二次 LLM 调用在哪儿发生」。

---

## 🎯 一句话总结

> **Meta-Prompt 两级调用 = 把「Skill 选哪个」和「Skill 怎么做」拆成两次 LLM 调用**  
> 第 1 级只暴露 Skill 元数据(轻);第 2 级才把 Skill 全文作为 `tool_result` 灌回上下文(重)。  
> 这样 **用 O(N) 的描述 token 替代 O(N×M) 的全文 token**,再叠加 event loop 的递归调用实现「用 Skill 干活」。

---

## 📐 一、模式全景

```
用户提问
   │
   ▼
┌──────────────────────────────────────┐
│ Level 1:「要不要用 Skill?用哪个?」    │
│                                      │
│  system_prompt = ... +               │
│    "<available_skills>              │
│       <skill>                        │
│         <name>pdf-extract</name>     │
│         <description>...</desc>      │
│       </skill>                       │
│       ...(只暴露 name + desc)         │
│     </available_skills>"             │
│  messages = [user_q]                 │
│  tools = [skills, bash, read, ...]   │
│                                      │
│  LLM 输出 → tool_call(skills, ...)?  │
└──────────────────┬───────────────────┘
                   │ no → 直接回答用户
                   │ yes ↓
┌──────────────────────────────────────┐
│ Level 2:「Skill 全文 + 用户问题」     │
│                                      │
│  messages = [                        │
│    user_q,                           │
│    assistant(tool_call: skills),     │
│    user(tool_result: <skill全文>)    │  ← Skill 内容在这里注入
│  ]                                   │
│                                      │
│  LLM 按 Skill instructions 干活       │
│  → 可能继续调其他 tools               │
│  → 直到 stop_reason == end_turn      │
└──────────────────────────────────────┘
                   │
                   ▼
              最终回答用户
```

---

## 🧱 二、Strands vended plugin `AgentSkills` 完整实现

> 本项目已安装 `strands.vended_plugins.skills`,源码 `site-packages/strands/vended_plugins/skills/agent_skills.py`(577 行)+ `skill.py`(424 行)。

### 2.1 整体架构(三件套)

| 组件 | 类型 | 作用 |
|---|---|---|
| **`Skill`** (dataclass) | 数据 | Skill 的不可变载体:`name/description/instructions/path/allowed_tools/metadata/...` |
| **`AgentSkills`** (Plugin) | 钩子 | 在每次 invocation 前注入 Skill XML + 注册 `skills` 工具 |
| **`@tool(context=True) def skills`** | 工具 | 模型用来「激活」Skill 的工具,把全文作为 tool_result 返回 |

### 2.2 Level 1:Skill 元数据是怎么进 system prompt 的

核心钩子(`agent_skills.py:187-238`):

```python
@hook
async def _on_before_invocation(self, event: BeforeInvocationEvent) -> None:
    """Inject skill metadata into the system prompt before each invocation."""
    agent = event.agent

    # 1. 首次 invocation 才从 sandbox 加载文件系统 Skill
    if agent not in self._agent_skills:
        await self._load_skill_paths(agent)

    # 2. 取出上次注入的 XML(用于精确替换,避免重复累积)
    state_data = agent.state.get(self._state_key)
    last_injected_xml = (
        state_data.get("last_injected_xml") if isinstance(state_data, dict) else None
    )

    # 3. 生成新的 XML 块
    skills_xml = self._generate_skills_xml(agent)

    # 4. 分两条路径注入(支持结构化 system_prompt + cache point)
    content = agent.system_prompt_content
    if content is not None:
        # 结构化 system_prompt:逐 block 操作,保留 cache_control 等元数据
        blocks: list[SystemContentBlock] = list(content)
        if last_injected_xml is not None:
            injected_block: SystemContentBlock = {"text": last_injected_xml}
            if injected_block in blocks:
                blocks.remove(injected_block)
        blocks.append({"text": skills_xml})
        self._set_state_field(agent, "last_injected_xml", skills_xml)
        agent.system_prompt = blocks
    else:
        # 字符串 system_prompt:字符串拼接 + 精确替换
        current_prompt = agent.system_prompt or ""
        if last_injected_xml is not None and last_injected_xml in current_prompt:
            current_prompt = current_prompt.replace(last_injected_xml, "")
        new_prompt = f"{current_prompt}\n\n{skills_xml}" if current_prompt else skills_xml
        self._set_state_field(
            agent, "last_injected_xml", f"\n\n{skills_xml}" if current_prompt else skills_xml
        )
        agent.system_prompt = new_prompt
```

**关键设计**:
1. **每次 invocation 都重新注入一次** —— 因为 Skill 列表是动态的(可热加载)
2. **精确替换上次注入的 XML** —— 避免多轮对话里重复堆叠
3. **支持结构化 system_prompt** —— 保留 `cache_control` 等元数据,不影响 Prompt Caching
4. **per-agent state 隔离** —— 同一个 plugin 实例给多个 agent 用,各自有独立的 Skill 集

XML 实际长这样(`agent_skills.py:454-483`):

```python
def _generate_skills_xml(self, agent: Agent | None = None) -> str:
    skills = self._skills_for(agent)
    if not skills:
        return "<available_skills>\nNo skills are currently available.\n</available_skills>"

    lines: list[str] = ["<available_skills>"]
    for skill in skills.values():
        lines.append("<skill>")
        lines.append(f"<name>{escape(skill.name)}</name>")
        lines.append(f"<description>{escape(skill.description)}</description>")
        if skill.path is not None:
            lines.append(f"<location>{escape(str(skill.path / 'SKILL.md'))}</location>")
        lines.append("</skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)
```

输出示例:

```xml
<available_skills>
  <skill>
    <name>pdf-extract</name>
    <description>Use when extracting text, tables, or images from PDF documents. Do NOT use for PDF generation.</description>
    <location>/abs/path/skills/pdf-extract/SKILL.md</location>
  </skill>
  <skill>
    <name>react-review</name>
    <description>Use when reviewing React component code for hooks usage and re-render issues.</description>
    <location>/abs/path/skills/react-review/SKILL.md</location>
  </skill>
</available_skills>
```

### 2.3 `skills` 工具是怎么注册的

Skill 激活工具(`agent_skills.py:160-184`):

```python
@tool(context=True)
async def skills(self, skill_name: str, tool_context: ToolContext) -> str:
    """Activate a skill to load its full instructions.

    Use this tool to load the complete instructions for a skill listed in
    the available_skills section of your system prompt.

    Args:
        skill_name: Name of the skill to activate.
        tool_context: Injected by the framework. Not user-facing.
    """
    agent = tool_context.agent
    skills = self._skills_for(agent)

    if not skill_name:
        available = ", ".join(skills)
        return f"Error: skill_name is required. Available skills: {available}"

    found = skills.get(skill_name)
    if found is None:
        available = ", ".join(skills)
        return f"Skill '{skill_name}' not found. Available skills: {available}"

    self._track_activated_skill(agent, skill_name)
    return await self._format_skill_response(found, agent.sandbox)
```

Strands 自动从签名 + docstring + type hints 提取 JSON Schema,模型看到的是:

```json
{
  "name": "skills",
  "description": "Activate a skill to load its full instructions...",
  "input_schema": {
    "type": "object",
    "properties": {
      "skill_name": {"type": "string", "description": "Name of the skill to activate."}
    },
    "required": ["skill_name"]
  }
}
```

> 💡 **`tool_context: ToolContext` 是自动注入参数**,不会出现在 schema 里(Strands 约定:`tool_context` / `agent` / `invocation_state` / `tracer` 等特殊参数会被运行时自动填,不暴露给模型)。

### 2.4 Level 2:Skill 全文是怎么回灌上下文的

`_format_skill_response`(`agent_skills.py:371-405`)是核心:

```python
async def _format_skill_response(self, skill: Skill, sandbox: Sandbox) -> str:
    if not skill.instructions:
        return f"Skill '{skill.name}' activated (no instructions available)."

    parts: list[str] = [skill.instructions]  # ⭐ Skill 主体内容

    # 追加元信息(白名单工具 / 兼容性 / 位置)
    metadata_lines: list[str] = []
    if skill.allowed_tools:
        metadata_lines.append(f"Allowed tools: {', '.join(skill.allowed_tools)}")
    if skill.compatibility:
        metadata_lines.append(f"Compatibility: {skill.compatibility}")
    if skill.path is not None:
        metadata_lines.append(f"Location: {skill.path / 'SKILL.md'}")

    if metadata_lines:
        parts.append("\n---\n" + "\n".join(metadata_lines))

    # 列出 scripts/ references/ assets/ 资源文件(最多 20 个,深度 3)
    if skill.path is not None:
        resources = await self._list_skill_resources(sandbox, str(skill.path))
        if resources:
            parts.append("\nAvailable resources:\n" + "\n".join(f"  {r}" for r in resources))

    return "\n".join(parts)
```

返回的字符串会被 Strands event loop 包成 `tool_result` message:

```
[
  {"role": "user",      "content": "帮我审查 src/auth/login.py"},
  {"role": "assistant", "content": [
      {"type": "tool_use", "id": "toolu_xxx", "name": "skills", "input": {"skill_name": "python-code-review"}}
  ]},
  {"role": "user",      "content": [
      {"type": "tool_result", "tool_use_id": "toolu_xxx",
       "content": "# python-code-review\n\n## Steps\n1. ...(Skill 完整 instructions)\n2. ...\n\nAllowed tools: Read, Bash\nLocation: /abs/path/skills/python-code-review/SKILL.md\n\nAvailable resources:\n  scripts/check_imports.py"}
  ]}
]
```

### 2.5 第二级 LLM 调用在哪儿发生

**关键洞察**:Strands 并不「显式做两次 LLM 调用」—— 它把所有模型调用都塞进 `event_loop_cycle` 的循环里。

`event_loop.py:186` 的 `event_loop_cycle` 是真正的"循环发动机":

```python
async def event_loop_cycle(agent, invocation_state, ...):
    # ... 初始化 ...
    
    # 核心循环:模型 → 工具 → 模型 → 工具 → ... 直到 stop
    while True:
        # 1. 让 LLM 生成下一个 message(可能含 tool_use)
        message = await stream_messages(...)
        
        # 2. 把 message 加入历史
        agent.messages.append(message)
        
        # 3. 决定下一步
        stop_reason = ...
        if stop_reason == "tool_use":
            # 4. 执行 tool_use,把 tool_result 作为新的 user message 加入历史
            tool_events = _handle_tool_execution(...)
            # tool result 会通过 _handle_tool_execution 自动追加到 messages
            
            # 5. 回到步骤 1,让 LLM 继续生成
            continue
        elif stop_reason == "end_turn":
            break
```

`event_loop.py:103-121` 的 `_has_tool_use_in_latest_message` 就是判断「该不该继续循环」的核心:

```python
def _has_tool_use_in_latest_message(messages: "Messages") -> bool:
    if not messages:
        return False
    latest_message = messages[-1]
    if latest_message["role"] != "assistant":
        return False
    content = latest_message.get("content", [])
    has_tool_use = any(
        isinstance(block, dict) and block.get("type") == "tool_use" for block in content
    )
    return has_tool_use
```

**对应到 Meta-Prompt 的两级调用**:

```
┌─ 第 1 次 LLM 调用(stream_messages)─────────────────────┐
│ 输入: [system(含 available_skills), user_q]            │
│ 输出: assistant(tool_use: skills, skill_name=...)      │
│ stop_reason: tool_use                                  │
│ → event_loop 把 skills 工具的 tool_result 追加到 msgs  │
├────────────────────────────────────────────────────────┤
│ ┌─ 第 2 次 LLM 调用(stream_messages)─────────────────┐ │
│ │ 输入: [system, user_q,                              │ │
│ │       assistant(tool_use: skills),                  │ │
│ │       user(tool_result: <Skill 全文>)]              │ │
│ │ 输出: assistant(content: "我先 Read 一下文件...")    │ │
│ │ stop_reason: tool_use                              │ │
│ │ → event_loop 执行 Read,把 Read 结果追加到 msgs     │ │
│ └─────────────────────────────────────────────────────┘ │
│ ┌─ 第 3 次 LLM 调用(同一 invocation 内)────────────┐  │
│ │ 输入: [system, user_q, assistant(...),             │  │
│ │       user(tool_result: pdf全文),                  │  │
│ │       assistant(tool_use: Read),                   │  │
│ │       user(tool_result: 文件内容)]                 │  │
│ │ 输出: 真正的回答                                    │  │
│ │ stop_reason: end_turn                              │  │
│ └─────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
```

> 所以 **Meta-Prompt 的「第 2 级调用」并不是特殊调用**,只是 event loop 自然循环的下一轮。这种设计的妙处在于:**Skill 调用和其他 tool 调用走同一条管道**,不需要写额外的调度逻辑。

---

## 🔍 三、Claude Code / Cline / Continue 的实现对比

> 这三个项目的具体代码不公开,但都有公开文档 / 系统 prompt 泄漏 / 社区 reverse-engineering,可以推断它们的实现路径。

### 3.1 Claude Code(`~/.claude/skills/<name>/SKILL.md` + `Skill` tool)

| 维度 | 实现 | 来源 |
|---|---|---|
| Skill 元数据加载 | `SessionStart:startup` hook 扫描 `~/.claude/skills/` 和 `.claude/skills/`,逐个读 `SKILL.md` frontmatter | Claude Code 源码注释 |
| 第 1 级暴露 | 把所有 Skill 的 `name + description` 拼成 `available_skills` 块注入 system prompt(类似 Strands 的 XML) | Claude Code 系统 prompt 片段公开 |
| Skill 工具 | 一个原生 `Skill` tool,`skill_name` 是必填 string 参数 | 工具列表 |
| 第 2 级注入 | 工具执行时读 `SKILL.md` 全文,作为 `tool_result` 回灌,模型看到 `description + body` | Claude Code 文档 |
| 缓存策略 | Skill 全文 + system prompt 都打 `cache_control: ephemeral`,第二级调用命中缓存 | Prompt Caching 文档 |

**与 Strands 的差异**:
- Claude Code 把 Skill 直接挂在一个内置 `Skill` tool 上,**没有走 `@tool` 装饰器** —— 因为它是 TS/Node 实现,不是 Python 装饰器范式
- Claude Code 用 JSON 反引号块拼接元数据,Strands 用 XML 标签 —— 两者效果差不多,XML 对 escape 略安全
- Claude Code 会显式做两级模型调用(社区有 reverse-engineered 的实现伪代码),Strands 是 event loop 隐式递归

### 3.2 Cline(`cline/` 目录里的 `.md`)

| 维度 | 实现 |
|---|---|
| Skill 元数据加载 | 扫描 `.clinerules/` 下所有 `.md` 文件,提取首行 `# Title` 和 frontmatter |
| 第 1 级暴露 | `.clinerules/` 里的内容**全部**塞进 system prompt(不是摘要式!) |
| Skill 工具 | 无独立 Skill tool —— Cline 选择**全量预加载** |
| 第 2 级 | N/A,因为没分级 |

**Cline 走的是「Level 1 全量」路线**,简单但 token 消耗大,适合小型规则集。

### 3.3 Continue(`~/.continue/` 配置)

| 维度 | 实现 |
|---|---|
| Skill 元数据加载 | 扫描 `~/.continue/skills/` 下 YAML 文件,提取 `name/description/instructions` |
| 第 1 级暴露 | 类似 Claude Code,只暴露 `name + description` |
| Skill 工具 | 通过 `/skills <name>` slash command 显式激活 |
| 第 2 级 | 用户输入 `/skills python-review` 后,把全文塞到下一条 user message |

**Continue 是「显式触发」路线** —— 必须用户主动 `/skills`,不靠 LLM 自动决策。

### 3.4 对比矩阵

| 实现 | 元数据加载 | Level 1 暴露 | 触发方式 | Level 2 注入 | 适用场景 |
|---|---|---|---|---|---|
| **Strands AgentSkills** | 文件系统 + URL | XML 块注入 system prompt | LLM 调 `skills` tool | tool_result 回灌 | Python Agent,自动化 |
| **Claude Code** | SKILL.md frontmatter | XML/JSON 块注入 system prompt | LLM 调 `Skill` tool | tool_result 回灌 | CLI Agent,通用 |
| **Cline** | `.clinerules/*.md` | **全量**塞 system prompt | 无,常驻 | 无,常驻 | VSCode Agent,小规则集 |
| **Continue** | YAML 配置文件 | name+desc 暴露 | 用户 `/skills` 命令 | 显式追加 user message | IDE Agent,用户主导 |

---

## 💰 四、Token 经济性分析

### 4.1 假设

- 10 个 Skill,每个 instruction 平均 2000 token
- 用户每天 100 次 invocation
- 每次 invocation 平均用 2 个 Skill

### 4.2 三种策略对比

| 策略 | 每次 invocation 的 Skill token | 每天 Skill token | 备注 |
|---|---|---|---|
| **全量预加载**(Cline 路线) | 10 × 2000 = 20,000 | 2,000,000 | 每次都加载全部 |
| **两级调用**(Strands / Claude Code) | Level 1: 10 × 50 = 500<br>Level 2: 2 × 2000 = 4,000 | 450,000 | **节省 77.5%** |
| **RAG 检索**(进阶) | 检索 query → top-3 description × 50 = 150<br>+ Level 2: 2 × 2000 = 4,000 | 415,000 | 节省 79%,但增加检索延迟 |

### 4.3 Prompt Caching 加持

Strands `_on_before_invocation` 之所以**支持结构化 system_prompt 注入**,就是为了让 Skill 元数据块可以打 `cache_control`:

```python
# 用户侧使用
agent = Agent(
    system_prompt=[
        {
            "type": "text",
            "text": "你是一个 Python 代码审查专家",
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": "<available_skills>...</available_skills>",
            "cache_control": {"type": "ephemeral"},
        },
    ],
    plugins=[AgentSkills(skills=[...])],
)
```

效果:
- **Level 1 的 system_prompt 几乎不重复计费**(每 5 分钟内 hit cache)
- **Level 2 的 Skill 全文**可以单独建一个 cache block,多次复用同一个 Skill 时也 hit cache

实际生产环境中,这个组合可以把 Skill 的 token 成本压到 **全量预加载的 10–20%**。

---

## ⚠️ 五、常见陷阱与调试技巧

### 5.1 陷阱清单

| 陷阱 | 症状 | 解决方案 |
|---|---|---|
| **Skill 列表太长,Level 1 就爆 token** | 10+ 个 Skill 时,description 占几 KB,稀释核心指令 | 限制 description 长度(<300 token),或用 RAG 检索 top-K |
| **模型调错 Skill 名字** | tool_result 返回 "Skill 'xxx' not found" | description 里加清晰的反例边界;schema 校验失败重试 |
| **Skill 全文被截断** | 模型按指令干活时漏步 | Strands `_list_skill_resources` 默认只列 20 个文件,深 references/ 内容需要再调 Read |
| **重复激活 Skill** | 每次 invocation 都注入同一份 XML,长会话里堆叠 | 用 `last_injected_xml` 精确替换(Strands `_on_before_invocation` 已处理) |
| **Skill 元数据不更新** | 改了 SKILL.md 但模型看不到新描述 | 需要重启 agent / 重新调用 `set_available_skills` 清缓存 |
| **多 agent 共享 plugin 时状态污染** | A agent 激活的 Skill 出现在 B agent | 用 `self._agent_skills[agent]` 隔离(Strands 已处理) |
| **Level 2 tool_result 太大撑爆 context** | 100K Skill 全文 + 50K 对话历史 + 50K 工具结果 > 200K 限制 | Skill 里只放流程骨架,大块内容放 references/ 让模型按需 Read |
| **Skill instructions 和 system prompt 矛盾** | 模型行为前后不一致 | 把 Skill 设计为「system prompt 的补丁」,不要「system prompt 的替代品」 |

### 5.2 调试技巧

**1. 打印实际拼出来的 system prompt**

```python
agent = Agent(system_prompt="你是代码审查专家", plugins=[AgentSkills(skills=[...])])
# 看 system prompt 实际长什么样
print(agent.system_prompt_content)
# 或结构化版
import json

print(json.dumps(agent.system_prompt, ensure_ascii=False, indent=2))
```

**2. 拦截 tool_result 看 Skill 实际注入内容**

```python
from strands.hooks import BeforeToolCallEvent


@agent.hooks.tool_called
def log_skill_call(event):
    if event.tool_use["name"] == "skills":
        print(f"激活 Skill: {event.tool_use['input']['skill_name']}")
        print(f"Skill 全文长度: {len(event.tool_result.get('content', ''))}")
```

**3. 统计每轮 Skill 相关 token**

```python
agent_result = agent("...")
metrics = agent_result.metrics
print(f"input_tokens:  {metrics.accumulated_usage['inputTokens']}")
print(f"output_tokens: {metrics.accumulated_usage['outputTokens']}")
```

**4. 验证 Schema 是否正确暴露**

```python
# 拿到所有 tools 的 schema,看 skills tool 长什么样
tools = agent.tool_registry.get_all_tools_definitions()
for t in tools:
    if t["toolSpec"]["name"] == "skills":
        print(json.dumps(t, indent=2, ensure_ascii=False))
```

---

## 🧩 六、最佳实践(综合 Strands + Claude Code 经验)

### 6.1 Level 1 元数据控制

- [ ] **Skill 总数控制在 20 个以内** —— 超过就分目录 / 分 namespace
- [ ] **每个 description < 300 token** —— 模型注意力在长列表末尾会衰减
- [ ] **description 写清触发 + 反例** —— 见 [03_Skill_Schema设计最佳实践.md §3](./03_Skill_Schema设计最佳实践.md)
- [ ] **元数据块打 `cache_control`** —— 多轮 invocation 复用缓存
- [ ] **每次 invocation 都重新注入 + 精确替换** —— 支持热加载 Skill

### 6.2 Level 2 Skill 全文控制

- [ ] **instructions 只放流程骨架** —— 大块内容放 references/
- [ ] **每个 Skill 都有可验证的输出** —— 让模型「做完一步就能验证」
- [ ] **resources 目录最多 20 个文件** —— 超过用 references/ 子目录分页
- [ ] **Skill 全文也打 `cache_control`** —— 同一 Skill 在多轮中复用

### 6.3 event loop / 调用链控制

- [ ] **递归深度有上限** —— Strands 有 `_check_limits`,防止无限循环
- [ ] **tool_use 失败有重试** —— Strands `ModelRetryStrategy` 默认开启
- [ ] **tool_result 大小有截断** —— Strands `generate_missing_tool_result_content` 处理
- [ ] **激活的 Skill 列表要持久化** —— Strands `_track_activated_skill` + `agent.state`

---

## 🧪 七、一个完整的可运行 Demo

```python
"""Demo:Strands AgentSkills 两级调用的最小可运行示例"""
import asyncio
from pathlib import Path
from strands import Agent
from strands.vended_plugins.skills import AgentSkills, Skill

# 1. 准备两个 Skill(内存方式,不依赖文件系统)
skill_review = Skill(
    name="python-code-review",
    description=(
        "Use this skill when the user wants to review Python code for "
        "type hints, docstrings, and PEP 8 compliance. NOT for security "
        "audit (use security-audit instead)."
    ),
    instructions="""
## Steps
1. Read the file at the user-provided path
2. For each function/method:
   - Verify it has a docstring
   - Verify all parameters have type hints
   - Check line length <= 100
3. Output a JSON report with format:
   ```json
   {"file": "...", "issues": [{"line": N, "type": "...", "msg": "..."}]}
   ```
""",
    allowed_tools=["Read", "Bash"],
)

skill_test = Skill(
    name="python-test-gen",
    description=(
        "Use this skill when the user wants to generate pytest unit tests "
        "for a Python module. NOT for integration tests or e2e tests."
    ),
    instructions="""
## Steps
1. Read the target file
2. For each public function, generate a pytest test case covering:
   - Happy path
   - Edge cases (empty input, None, boundary values)
3. Write tests to `tests/test_<module>.py`
""",
    allowed_tools=["Read", "Write"],
)

# 2. 创建 plugin + agent
plugin = AgentSkills(skills=[skill_review, skill_test])

agent = Agent(
    system_prompt="你是一个 Python 代码质量助手。",
    plugins=[plugin],
)

# 3. 第一次打印:看 Level 1 注入的 XML 长什么样
print("=" * 60)
print("Level 1: 注入到 system prompt 的元数据")
print("=" * 60)
print(agent.system_prompt)

# 4. 触发一次完整 invocation(会经过 L1 → L2)
async def main():
    result = await agent.invoke_async(
        "帮我审查 src/auth/login.py 这个文件"
    )
    print("\n" + "=" * 60)
    print("最终回答:")
    print("=" * 60)
    print(result)

# asyncio.run(main())  # 取消注释即可运行
```

预期 Level 1 system prompt 输出:

```
你是一个 Python 代码质量助手。

<available_skills>
  <skill>
    <name>python-code-review</name>
    <description>Use this skill when the user wants to review Python code for type hints, docstrings, and PEP 8 compliance. NOT for security audit (use security-audit instead).</description>
  </skill>
  <skill>
    <name>python-test-gen</name>
    <description>Use this skill when the user wants to generate pytest unit tests for a Python module. NOT for integration tests or e2e tests.</description>
  </skill>
</available_skills>
```

预期 Level 2 流程(简化):

```
LLM(turn 1) → tool_use: skills(skill_name="python-code-review")
              tool_result: "# python-code-review\n\n## Steps\n1. ..."
LLM(turn 2) → tool_use: Read(file_path="src/auth/login.py")
              tool_result: <文件内容>
LLM(turn 3) → content: "审查报告:\n1. login_user 缺少 docstring..."
```

---

## 🧩 关键 Takeaway

> **Meta-Prompt 两级调用 = 元数据进 system_prompt + 全文回灌 tool_result + event_loop 自然递归**
>
> 1. **不要发明新调度器** —— 利用 LLM 的 event loop 把「第 2 级调用」变成循环的下一轮
> 2. **元数据是入口,全文是弹药** —— 描述决定选不选,内容决定做不做
> 3. **Token 经济性 = 元数据必缓存 + 全文按需加载 + descriptions 不重叠** —— 三者叠加才能省 token

---

## 🔗 关联文档

- [01_LLM如何理解Skill.md](./01_LLM如何理解Skill.md) — 三步循环总览
- [02_Prompt注入机制.md](./02_Prompt注入机制.md) — Context Window 分层堆叠 + Skill 注入
- [03_Skill_Schema设计最佳实践.md](./03_Skill_Schema设计最佳实践.md) — name / description / input_schema 怎么写
- [README.md](./README.md) — 系列索引
