# 📚 LLM Skill 与 Prompt 注入原理

> 整理自 Claude Code 会话 `5349010b-...` 的两轮问答,后续继续深入扩展

本目录沉淀关于 **「LLM 如何理解并使用 Skill / Tool」** 和 **「Prompt 注入到底是怎么回事」** 的核心原理,作为后续深入学习与扩展的入口。

---

## 📑 文档清单

| 文件 | 内容 | 适用读者 |
|---|---|---|
| [01_LLM如何理解Skill.md](./01_LLM如何理解Skill.md) | Q1 总结:Skill/Tool 本质是「上下文工程 + 模式匹配 + 外部运行时」的三步循环 | 想理解 Agent Tool Use 底层原理的人 |
| [02_Prompt注入机制.md](./02_Prompt注入机制.md) | Q2 总结:Prompt 注入 = HTTP 请求里的字符串拼接 + Context Window 的分层堆叠 | 想搞清楚 system prompt、Skill、Memory、Tools 是怎么进 LLM 的人 |
| [03_Skill_Schema设计最佳实践.md](./03_Skill_Schema设计最佳实践.md) | Skill 元数据(name/description/input_schema/instructions)怎么写,让模型「选得对、填得准、做得好」 | 在写或评审 Skill 的工程师 |
| [04_Meta_Prompt两级调用工程实现.md](./04_Meta_Prompt两级调用工程实现.md) | 以 Strands `AgentSkills` 源码为主线,拆解「元数据进 system_prompt + 全文回灌 tool_result + event_loop 隐式递归」的完整调用链;对比 Claude Code / Cline / Continue 的实现差异 | 在写 Agent 框架、做 Skill 调度、设计 Meta-Prompt 的工程师 |
| [05_Context_Window分层堆叠真实代码.md](./05_Context_Window分层堆叠真实代码.md) | 用 Strands 源码 trace 一遍 `Agent(prompt)` → HTTP payload 的全链路:`BeforeInvocationEvent` → `HookOrder` → `split_system_prompt` → `AnthropicModel._format_request`;对照 Claude Code 的 9 层注入顺序 | 在做 Agent 框架、做 Plugin / Hook、设计 system_prompt 分层的工程师 |
| [06_Prompt_Caching实战.md](./06_Prompt_Caching实战.md) | system / tools / messages 三层 cache_control 在 Strands `AnthropicModel` 里的真实实现、Anthropic 计费模型、TTL 陷阱、跨 provider 差异、真实成本测算(可省 50–90%) | 优化 Agent 成本、做 cache 策略、在生产环境调优 LLM 调用的工程师 |
| [07_Prompt_Injection攻防.md](./07_Prompt_Injection攻防.md) | 6 大攻击向量(直接/间接/Skill/Memory/Smuggling/Confusion)+ Strands 7 层防御体系(Interrupt / Sandbox / Tool 白名单 / 输出审计)+ 攻防对照测试 | 负责 Agent 安全、做红蓝对抗、设计 HITL 工作流的工程师 |

---

## 🗺️ 后续可深入的方向(占位,后续扩展时新建文件)

- [x] **Skill Schema 设计的最佳实践**:description 怎么写才能让模型选对 tool、参数描述怎么避免幻觉 → [03_Skill_Schema设计最佳实践.md](./03_Skill_Schema设计最佳实践.md)
- [x] **Meta-Prompt 两级调用的工程实现**:Claude Code / Cursor / Cline 在 `Skill` 工具上的真实代码路径(可结合 Strands `AgentSkills._generate_skills_xml` / `_format_skill_response` 源码) → [04_Meta_Prompt两级调用工程实现.md](./04_Meta_Prompt两级调用工程实现.md)
- [x] **Context Window 分层堆叠的真实代码**:从 `SessionStart` hook 到 `messages` payload 的完整 trace → [05_Context_Window分层堆叠真实代码.md](./05_Context_Window分层堆叠真实代码.md)
- [x] **Prompt Caching 实战**:哪些块该缓存、缓存命中率怎么算、计费逻辑 → [06_Prompt_Caching实战.md](./06_Prompt_Caching实战.md)
- [x] **Prompt Injection 攻防**:用户输入里的 `<system>` 劫持、间接注入(tool result 回灌)、防御手段 → [07_Prompt_Injection攻防.md](./07_Prompt_Injection攻防.md)
- [ ] **Tool Use 失败的容错闭环**:JSON Schema 校验失败时的重试、参数修补、用户确认
- [ ] **Skill 与 Tool 的边界**:为什么 Claude Code 区分 `Skill`(指令)和 `Tool`(能力)?能否互相替代?
- [ ] **多 Agent 场景下的 Skill 共享**:SubAgent 的 Skill 加载策略、父子 Skill 的隔离与继承

---

## 🔗 参考来源

- Claude Code 会话:`~/.claude/projects/-mnt-d-agent-harness-sdk-demo-strands-agent/5349010b-e4d7-422a-bc7d-735054c8c12e.jsonl`
- 关联项目根目录:`strands-agent/`
- 相关已有文档:
  - `docs/Agent工作原理_ELI5.md` — Strands Agent 整体运行原理
  - `docs/核心事件循环详解.md` — Agent 事件循环
  - `docs/官方文档校验报告与学习路径.md` — 学习路径建议
