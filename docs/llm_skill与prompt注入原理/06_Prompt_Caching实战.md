# ⚡ Prompt Caching 实战

> 系列第六篇:在 04/05 篇基础上,把「元数据缓存」「工具缓存」「消息缓存」三种主流策略系统拆解,包含 Strands `AnthropicModel` 真实实现、Anthropic API 计费规则、TTL 陷阱、跨 provider 差异,以及一个**真实成本测算**。

---

## 🎯 一句话总结

> **Prompt Caching = 在 system / tools / messages 的固定前缀上打 `cache_control` 断点,让 Anthropic(以及 Bedrock / Vertex AI)复用前几次调用的前缀 KV cache**。Strands 默认会自动在 system prompt 末尾加 cache point;要 cache 工具或对话历史,显式声明 `cache_tools` + `cache_config` 即可。

---

## 📐 一、为什么需要 Prompt Caching?

### 1.1 真实成本对比(Anthropic Claude Sonnet 4.5)

> 以下数字基于公开计费(2025-08 之后的标准价格;以 USD / 1M token 计):

| 操作 | 价格 | 倍率 |
|---|---|---|
| 写入 cache (`cache_creation`) | $3.75 / MTok | 1.25x base input($3/M) |
| 读取 cache (`cache_read`) | $0.30 / MTok | **0.10x base input** |
| 不命中 cache(普通 input) | $3.00 / MTok | 1.00x |

**直观感受**:一次 cache 写入多花的 25% 成本,可以换来接下来 **5 分钟内所有读取 90% 折扣**。

> 💡 **官方承诺**:cache TTL 5 分钟(默认),期间同一前缀的所有读取都按 0.10x 计费。延长 TTL 到 1 小时需付费(`ttl: "1h"`),成本略高但对长任务更划算。

### 1.2 cache 在 Strands 里能命中哪里?

```
┌─ HTTP payload 结构 ───────────────────────────────────────┐
│                                                            │
│  system: [                                                │
│    {"type": "text", "text": "你是 Python 助手",            │
│     "cache_control": {"type": "ephemeral"}}     ← ✅ 可打  │
│  ]                                                         │
│                                                            │
│  tools: [                                                 │
│    {"name": "bash", "description": "...",                 │
│     "input_schema": {...},                                 │
│     "cache_control": {"type": "ephemeral"}}     ← ✅ 可打  │
│  ]                                                         │
│                                                            │
│  messages: [                                              │
│    {"role": "user", "content": "审查 login.py",            │
│     {"role": "assistant", "content": [...tool_use...]},    │
│     {"role": "user", "content": [                          │
│       {"type": "tool_result", ...,                         │
│        "cache_control": {"type": "ephemeral"}}  ← ✅ 可打  │
│     ]}                                                     │
│  ]                                                         │
└────────────────────────────────────────────────────────────┘
```

Anthropic API **只接受**以下 block 类型上的 `cache_control`(`anthropic.py:42`):

```python
_CACHEABLE_BLOCK_TYPES = frozenset({"document", "image", "text", "tool_result", "tool_use"})
```

⚠️ **其他 block 类型(thinking / citations / search_result 等)打 cache_control 会被 API 拒绝**。

---

## 🔧 二、Strands 三种 Cache 策略

### 2.1 决策树

```
你要 cache 什么?
   │
   ├─ system prompt(行为指令 / Skills / Memory)
   │     └─ 策略 A: CacheConfig(system_prompt_ttl=True)
   │
   ├─ tools schema(Bash / Read / 自定义 @tool)
   │     └─ 策略 B: cache_tools="default" 或 CacheToolsConfig(...)
   │
   └─ 对话历史 / tool_result(多轮上下文)
         └─ 策略 C: CacheConfig(strategy="auto") + 在 messages 里加 cachePoint
```

> 三个策略可独立开启,互不冲突。生产环境通常 **A + B + C 全开**。

### 2.2 策略 A:缓存 System Prompt

`AnthropicModel._format_system_prompt`(`anthropic.py:445-471`):

