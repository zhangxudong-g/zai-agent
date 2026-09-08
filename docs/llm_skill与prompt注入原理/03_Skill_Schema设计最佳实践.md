# 🧩 Skill Schema 设计的最佳实践

> 系列第三篇:在前两篇「Skill 三步循环」「Prompt 注入机制」之上,**具体怎么写一份让 LLM 用得对、用得稳的 Skill**
>
> 范例来自:
> - Claude Code Skill 规范(`~/.claude/skills/<name>/SKILL.md` + YAML frontmatter)
> - Strands vended plugin `strands.vended_plugins.skills.Skill`(本项目已安装)
> - Strands `@tool` 装饰器从 docstring + type hints 自动生成 JSON Schema

---

## 🎯 一句话总结

> **Skill Schema 的设计核心 = 让模型「一眼看懂、一次选对、一填就准」**  
> 一个好 schema 在 5 个维度同时发力:`name` 唯一可识别、`description` 触发判断准、`input_schema` 约束严、`instructions` 行为模式清、`metadata` 留好拓展点。

---

## 📐 一、Schema 的两层结构

Skill 的「schema」其实有两层,经常被混淆:

```
┌────────────────────────────────────────────────┐
│ 第 1 层:Skill 元数据(给 LLM 看,用于"选哪个")  │
│  - name         : 标识                          │
│  - description  : 触发条件 + 用途                │
│  - allowed-tools: 允许调用的工具白名单           │
│  - metadata     : 拓展键值                       │
│  - compatibility: 环境/版本约束                  │
├────────────────────────────────────────────────┤
│ 第 2 层:Skill 指令(给 LLM 看,用于"怎么做")     │
│  - instructions : markdown 正文,流程化指导      │
│  - 资源:scripts/ references/ assets/ 目录       │
└────────────────────────────────────────────────┘
```

对应的「完整调用链路」:

| 阶段 | 用第几层 | 模型要做什么 |
|---|---|---|
| 工具选择 | 第 1 层 (name + description) | 在 N 个 skill 中挑 1 个 |
| 参数填充 | 第 1 层 (input_schema) | 生成符合 JSON Schema 的参数 |
| 行为执行 | 第 2 层 (instructions) | 按指令一步步干活 |

---

## 🏷️ 二、Name:看似简单,坑最多

### 2.1 命名硬规范(来自 AgentSkills.io / Strands 实现)

```python
_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_MAX_SKILL_NAME_LENGTH = 64
```

| 规则 | 示例 ✅ | 示例 ❌ |
|---|---|---|
| 1–64 个字符 | `pdf-extract` | (空) |
| 只允许小写字母、数字、连字符 | `frontend-debug` | `Pdf_Extract`、`PDF.Extract` |
| 不能以连字符开头或结尾 | `code-review` | `-code-review-` |
| 不能有连续连字符 | `react-hooks` | `react--hooks` |
| **必须与父目录同名** | 目录 `pdf-extract/` 内 `SKILL.md` 的 `name: pdf-extract` | 目录 `utils/`,`name: super-utils` |

### 2.2 命名软建议(实战经验)

| 建议 | 反例 | 正例 |
|---|---|---|
| 用**动宾短语**说明能力,而非名词 | `pdf`、`utils` | `pdf-extract`、`file-merge` |
| 体现**作用域**,避免过宽 | `code` | `python-deps-audit`、`react-hook-review` |
| 避免**缩写**,除非业内通用 | `msg-prsr`、`db-cnxn` | `message-parser`、`db-connection` |
| 保持**单数语义**,用连字符分词 | `extractDataFromPdf` | `extract-data-from-pdf` |

> 💡 **为什么这么严?** 因为模型在 tool 选择阶段是「看着 name + description 决策」的。`pdf` 太宽,模型每次都得猜;`extract-data-from-pdf` 一眼就知道何时触发。详见后文 §4.1。

---

## 📝 三、Description:决定模型「何时调用」

> **description 是 Skill Schema 中权重最高的字段** —— 它直接决定 LLM 在 N 个 skill 中会不会「选中」你。

### 3.1 黄金公式

