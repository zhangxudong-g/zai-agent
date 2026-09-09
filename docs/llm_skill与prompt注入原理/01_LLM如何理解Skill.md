# 🧠 LLM 如何理解并使用 Skill —— 核心原理

> 总结自 Claude Code 会话 `5349010b-...` 的 Q1  
> **核心结论**:Skill/Tool 并不是 LLM "原生"会用的能力,而是通过**结构化的提示工程 + 模式匹配 + 外部运行时**拼装出来的「幻觉式调用」。模型本身只输出文本,真正的执行靠外部系统。

---

## 🎯 一句话总结

> **Skill 的本质 = 上下文工程 + 模式匹配 + 外部运行时**  
> LLM 没有"手",Skill 描述是给它的「说明书」,真正干活的是外面那层胶水代码。

---

## 🔄 核心原理:三步循环

```
┌─────────────────────────────────────────────────┐
│  1️⃣ 声明 (Schema)                               │
│     把 Skill 描述以 JSON Schema 格式塞进上下文    │
│                     ↓                            │
│  2️⃣ 决策 (Inference)                            │
│     LLM 在 token 级别「预测」该调用哪个 tool       │
│                     ↓                            │
│  3️⃣ 执行 (Runtime)                              │
│     外部解析、调用函数、把结果回填给 LLM 继续推理  │
└─────────────────────────────────────────────────┘
```

---

## 1️⃣ 声明阶段:把 Skill 「喂」给模型

LLM 没有任何「内置」工具,它只是在 **上下文窗口** 里看到了工具描述:

```python
tools = [
    {
        "name": "get_weather",
        "description": "获取指定城市的当前天气",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名"}},
            "required": ["city"],
        },
    }
]
```

这些描述会被拼进 system prompt,告诉模型:**「你拥有这些能力,可以用 JSON 格式调用它们」**。

---

## 2️⃣ 决策阶段:为什么模型「会」调用?

这其实是 **统计学习 + 模式匹配** 的产物:

| 训练数据中的模式 | 模型学到的能力 |
|---|---|
| 海量代码(API 调用、JSON 数据) | 识别「何时需要外部信息」 |
| 函数定义 / 文档字符串 | 把描述映射到参数 |
| ReAct、Toolformer 等微调数据 | 「思考 → 行动」的循环范式 |

当用户问「北京天气怎么样?」时,模型的 Transformer 会:

1. **注意力机制** 命中「天气」相关的 token
2. 在工具描述的约束下,**预测** 应该输出 `{"name": "get_weather", "arguments": {"city": "北京"}}`
3. 这个 JSON **不是「调用」**,而是 **一段文本**,只不过格式严格符合 schema

---

## 3️⃣ 执行阶段:外部运行时介入

```python
# 模型输出的是文本,不是真实调用
response = llm.invoke("北京天气?", tools=tools)
# response.tool_calls == [{'name': 'get_weather', 'args': {'city': '北京'}}]

# 真正执行的是这段代码
result = get_weather(city="北京")  # 真实函数调用

# 结果回填给模型
final = llm.invoke(messages=[..., ToolMessage(content=json.dumps(result), tool_call_id=...)])
```

---

## 🪄 为什么 Claude Code 的 Skill 能工作?

以 `using-superpowers` Skill 为例:

1. **Skill 是一份 markdown 指令文件**,不是二进制插件
2. Skill 工具调用时,系统把 Skill 的 **完整内容** 注入到 system prompt
3. 模型看到这些指令后,**按指令行事**(因为指令里描述了触发条件和行为模式)
4. 真正的「读文件、遵循步骤」是模型在文本层面完成的,**Skill 本身不执行任何代码**

```
┌──────────────────────────────────────┐
│ System Prompt(运行时注入)            │
│                                      │
│ # using-superpowers                  │
│ 你必须在做任何事之前先调用相关 skill... │
│                                      │
│ ## Rule                              │
│ Invoke skills BEFORE any response...  │
└──────────────────────────────────────┘
                    ↓
         模型在生成第一个 token 时,
         就已经把这条规则「内化」到 attention 里了
```

---

## 🚨 三个常见误解澄清

| 误解 | 真相 |
|---|---|
| ❌「LLM 自己会调用工具」 | ✅ LLM 只生成符合 schema 的 JSON 文本,真正执行靠外部代码 |
| ❌「Skill 让模型多了一种能力」 | ✅ Skill 是给模型**写好的操作手册**,模型按手册做事 |
| ❌「模型能可靠地使用工具」 | ✅ 模型可能编造参数、漏填字段,所以需要 **JSON Schema 校验 + 重试机制** |

---

## 🧩 关键 Takeaway

> **Skill/Tool 的本质 = 上下文工程 + 模式匹配 + 外部运行时**
> 
> 模型能不能用好 skill,取决于三个要素:
> 1. **描述写得清不清楚** — 决定模型能否「选对」tool
> 2. **schema 约束够不够严格** — 决定模型能否「填对」参数
> 3. **错误处理闭环完不完整** — 决定调用失败时能否「自愈」

---

## 🔗 关联文档(待扩展)

- [02_Prompt注入机制.md](./02_Prompt注入机制.md) — Skill 内容是怎么注入到 prompt 的
- [README.md](./README.md) — 本目录索引与后续扩展方向