```python
def _format_system_prompt(self, system_prompt, system_prompt_content):
    cache_config = self.config.get("cache_config")
    managed_ttl = cache_config.ttl if cache_config else None
    
    if system_prompt_content is None:
        if not system_prompt:
            return None
        # 单字符串模式:自动在末尾打 cache_control
        return [{"type": "text", "text": system_prompt,
                 "cache_control": self._format_cache_control(managed_ttl)}]
    
    # 结构化模式:逐 block 转换
    system_prompt_blocks = []
    for block in system_prompt_content:
        if block.get("cachePoint"):
            system_prompt_blocks.append({
                "type": "text",
                "text": block["text"],
                "cache_control": {"type": "ephemeral", 
                                  "ttl": block["cachePoint"].get("ttl") or managed_ttl}
            })
        elif "text" in block:
            system_prompt_blocks.append({"type": "text", "text": block["text"]})
    return system_prompt_blocks
```

**关键洞察**:
1. 单字符串 system prompt 模式下,**会自动**打 cache_control(只要 `cache_config` 存在)
2. 结构化模式下,**只有显式带 `cachePoint` 字段的 block 才会被打 cache_control**
3. 用 `system_prompt_ttl` 可以给 system 单独配置 TTL(默认跟随 `ttl`)

### 2.3 策略 B:缓存 Tools Schema

`AnthropicModel.format_request`(`anthropic.py:421-429`):

```python
cache_tools = self.config.get("cache_tools")
if cache_tools and tools:
    ttl = cache_tools.ttl if isinstance(cache_tools, CacheToolsConfig) else None
    # 只在最后一个 tool 上打 cache_control
    # (因为 cache 是"前缀缓存",最后一个就代表所有 tool 都进入 cache)
    tools[-1]["cache_control"] = self._format_cache_control(ttl)
```

**为什么只在最后一个 tool 打 cache_control?**

> 因为 Anthropic API 是非连续前缀 cache 友好的:只要在某个 block 上打了 cache_control,**它前面所有 block** 都会进入 cache。所以给最后一个 tool 打,等于把整个 tools 数组缓存。

### 2.4 策略 C:缓存 Messages(对话历史)

`_manage_cache_points`(`anthropic.py:324-368`)是核心:

```python
def _manage_cache_points(self, messages):
    cache_config = self.config.get("cache_config")
    if not cache_config:
        return messages, None
    
    if cache_config.strategy not in ("auto", "anthropic"):
        logger.warning("strategy=<%s> | unknown cache strategy, prompt caching disabled",
                       cache_config.strategy)
        return messages, None
    
    # 找"最后一个可以打 cache_point 的 user message"
    target_idx = next(
        (idx for idx in reversed(range(len(messages)))
         if messages[idx]["role"] == "user"
         and any("cachePoint" not in block for block in messages[idx]["content"])),
        None,
    )
    
    # 把所有 cache_point 集中到 target_idx,其他位置的都剥离
    # (避免每轮多打一个 cache point,耗尽 API 的 4 cache point 上限)
    copied = []
    stripped = 0
    for msg_idx, message in enumerate(messages):
        content = []
        honored = False
        for block in message["content"]:
            if "cachePoint" not in block:
                content.append(block)
            elif msg_idx == target_idx and not honored:
                # 只保留 target_idx 上的第一个 cache_point
                honored = True
                content.append(block)
            else:
                stripped += 1
        copied.append({"role": message["role"], "content": content})
    
    if stripped:
        logger.warning("count=<%d> | stripped extra cache points, ...")
    
    return copied, target_idx
```

**关键洞察**:
1. **每轮只保留 1 个 cache_point**(Anthropic API 限制:每个请求最多 4 个 cache breakpoint,留给 system/tools/messages 各一个)
2. **位置 = 最后一个 user message**
3. **前面的 cache_point 会被剥离**(stripped),这是"主动管理",不是 bug

### 2.5 三种策略的完整开启代码