```
description = 触发场景(when) + 能力概述(what) + 一句话边界(when not)
```

### 3.2 正反例对比

```yaml
# ❌ 反例 1:太抽象,模型无法判断何时用
description: 处理 PDF

# ✅ 正例:写清触发场景 + 能力 + 边界
description: |
  Use this skill when the user wants to extract text, tables, or images
  from PDF documents (scanned or digital). Do NOT use for: 
  PDF generation, PDF merging, or password-protected PDFs (use pdf-toolkit instead).
```

### 3.3 实战 checklist

- [ ] **第一句话直接给出触发场景**:用 `Use this skill when...` / `Use when...` 开头,模型对这种 pattern 训练充分
- [ ] **包含至少 2 个具体的用户输入示例**:模型擅长模式匹配,显式示例 > 抽象描述
- [ ] **明确边界 / 反例**:用 `Do NOT use for: ...` 或 `For ... use X instead` 防止误触发
- [ ] **避免与别的 skill 描述重叠**:重叠会显著降低选择准确度
- [ ] **长度 1–3 句**:超过 3 句会被模型「注意力稀释」,反而看不清核心

### 3.4 来自 Strands 实战的 description

```python
@dataclass
class Skill:
    name: str
    description: str   # ← 这一行直接进 system prompt 的 <description> 字段
    instructions: str = ""
```

实际拼进 system prompt 的样子(`strands.vended_plugins.skills.agent_skills.AgentSkills._generate_skills_xml`):

```xml
<available_skills>
  <skill>
    <name>pdf-extract</name>
    <description>Use when extracting text, tables, or images from PDFs.</description>
    <location>/abs/path/skills/pdf-extract/SKILL.md</location>
  </skill>
  ...
</available_skills>
```

> ⚠️ 注意 Strands 会用 `xml.sax.saxutils.escape` 转义 description,**不要在 description 里写未闭合的 `<>` 或 `&`,会被原样转义掉**。

---

## 🧬 四、input_schema:决定模型「参数填得对不对」

### 4.1 Strands 的两条路径(关键差异)

```
┌─────────────────────────────────────────────────────────┐
│ 路径 A:@tool 装饰器                                      │
│  - 从 Python 函数 docstring + type hints 自动提取          │
│  - 用 Pydantic 生成 JSON Schema                          │
│  - 参数约束:type hint 自动转,description 从 docstring 抓   │
├─────────────────────────────────────────────────────────┤
│ 路径 B:Skill / SKILL.md                                  │
│  - description 触发,instructions 告诉模型怎么填参数        │
│  - 参数 schema 写在 instructions 的 markdown 里(非强制)     │
│  - 没有 JSON Schema 校验,只能靠 description 约束          │
└─────────────────────────────────────────────────────────┘
```

**关键差异**:路径 A 有 schema 强校验,模型填错会被运行时拒收;路径 B 是「软约束」,完全靠模型自觉。

### 4.2 @tool 装饰器的最佳实践(路径 A)

```python
from strands import tool
from typing import Annotated
from pydantic import Field

@tool
def search_documents(
    query: Annotated[
        str,
        Field(description="用户搜索关键词,2-50 字符,支持中英文"),
    ],
    top_k: Annotated[
        int,
        Field(description="返回结果数量,1-20,默认 5", ge=1, le=20),
    ] = 5,
    language: Annotated[
        str,
        Field(description="文档语言:zh/en/auto,默认 auto"),
    ] = "auto",
) -> dict:
    """搜索内部知识库并返回匹配的文档片段。

    Use this tool when the user wants to find information from internal
    documents, FAQs, or knowledge base. NOT for: web search, code search
    (use grep_search instead), or recent news (use web_search).

    Args:
        query: 搜索关键词
        top_k: 返回 Top-K 结果
        language: 文档语言

    Returns:
        包含 results 列表的字典,每项含 title/snippet/score
    """
    ...
```

要点:

| 要点 | 体现 |
|---|---|
| **函数 docstring 第一句 = description** | `搜索内部知识库并返回匹配的文档片段` |
| **完整 docstring 包含触发条件 + 反例** | `Use this tool when...` + `NOT for: ...` |
| **每个参数都用 `Annotated[T, Field(description=...)]`** | 比单独 docstring 更稳 |
| **参数约束用 Pydantic Field()** | `ge=1, le=20`,运行时自动校验 |
| **默认值写在签名里** | `top_k: int = 5`,模型看到默认值会显著减少幻觉 |

### 4.3 SKILL.md 的最佳实践(路径 B)

如果用 Claude Code / Strands Skill 模式,**在 instructions 里也写一份「伪 schema」**:

```markdown
## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `query` | string | yes | 用户搜索关键词,2-50 字符,支持中英文 |
| `top_k` | integer | no | 返回结果数量,1-20,默认 5 |
| `language` | string | no | 文档语言:zh/en/auto,默认 auto |

## Output Format

```json
{
  "results": [
    {"title": "...", "snippet": "...", "score": 0.92}
  ]
}
```
```

> 💡 **为什么用表格?**
> - 模型对 markdown 表格的解析准确率高于纯文本
> - Type / Required / Description 三列可以一次说清,减少歧义
> - 把 schema 写在 instructions 里,等于「让模型自己当校验器」,填补路径 B 没有运行时校验的坑

---

## 📜 五、Instructions:让模型「做对」

### 5.1 黄金结构

```markdown
# Skill Name

## When to use   ← 触发条件(与 description 互补,但更详细)
## Goal          ← 最终产出长什么样
## Steps         ← 步骤化流程(1, 2, 3...)
## Parameters    ← 参数说明(伪 schema,见 §4.3)
## Output Format ← 输出格式(防止模型发散)
## Edge cases    ← 异常情况怎么处理
## Example       ← 1-2 个端到端示例
```

### 5.2 三条硬原则

#### 原则 1:**每一步都要有可验证的产出**

```markdown
## Steps

1. 用 Read 工具读取 `<file_path>` 指向的文件
2. 解析文件,提取所有 `def` 开头的函数定义
3. 对每个函数,运行 `python -c "from <module> import <func>"` 验证可导入
4. **校验**:每个函数必须有 docstring;缺失则报错并列出缺失列表
5. 输出 JSON 报告:
   ```json
   {"total": N, "ok": M, "missing_docstring": [...]}
   ```
```

❌ 错误示范:`"检查每个函数是否合格"` —— 模型不知道「合格」是什么标准。

#### 原则 2:**用「禁止 / 必须」代替「建议 / 应该」**

| 弱措辞 | 强措辞 |
|---|---|
| "建议使用 Read 工具" | "**必须**使用 Read 工具,不要用 Bash + cat" |
| "如果可能的话,验证一下" | "**必须**运行验证命令;失败则停止并报告" |
| "输出应该包含文件名" | "输出 JSON 中 `filename` 字段**必须**存在" |

#### 原则 3:**示例要完整(输入 → 思考 → 输出)**

```markdown
## Example

**用户输入**: "帮我审查 src/auth/login.py"

**执行步骤**:
1. Read `src/auth/login.py` → 读取 247 行
2. 找到 3 个 `def login_*()` 函数
3. 验证可导入:✅ 全部通过
4. 检查 docstring:❌ `login_user` 缺失

**输出**:
```json
{
  "file": "src/auth/login.py",
  "total_functions": 3,
  "missing_docstring": ["login_user"]
}
```
```

---

## 🗂️ 六、Metadata 与资源目录:留好拓展点

### 6.1 Frontmatter 完整字段

```yaml
---
name: pdf-extract                   # 必填,见 §2
description: Use when...            # 必填,见 §3
allowed-tools: Read Bash python     # 可选,白名单
license: Apache-2.0                 # 可选,合规
compatibility: python>=3.10         # 可选,环境约束
metadata:                           # 可选,自由拓展
  author: alice
  version: 1.2.0
  tags: [pdf, etl]
---
```

### 6.2 资源目录约定(Strands Skill 实现)

```
pdf-extract/
├── SKILL.md              # 必填,主文件
├── scripts/              # 可执行脚本(被 Read 后 Bash 执行)
│   └── extract.py
├── references/           # 参考文档(被 Read 后塞上下文)
│   └── pdf-spec.md
└── assets/              # 静态资源(图片、模板等)
    └── template.json
```

