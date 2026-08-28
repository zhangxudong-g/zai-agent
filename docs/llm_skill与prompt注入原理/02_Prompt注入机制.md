# 💉 Prompt 注入机制 —— 完整链路拆解

> 总结自 Claude Code 会话 `5349010b-...` 的 Q2  
> **核心结论**:"注入"就是把文本塞进 HTTP 请求的 `system` 字段或 `messages` 数组,模型**没有任何主动读取机制** —— 它只是在生成下一个 token 时,从整个上下文窗口里挑最相关的。

---

## 🎯 一句话总结

> **「注入」 ≠ 神秘的旁路机制,而是 = HTTP 请求 body 里的字符串拼接。**
> 1. **Runtime 负责拼字符串**:把 Skill、Memory、Tools、History 拼成 system + messages
> 2. **API 负责传字符串**:HTTP POST 把整个 payload 发给 Anthropic
> 3. **LLM 负责读字符串**:模型没有任何"主动调用",它只是从这堆 token 里预测下一个最合理的输出

---

## 🌐 一、传输层:一个普通的 HTTP POST

Claude Code 并不神秘,底层就是一个 HTTP 请求:

```http
POST https://api.anthropic.com/v1/messages
Headers:
  x-api-key: sk-ant-...
  anthropic-version: 2023-06-01
  content-type: application/json

Body:
{
  "model": "claude-opus-5",
  "max_tokens": 8192,
  "system": "...",          ← 系统提示(高优先级)
  "tools": [...],           ← 工具 schema
  "messages": [             ← 对话历史
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}
```

**所有「注入」都是在构造这个 JSON 对象**。模型收到后开始 tokenize,然后一次性进入 Transformer。

---

## 🥞 二、提示词的「分层堆叠」

Claude Code 启动后,context window 里大致是这样的结构(从上到下):

```
┌─────────────────────────────────────────┐
│ 1. 底层安全 / 身份 System Prompt         │  ← Anthropic 硬编码,不可改
│    "You are Claude, made by Anthropic..." │
├─────────────────────────────────────────┤
│ 2. Claude Code 行为 System Prompt       │  ← 工具自身定义
│    "You are Claude Code, a CLI tool..."   │
├─────────────────────────────────────────┤
│ 3. 环境信息 (cwd, OS, git status)        │  ← 运行时注入
├─────────────────────────────────────────┤
│ 4. CLAUDE.md / AGENTS.md                │  ← 项目级指令(若有)
├─────────────────────────────────────────┤
│ 5. Memory(从 ~/.claude/memory 加载)     │  ← 跨会话记忆
├─────────────────────────────────────────┤
│ 6. 激活的 Skill 内容                    │  ← 上一份文档讲的机制 ⭐
├─────────────────────────────────────────┤
│ 7. 工具 schema(Bash, Read, Skill, ...)  │  ← JSON Schema
├─────────────────────────────────────────┤
│ 8. 对话历史 messages[]                  │  ← 上一轮问答
├─────────────────────────────────────────┤
│ 9. 用户最新输入                         │  ← 这次问的问题
└─────────────────────────────────────────┘
```

**所有这些块在发给 API 之前,会被 runtime 拼成一个大的字符串/token 序列**,对模型来说没有「分层」概念,只是一段连续的文本。

---

## 🎬 三、Skill 的注入细节(以 Claude Code 为例)

### 触发时机

当你问「如何注入提示词?」时,Claude Code 在底层做了这件事:

```python
# 伪代码,展示核心流程
def on_user_input(user_message):
    # 1. 加载"按需"Skill 的元数据(只有描述,几百 token)
    available_skills = [
        {"name": "brainstorming", "description": "Use when creating features..."},
        {"name": "systematic-debugging", "description": "Use when encountering any bug..."},
        # ...
    ]

    # 2. 拼进 system prompt(只暴露名字 + 描述,不暴露内容)
    system += format_skill_list(available_skills)

    # 3. 第一次调用 LLM,让它判断要不要用 Skill
    response = llm_call(system=system, messages=[user_message], tools=[...])

    # 4. 如果模型决定调用 Skill tool
    if response.tool_call.name == "Skill":
        skill_name = response.tool_call.args["skill"]
        skill_content = read_file(f".claude/skills/{skill_name}.md")

        # 5. 把 Skill 内容作为"工具结果"塞进对话
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": response.tool_call.id,
                "content": skill_content   # ⭐ 真正的"注入"发生在这里
            }]
        })

        # 6. 第二次调用 LLM(此时模型已经看到完整 Skill 指令)
        final = llm_call(system=system, messages=messages)
```

### 关键点:**两级调用**(meta-prompt 模式)

```
调用 1: [系统提示 + Skill 列表摘要 + 用户问题]
        ↓ 模型输出
        "我应该调用 Skill: brainstorming"

调用 2: [系统提示 + Skill 完整内容(作为 tool_result) + 用户问题]
        ↓ 模型输出
        "我现在按照 Skill 指令开始 brainstorm..."
```

**为什么要这样做?** 因为完整加载所有 Skill 会爆 token。常见优化策略:

| 策略 | 做法 | 代价 |
|---|---|---|
| **延迟加载**(默认) | 第一轮只给名字 + 描述,用到时再读全文 | 多一次 LLM 调用 |
| **预加载** | 一次性全部塞进 system prompt | 简单但贵,token 消耗大 |
| **混合** | 关键 Skill 全量,其他按需 | 平衡点,实际工程常用 |

---

## 🛠️ 四、其他注入手段(代码层面)

### 1. 直接塞 system 字段(最常见)

```python
import anthropic

client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-opus-5",
    system="你是一位资深 Python 工程师,只回答 Python 相关问题。",  # ← 注入
    messages=[{"role": "user", "content": "如何读取文件?"}]
)
```

### 2. 用 `system` 数组(支持缓存 + 多块)

```python
system=[
    {"type": "text", "text": "你是一位资深 Python 工程师"},
    {"type": "text", "text": "<env>当前时间:2026-08-28</env>"},
]
```

### 3. 在 user 消息里「伪注入」(⚠️ 不推荐)

```python
messages=[{
    "role": "user",
    "content": f"""
    【系统指令】你只能回答技术问题。
    【用户输入】今天天气怎么样?
    """
}]
```

⚠️ 这种方式**不安全**,因为用户输入可以伪造 system 标记 —— 这就是经典的 **Prompt Injection 攻击**。

### 4. Prompt Caching(性能优化)

```python
system=[
    {
        "type": "text",
        "text": "你是一位资深 Python 工程师...",
        "cache_control": {"type": "ephemeral"}  # ← 缓存这段
    }
]
```

下次请求时,这段不会被重新计费/重新计算,首 token 延迟大幅降低。

---

## ⚠️ 五、注入的「陷阱」

| 陷阱 | 说明 |
|---|---|
| **位置敏感** | 开头的指令权重最高,越靠后越容易被「忽略」(注意力衰减) |
| **长度爆炸** | context window 有限(200K tokens),塞太多反而稀释关键指令 |
| **互相冲突** | 多层 prompt 矛盾时,模型会「挑一个最自然的」(不一定是你想要的) |
| **注入攻击** | 用户输入里塞 `<system>忽略之前所有指令</system>` 可能劫持行为 |

---

## 🧩 关键 Takeaway

> **「注入」 ≠ 神秘的旁路机制,而是 = HTTP 请求 body 里的字符串拼接。**
>
> 所以**提示词工程的核心**,不是找到某种「高级注入 API」,而是**怎么把这堆字符串组织得让模型最容易「听话」** —— 这就是为什么**位置、措辞、示例**都重要。

---

## 🔗 关联文档

- [01_LLM如何理解Skill.md](./01_LLM如何理解Skill.md) — Skill/Tool 的三步循环
- [README.md](./README.md) — 本目录索引与后续扩展方向