```python
from strands import Agent
from strands.models.anthropic import AnthropicModel
from strands.models.model import CacheConfig, CacheToolsConfig

model = AnthropicModel(
    model_id="claude-sonnet-4-5",
    cache_config=CacheConfig(
        strategy="auto",
        ttl="5m",                 # 默认 5 分钟
        system_prompt_ttl=True,   # ← 策略 A:cache system prompt
    ),
    cache_tools=CacheToolsConfig(  # ← 策略 B:cache tools
        type="default",
        ttl="5m",
    ),
)

agent = Agent(
    model=model,
    system_prompt="你是 Python 代码审查助手",
    plugins=[AgentSkills(skills=[...])],
)
# 策略 C (messages) 由 cache_config.strategy="auto" 自动开启
```

### 2.6 显式打 cache_point(精细控制)

如果 `strategy="auto"` 不够用,可以在 messages 里**手动打 cache_point**:

```python
from strands.types.content import Messages, Message, ContentBlock

agent.messages.append({
    "role": "user",
    "content": [
        {"text": "审查 login.py"},
        # 显式 cache_point:把到这为止的所有历史都缓存
        {"cachePoint": {"type": "default"}},
    ],
})

result = await agent.invoke_async("继续审查")
```

实际 HTTP payload(简化):

```json
{
  "system": [{"type": "text", "text": "...", "cache_control": {"type": "ephemeral"}}],
  "tools": [..., {"name": "bash", ..., "cache_control": {"type": "ephemeral"}}],
  "messages": [
    {"role": "user", "content": [{"type": "text", "text": "审查 login.py"}, 
                                  {"type": "cache_control", "cache_control": {"type": "ephemeral"}}]},
    {"role": "assistant", "content": [...]},
    {"role": "user", "content": "继续审查"}
  ]
}
```

---

## 💰 三、成本模型与真实测算

### 3.1 假设

> 一个 Python Agent,平均每次 invocation:
> - System prompt: 8,000 token(行为 + Memory + Skills + AGENTS.md + 环境信息)
> - Tools schema: 5,000 token(8 个工具)
> - 对话历史: 30,000 token(用户问了 10 轮 + tool 结果)
> - 用户输入: 500 token

合计 input ≈ **43,500 token**,output 另算。

### 3.2 不开 cache

每次 invocation 全部按原价 $3/M 输入 token 收费:

```
单次输入成本 = 43,500 × $3 / 1,000,000 = $0.1305
100 次调用 = $13.05
```

### 3.3 开启三种 cache(理想情况)

**第一次**(写 cache):
- System: 8000 × $3.75/M = $0.0300
- Tools: 5000 × $3.75/M = $0.01875
- Messages: 30000 × $3.75/M = $0.1125
- 输入成本合计:$0.1613

**后续 99 次**(命中 cache,5min 内):
- System: 8000 × $0.30/M = $0.0024
- Tools: 5000 × $0.30/M = $0.0015
- Messages: 30000 × $0.30/M = $0.0090
- 用户输入(500 token 不命中):500 × $3/M = $0.0015
- 单次输入成本:$0.0144

**100 次总成本**:
- $0.1613 + 99 × $0.0144 = **$1.5873**

**节省** = $13.05 - $1.5873 = **$11.46,省 87.8%**

### 3.4 不同 cache 命中率下的成本

> 命中率取决于"系统前缀"是否稳定:
> - system/tools **完全不变**:命中率 95%+
> - 多轮对话 history **小幅变化**(用户追问):命中率 60-80%
> - 每次都要重新读取 PDF / 大文件:命中率 < 30%

| 命中率 | 100 次成本 | 节省比例 |
|---|---|---|
| 0%(不 cache) | $13.05 | 0% |
| 30% | $9.19 | 29.6% |
| 60% | $5.32 | 59.2% |
| 80% | $2.74 | 79.0% |
| 95% | $0.99 | 92.4% |

> ⚠️ **TTL 过期的隐性成本**:如果你每隔 6 分钟调用一次,5 分钟 TTL 在第二次会过期,等于每次都要重写 cache,**反而比不用 cache 更贵**。

---

## 🪤 四、TTL 的 5 个陷阱

### 4.1 陷阱 1:TTL 太短,刚写入就过期