Strands 会扫描这三个目录,把前 20 个文件路径列在 skill 激活后的响应里(避免 context 爆炸):

```python
_DEFAULT_MAX_RESOURCE_FILES = 20
_MAX_RESOURCE_DEPTH = 3
_RESOURCE_DIRS = ("scripts", "references", "assets")
```

> 💡 **资源 > 长 instructions**:把大块内容放 references/ 让模型按需 Read,instructions 只保留流程骨架,可以显著减少不必要的 token 消耗。

---

## 🚨 七、常见反模式与避坑

| 反模式 | 问题 | 修复 |
|---|---|---|
| **description 只写「做什么」不写「何时用」** | 模型在多个 skill 中随机选 | 改写为 `Use this skill when...` + 具体场景 |
| **name 与父目录名不一致** | 加载时报警告(Strands strict 模式直接报错) | 强制约束:`name == dir_name` |
| **input 参数用 `dict` / `Any`** | 模型任意填,运行时崩溃 | 用 Pydantic 模型严格定义每个字段 |
| **instructions 写成一大段散文** | 模型抓不住重点,经常漏步 | 用编号步骤 + 校验点 + 输出格式 |
| **允许的工具列表留空** | 模型可能调用任何 tool,出现权限越界 | 显式声明 `allowed-tools`,最小权限原则 |
| **description 中混入 `<` `>` `&`** | Strands 会用 `xml.sax.saxutils.escape` 转义,语义丢失 | 改用自然语言描述,避免特殊符号 |
| **参数 description 缺失** | 模型自由发挥,字段名拼写错误频繁 | 每个参数都用 `Annotated[T, Field(description=...)]` |
| **未声明 Output Format** | 模型返回格式不稳定,下游解析困难 | 在 instructions 里贴 JSON/XML 示例 |

---

## 🧪 八、验证清单(发布前自查)

把这份清单贴到 PR 模板里:

```markdown
## Skill Schema Review Checklist

### Name
- [ ] 1–64 字符,小写字母/数字/连字符
- [ ] 动宾短语,体现作用域
- [ ] 与父目录名完全一致

### Description
- [ ] 以 "Use this skill when..." 开头
- [ ] 包含至少 2 个具体输入示例
- [ ] 明确 Do NOT use 边界
- [ ] 长度 1–3 句,无未转义的 `<>&`
- [ ] 与同目录下其他 skill description 无重叠

### input_schema
- [ ] 每个参数都有 description(或 `Annotated[T, Field(...)]`)
- [ ] 数值参数有 `ge`/`le` 约束
- [ ] 枚举值用 `Literal[...]` 而非 `str`
- [ ] 默认值写在签名里,降低幻觉

### Instructions
- [ ] 包含 When to use / Goal / Steps / Parameters / Output Format / Edge cases
- [ ] 每步有可验证的产出
- [ ] 用「必须/禁止」代替「建议/应该」
- [ ] 至少 1 个端到端示例(输入→思考→输出)

### Metadata & Resources
- [ ] frontmatter 合法 YAML,引号闭合
- [ ] 大块内容放 references/,不放 instructions
- [ ] scripts/ 中的脚本有自描述 docstring
```

---

## 🧩 关键 Takeaway

> **好的 Skill Schema = 让模型「选得对、填得准、做得好」**
>
> 1. **name** 是身份证,**description** 是触发器,**input_schema** 是合同,**instructions** 是 SOP
> 2. 模型在 tool 选择阶段几乎只看 **description**,所以它比 instructions 更重要
> 3. 不要把 Skill 当成「文档」写,要当成「模型的工作手册」写 —— 每一步都要可验证

---

## 🔗 关联文档

- [01_LLM如何理解Skill.md](./01_LLM如何理解Skill.md) — 三步循环(为什么需要 schema)
- [02_Prompt注入机制.md](./02_Prompt注入机制.md) — schema 是怎么进 system prompt 的
- [README.md](./README.md) — 系列索引