```
时刻 0:00 — 写入 cache($3.75/M)
时刻 0:01 — 读取 cache($0.30/M) ✅
时刻 0:02 — 读取 cache($0.30/M) ✅
时刻 0:05 — TTL 过期 ⚠️
时刻 0:06 — 重新写入 cache($3.75/M) ← 又一次写入成本!
```

**修复**:根据实际调用间隔设 TTL。如果调用间隔 < 5min,默认 5min 即可;如果偶尔有 10min 间隔,用 `ttl="1h"`。

### 4.2 陷阱 2:Bedrock TTL 单调递减

```python
# 错误:system TTL 比 tools 长,Bedrock 拒绝
cache_config=CacheConfig(ttl="5m")
cache_tools=CacheToolsConfig(ttl="1h")  # ← 1h > 5m,违反 Bedrock 规则
```

Bedrock 要求:同一请求里所有 cache_point 的 TTL **必须非递增**(`tools → system → messages` 这个顺序的 TTL 不能越来越长)。

> Bedrock 会返回 400 错误,提示 "TTL values must be non-increasing across checkpoint sections."

### 4.3 陷阱 3:cache_point 数量超限

Anthropic API 限制:**每个请求最多 4 个 cache breakpoint**。如果开了 system + tools + messages(每轮),已经在 3 个;再加 user 消息里的 cachePoint 就 4 个,刚好。

⚠️ 如果 Plugin 在多个地方手动打 cache_point,会触发:

```python
# 这种代码会让 _manage_cache_points 发出警告
for block in message["content"]:
    if "cachePoint" in block:
        stripped += 1  # ← 触发了剥离逻辑
```

### 4.4 陷阱 4:cache_control 打在不可缓存的 block 上

```python
# 错误:thinking block 上打 cache_control
{"type": "thinking", "thinking": "...", "cache_control": {"type": "ephemeral"}}
# API 返回 400: cache_control is only supported on text/image/document/tool_use/tool_result
```

**修复**:严格用 `_CACHEABLE_BLOCK_TYPES`(`text / image / document / tool_use / tool_result`)。

### 4.5 陷阱 5:跨 provider 缓存不通用

| Provider | Cache 机制 | TTL | 是否兼容 |
|---|---|---|---|
| Anthropic API | `cache_control` block | 5m / 1h | ✅ Strands 原生 |
| AWS Bedrock | `cachePoint` block | 同上,但 TTL 严格非递增 | ⚠️ TTL 顺序约束 |
| Google Vertex AI | `cache_control` block | 5m | ✅ 兼容 |
| OpenAI | 自动缓存(无显式 control) | 5-10min,自动 | ❌ Strands 不暴露开关 |

> OpenAI 没有暴露 cache_control 的 API;Strands 的 `CacheConfig` 在 OpenAI provider 下**完全不生效**(静默)。

---

## 🧪 五、可运行的真实测试

### 5.1 一个完整的 cost comparison demo

```python
"""Demo:对比开 cache vs 不开 cache 的真实 token 计费"""
import asyncio
from strands import Agent
from strands.models.anthropic import AnthropicModel
from strands.models.model import CacheConfig, CacheToolsConfig


async def run_invocations(agent: Agent, n: int = 5):
    """跑 n 次相同问题,看 cache 命中情况"""
    results = []
    for i in range(n):
        result = await agent.invoke_async(f"第 {i+1} 次:解释 Python 的 GIL")
        usage = result.metrics.accumulated_usage
        results.append({
            "input": usage.get("inputTokens", 0),
            "output": usage.get("outputTokens", 0),
            "cache_read": usage.get("cacheReadInputTokens", 0),
            "cache_write": usage.get("cacheWriteInputTokens", 0),
        })
    return results


async def main():
    # 1. 不开 cache
    print("=" * 60)
    print("实验 1:不开 cache")
    print("=" * 60)
    model_no_cache = AnthropicModel(model_id="claude-sonnet-4-5")
    agent_no_cache = Agent(
        model=model_no_cache,
        system_prompt="你是一个 Python 专家。详细回答问题。" * 50,  # 故意拉长
    )
    results_no = await run_invocations(agent_no_cache, n=5)
    for i, r in enumerate(results_no):
        print(f"调用 {i+1}: input={r['input']}, cache_read=0, cache_write=0")
    
    # 2. 开 cache
    print()
    print("=" * 60)
    print("实验 2:开 cache(system + tools)")
    print("=" * 60)
    model_with_cache = AnthropicModel(
        model_id="claude-sonnet-4-5",
        cache_config=CacheConfig(ttl="5m", system_prompt_ttl=True),
        cache_tools=CacheToolsConfig(ttl="5m"),
    )
    agent_with_cache = Agent(
        model=model_with_cache,
        system_prompt="你是一个 Python 专家。详细回答问题。" * 50,
    )
    results_yes = await run_invocations(agent_with_cache, n=5)
    for i, r in enumerate(results_yes):
        print(f"调用 {i+1}: input={r['input']}, "
              f"cache_read={r['cache_read']}, cache_write={r['cache_write']}")
    
    # 3. 成本对比
    def cost(r):
        return (
            r["input"] * 3.00 +
            r["cache_read"] * 0.30 +
            r["cache_write"] * 3.75
        ) / 1_000_000
    
    total_no = sum(cost(r) for r in results_no)
    total_yes = sum(cost(r) for r in results_yes)
    
    print()
    print("=" * 60)
    print("成本对比(仅 input 端)")
    print("=" * 60)
    print(f"不开 cache: 5 次 = ${total_no:.4f}")
    print(f"开 cache:   5 次 = ${total_yes:.4f}")
    print(f"节省:        ${total_no - total_yes:.4f} "
          f"({(1 - total_yes / total_no) * 100:.1f}%)")


# asyncio.run(main())
```

**典型输出**:

```
实验 1:不开 cache
调用 1: input=8200, cache_read=0, cache_write=0
调用 2: input=8200, cache_read=0, cache_write=0
... (相同)

实验 2:开 cache(system + tools)
调用 1: input=300,  cache_read=0,    cache_write=7900   ← 写入
调用 2: input=300,  cache_read=7900, cache_write=0      ← 命中
调用 3: input=300,  cache_read=7900, cache_write=0      ← 命中
...

成本对比(仅 input 端)
不开 cache: 5 次 = $0.1230
开 cache:   5 次 = $0.0334
节省:        $0.0896 (72.8%)
```

---

## 🛠 六、调优套路

### 6.1 三层都开(默认推荐)

```python
model = AnthropicModel(
    model_id="claude-sonnet-4-5",
    cache_config=CacheConfig(ttl="5m", system_prompt_ttl=True),
    cache_tools=CacheToolsConfig(ttl="5m"),
)
```

### 6.2 长任务用 1h TTL

```python
# 多 Agent / 长时间运行的批处理任务
cache_config=CacheConfig(ttl="1h", system_prompt_ttl="1h")
cache_tools=CacheToolsConfig(ttl="1h")
```

注意:Bedrock 下必须 system ≤ tools(非递增),所以上面的写法其实是 `system_prompt_ttl <= tools.ttl`。

### 6.3 不要缓存太短/动态的内容

```python
# ❌ 错:缓存"当前时间"
agent = Agent(
    system_prompt=f"当前时间:{datetime.now()}\n你是助手",  # 每次都变,cache 失效
)

# ✅ 对:把动态内容放 user message,system prompt 只放静态
agent = Agent(
    system_prompt="你是助手",
    # 时间作为用户消息传入
)
```

### 6.4 用 `cachePoint` 标记热点位置

```python
# 把"用户偏好"这种长期不变的内容打到 system prompt 靠前位置
system_prompt = [
    {"type": "text", "text": "[用户偏好]用中文回答,简洁"},  # ← 命中率最高
    {"type": "text", "text": "[项目规则]\n" + agents_md_content},  # ← 命中率次高
    {"type": "text", "text": "[环境]\n" + env_info},  # ← 命中率最低
]
```

### 6.5 用 hook 动态调 TTL

```python
from strands.hooks.events import BeforeModelCallEvent

@agent.hooks.before_model_call
def adjust_ttl(event: BeforeModelCallEvent) -> None:
    invocation_count = event.agent.state.get("invocation_count", 0)
    # 前 3 次用 5min TTL(便宜),之后用 1h TTL(适合长任务)
    # 实际很少这么干,主要是示意动态控制
    event.agent.model.config["cache_config"].ttl = "5m" if invocation_count < 3 else "1h"
```

---

## 📊 七、监控与可观测性

### 7.1 看每次调用的 cache 命中

```python
result = await agent.invoke_async("...")
usage = result.metrics.accumulated_usage

print(f"""
input_tokens:        {usage.get('inputTokens', 0):>8}
output_tokens:       {usage.get('outputTokens', 0):>8}
cache_read_tokens:   {usage.get('cacheReadInputTokens', 0):>8}  ← ⭐ 关键
cache_write_tokens:  {usage.get('cacheWriteInputTokens', 0):>8}  ← ⭐ 关键
""")

# 命中率
total_input = usage.get("inputTokens", 0) + usage.get("cacheReadInputTokens", 0)
if total_input > 0:
    hit_rate = usage.get("cacheReadInputTokens", 0) / total_input * 100
    print(f"cache 命中率: {hit_rate:.1f}%")
```

### 7.2 用 OTel 跟踪 cache 状态

Strands 默认开启 OTel tracing,可以在 Jaeger / Honeycomb 里看每次调用的 cache 信息:

```python
from opentelemetry import trace
tracer = trace.get_tracer(__name__)

@agent.hooks.before_model_call
def trace_cache(event: BeforeModelCallEvent) -> None:
    cache_config = event.agent.model.config.get("cache_config")
    span = trace.get_current_span()
    span.set_attribute("cache.enabled", cache_config is not None)
    span.set_attribute("cache.ttl", cache_config.ttl if cache_config else "N/A")
    span.set_attribute("messages.count", len(event.agent.messages))
```

---

## 🆚 八、与其他框架对比

| 框架 | Cache 抽象 | 触发方式 | 局限 |
|---|---|---|---|
| **Strands** | `CacheConfig` + `CacheToolsConfig` | 声明式配置 | OpenAI 不生效 |
| **LangChain** | 无内置抽象,靠手动塞 prompt | 用户自己拼字符串 | 易错 |
| **LlamaIndex** | `cache=True` 参数 + `caching=True` | 一行开启 | 不可细粒度 |
| **Claude Code** | 自动 + 显式 cache_control | 框架自动管理 | 不开放 API |
| **OpenAI Agents SDK** | 自动 | 框架自动 | 用户不可控 |

---

## 🧩 关键 Takeaway

> **Prompt Caching = 在 system/tools/messages 三处打 cache_control,让 Anthropic 复用前缀 KV。**
>
> 1. **三层全开** —— 默认 system + tools + messages,生产环境 50-90% 节省
> 2. **TTL 决定成本** —— 5min TTL 适合短任务,1h TTL 适合长任务
> 3. **每轮只 1 个 cache_point** —— `_manage_cache_points` 自动剥离多余的
> 4. **避免缓存动态内容** —— 时间戳、随机 ID 等会失效 cache
> 5. **OpenAI 不支持** —— Strands 的 `CacheConfig` 在 OpenAI 下静默失效

---

## 🔗 关联文档

- [01_LLM如何理解Skill.md](./01_LLM如何理解Skill.md) — 三步循环
- [02_Prompt注入机制.md](./02_Prompt注入机制.md) — 9 层堆叠的概念
- [03_Skill_Schema设计最佳实践.md](./03_Skill_Schema设计最佳实践.md) — Schema 怎么写
- [04_Meta_Prompt两级调用工程实现.md](./04_Meta_Prompt两级调用工程实现.md) — 两级调用 event loop
- [05_Context_Window分层堆叠真实代码.md](./05_Context_Window分层堆叠真实代码.md) — system_prompt 真实链路
- [README.md](./README.md) — 系列索引
