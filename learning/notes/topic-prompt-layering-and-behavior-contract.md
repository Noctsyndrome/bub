# 🧠 专题教材：Bub 的 Prompt 分层与行为约束来源

最后更新：2026-02-24

本专题回答一个非常关键的问题：

> 很多 agent 工具都有一个定义“灵魂”的 prompt 文件，Bub 有吗？

答案是：✅ 有，但不是“单一文件”，而是一个 **分层组合系统**。

你可以把 Bub 的行为来源理解为：

```text
最终系统提示词（System Prompt）
  = 基础系统提示（BUB_SYSTEM_PROMPT）
  + 工作区级提示（AGENTS.md）
  + Bub 内置运行时契约（runtime contract）
  + 工具提示（tool_view + 按需展开）
  + 技能提示（SKILL.md + 按需展开）
```

这也是 Bub 比“只有一个 system prompt 文件”的 agent 更工程化的地方。

---

## 🎯 学习目标（看完你应能回答）

1. Bub 的“灵魂”到底是哪个文件，还是多个来源组合？
2. `AGENTS.md` 在 Bub 中扮演什么角色？
3. `BUB_SYSTEM_PROMPT` 与 `AGENTS.md` 谁先谁后？如何叠加？
4. 为什么 Bub 要把一部分规则写死在 `_runtime_contract()` 里？
5. 工具/技能提示为什么采用“渐进展开（progressive）”而不是全量注入？

---

## 🧱 总览：Bub 的 5 层行为来源

| 层级 | 来源 | 作用 | 可配置性 |
|------|------|------|----------|
| 1 | `BUB_SYSTEM_PROMPT` | 基础行为风格/全局约束 | ✅ 环境变量 |
| 2 | 工作区 `AGENTS.md` | 项目级规则、协作规范、代码风格约束 | ✅ 工作区文件 |
| 3 | `_runtime_contract()` | Bub 运行时硬性规则（工具调用、命令兼容、响应契约） | ❌ 内置代码 |
| 4 | `tool_view` | 工具清单与按需展开 schema | ✅ 由工具注册表驱动 |
| 5 | `skills` (`SKILL.md`) | 可扩展技能说明，支持按需展开 | ✅ 技能文件 |

📌 最像“灵魂文件”的是 `AGENTS.md`，但 Bub 的最终行为并不只由它决定。

---

## 1️⃣ 第一层：`BUB_SYSTEM_PROMPT`（环境变量配置层）

### 📍源码证据：配置项定义

来源：`src/bub/config/settings.py:15`-`src/bub/config/settings.py:18`、`src/bub/config/settings.py:33`

```python
model_config = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    env_prefix="BUB_",
    ...
)

system_prompt: str = ""
```

### ✅ 解读

这说明：

- Bub 会从 `.env` 和环境变量读取以 `BUB_` 开头的配置
- `BUB_SYSTEM_PROMPT` 会映射到 `settings.system_prompt`

也就是说，你完全可以在 `.env` 里定义一个全局基础 prompt，例如：

```env
BUB_SYSTEM_PROMPT=你是一名严格、审慎的代码审查助手，优先给出可验证结论。
```

### 💡适合放在这一层的内容

- 输出风格（简洁 / 详细）
- 通用安全约束
- 团队通用规范（跨项目）
- 模型行为偏好（例如先确认再执行）

不太适合放：

- 当前仓库的具体代码规范（这更适合 `AGENTS.md`）

---

## 2️⃣ 第二层：工作区 `AGENTS.md`（项目级“灵魂文件”）

### 📍源码证据：读取工作区 AGENTS.md

来源：`src/bub/integrations/republic_client.py:13`、`src/bub/integrations/republic_client.py:39`-`src/bub/integrations/republic_client.py:47`

```python
AGENTS_FILE = "AGENTS.md"

def read_workspace_agents_prompt(workspace: Path) -> str:
    """Read workspace AGENTS.md if present."""
    prompt_file = workspace / AGENTS_FILE
    if not prompt_file.is_file():
        return ""
    try:
        return prompt_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
```

### ✅ 解读

这段代码直接证明：

- Bub 会主动读取工作区根目录的 `AGENTS.md`
- 如果文件不存在，不报错，返回空字符串
- 如果存在，它会被作为项目级提示词的一部分注入模型上下文

这和很多 agent 工具里的“项目规则文件”非常类似。

### 🧭 你当前仓库的 `AGENTS.md` 已经在发挥作用

你前面多次看到的这些行为，其实就受 `AGENTS.md` 影响：

- 回答风格（偏工程化、直接）
- 工具使用偏好（优先 `rg`、优先 `apply_patch`）
- 中间进度汇报要求
- 代码编辑约束
- 文件引用格式约束

📌 所以从学习角度说，`AGENTS.md` 确实是你要重点研究的“项目级行为定义文件”。

---

## 3️⃣ Prompt 是如何被拼起来的（真正的组装点）

### 📍源码证据：`AppRuntime` 创建 `ModelRunner` 时注入两类 prompt 来源

来源：`src/bub/app/runtime.py:120`-`src/bub/app/runtime.py:132`

```python
runner = ModelRunner(
    ...
    base_system_prompt=self.settings.system_prompt,
    get_workspace_system_prompt=lambda: read_workspace_agents_prompt(self.workspace),
)
```

### ✅ 解读

这一步非常关键，它把两个来源明确注入给 `ModelRunner`：

- `base_system_prompt`：来自 `BUB_SYSTEM_PROMPT`
- `get_workspace_system_prompt`：来自工作区 `AGENTS.md`

也就是说，**真正负责拼接最终 system prompt 的人是 `ModelRunner`，不是配置模块，也不是 CLI**。

---

## 4️⃣ 第三层：Bub 内置运行时契约（硬编码但非常重要）

### 📍源码证据：`_render_system_prompt()` 的拼接顺序

来源：`src/bub/core/model_runner.py:184`-`src/bub/core/model_runner.py:196`

```python
def _render_system_prompt(self) -> str:
    blocks: list[str] = []
    if self._base_system_prompt:
        blocks.append(self._base_system_prompt)
    if workspace_system_prompt := self._get_workspace_system_prompt():
        blocks.append(workspace_system_prompt)
    blocks.append(_runtime_contract())
    blocks.append(render_tool_prompt_block(self._tool_view))

    compact_skills = render_compact_skills(self._list_skills(), self._expanded_skills)
    if compact_skills:
        blocks.append(compact_skills)
    return "\n\n".join(block for block in blocks if block.strip())
```

### ✅ 解读（重点）

这段代码直接给出了 Bub 的 Prompt 分层顺序（非常值得记住）：

1. `BUB_SYSTEM_PROMPT`
2. 工作区 `AGENTS.md`
3. `_runtime_contract()`（Bub 内置行为法则）
4. 工具提示（`tool_view`）
5. 技能提示（`skills`）

📌 这意味着：

- `AGENTS.md` 很重要，但不会覆盖 Bub 的全部运行时约束
- Bub 会在你的项目规则之后，再追加自己的运行契约
- 工具/技能提示不是静态文件，而是动态渲染结果

### 🧠 为什么运行时契约要放在代码里？

因为这类规则属于“平台级稳定约束”，例如：

- 动作用工具调用，而不是随意输出命令
- 不要伪造 runtime 生成的结构块（如 `<command ...>`）
- 需要时再用 `$name` 请求展开工具/技能详情

这些规则如果完全交给 `AGENTS.md`，不同项目会产生不可控差异。

---

## 5️⃣ `_runtime_contract()` 里到底写了什么（Bub 的行为法则）

### 📍源码证据：运行时契约（节选）

来源：`src/bub/core/model_runner.py:234`-`src/bub/core/model_runner.py:255`

```python
"1) Use tool calls for all actions (file ops, shell, web, tape, skills).\n"
"2) Do not emit comma-prefixed commands in normal flow; use tool calls instead.\n"
"3) If a compatibility fallback is required, runtime can still parse comma commands.\n"
"4) Never emit '<command ...>' blocks yourself; those are runtime-generated.\n"
"5) When enough evidence is collected, return plain natural language answer.\n"
"6) Use '$name' hints to request detail expansion for tools/skills when needed.\n"
```

### ✅ 解读（逐条翻译成人话）

- `1)` 所有动作优先用工具调用完成  
  👉 Bub 的主路径是 tool calls，不是模型输出 shell 命令文本

- `2)` 正常流程不要输出 `,xxx` 命令  
  👉 兼容命令语法存在，但不是推荐主路径

- `3)` 兼容回退场景下 runtime 仍能解析命令  
  👉 说明 Bub 同时支持“现代 tool-call”和“兼容命令语法”

- `4)` 不要伪造 `<command ...>` 块  
  👉 这些结构块属于 runtime 生成的数据结构，不是模型写作文案

- `5)` 证据足够时返回自然语言答案  
  👉 防止无限工具调用，鼓励收敛

- `6)` 用 `$name` 请求工具/技能详情展开  
  👉 这是 Bub 的“渐进提示词”机制入口

📌 这部分是你理解 Bub 与“纯 prompt agent”差异的关键：它不仅靠 prompt，还靠 runtime 语义配合。

---

## 6️⃣ 第四层：工具提示（Tool Prompt）是动态生成的，不是固定文案

### 📍源码证据：工具提示块渲染

来源：`src/bub/tools/view.py:8`-`src/bub/tools/view.py:15`

```python
def render_tool_prompt_block(view: ProgressiveToolView) -> str:
    compact = view.compact_block()
    expanded = view.expanded_block()
    if not expanded:
        return compact
    return f"{compact}\n\n{expanded}"
```

### ✅ 解读

工具提示不是一段写死的文本，而是运行时按当前状态生成：

- 默认给一个紧凑版工具清单（compact）
- 如果某些工具被“点名”或被使用，再追加详细 schema（expanded）

这就避免了：

- 一开始把全部工具 schema 塞进 prompt（太长）
- 模型在无关工具上浪费注意力

---

## 7️⃣ “渐进展开”是怎么实现的（`$name` hint 机制）

### 📍源码证据：`ProgressiveToolView.note_hint()`

来源：`src/bub/tools/progressive.py:28`-`src/bub/tools/progressive.py:38`

```python
def note_hint(self, hint: str) -> bool:
    normalized = hint.casefold()
    for descriptor in self.registry.descriptors():
        model_name = self.registry.to_model_name(descriptor.name)
        if descriptor.name.casefold() != normalized and model_name.casefold() != normalized:
            continue
        self.expanded.add(descriptor.name)
        return True
    return False
```

### 📍源码证据：`compact + expanded` 两段式输出

来源：`src/bub/tools/progressive.py:40`-`src/bub/tools/progressive.py:63`

```python
def compact_block(self) -> str:
    ...

def expanded_block(self) -> str:
    if not self.expanded:
        return ""
    ...
```

### ✅ 解读

当用户或模型在文本里写出 `$fs.read`、`$web.search` 这种 hint 时：

- Bub 会识别 hint
- 在后续 prompt 中展开对应工具的详细说明

这是一种非常实用的 prompt 管理策略：

- 平时保持上下文精简
- 需要时再按工具名精确展开

📌 这也是为什么 Bub 的提示词体系更像“可查询知识库”而不是“一次性大 prompt”。

---

## 8️⃣ 第五层：技能提示（`SKILL.md`）也是动态拼接的

### 📍源码证据：技能提示渲染

来源：`src/bub/skills/view.py:8`-`src/bub/skills/view.py:30`

```python
def render_compact_skills(skills: list[SkillMetadata], expanded_skills: set[str]) -> str:
    ...
    lines = ["<basic_skills>"]
    for skill in skills:
        ...
        lines.append(f"=== [{skill.name}]({skill.location}): {skill.description} ===")
        if skill.name in expanded_skills:
            lines.append(f"{skill.body.rstrip()}\n")
    ...
```

### ✅ 解读

技能系统和工具系统思路类似：

- 默认给出技能名 + 描述 + 位置（紧凑信息）
- 被展开的技能才注入完整 `SKILL.md` 内容

这意味着：

- `SKILL.md` 是技能级“行为脚本/操作指南”
- 但不会每次都把所有技能全文塞进模型上下文

---

## 9️⃣ 用户/模型文本如何触发技能展开（与工具共用 hint 流程）

### 📍源码证据：`ModelRunner._activate_hints()`

来源：`src/bub/core/model_runner.py:198`-`src/bub/core/model_runner.py:207`

```python
def _activate_hints(self, text: str) -> None:
    skill_index = self._build_skill_index()
    for match in HINT_RE.finditer(text):
        hint = match.group(1)
        self._tool_view.note_hint(hint)

        skill = skill_index.get(hint.casefold())
        if skill is None:
            continue
        self._expanded_skills.add(skill.name)
```

### ✅ 解读

这一段很关键，它说明：

- hint 不仅能展开工具，也能展开技能
- hint 来源既可以是用户输入，也可以是模型输出（因为 `_activate_hints` 在多个阶段被调用）

这就是 Bub 文档里提到的：

- `$name` hints progressively expand tool/skill details from either user input or model output

📌 也就是说，Bub 的 prompt 不是静态模板，而是随着对话推进逐步“长出来”的。

---

## 🔬 一个完整心智模型：Bub 的“灵魂”到底在哪里？

如果你要用一句话回答别人：

> Bub 的行为是由哪里定义的？

推荐回答：

> Bub 的行为不是由单一 prompt 文件定义，而是由 `BUB_SYSTEM_PROMPT`、工作区 `AGENTS.md`、内置 runtime contract、动态工具提示、动态技能提示共同组成，并由 `ModelRunner._render_system_prompt()` 在运行时拼接。

这个回答比“看 `AGENTS.md` 就行”更完整，也更符合源码事实。

---

## 🧪 实验建议（适合你现在就做）

### 实验 1：验证 `AGENTS.md` 对行为风格的影响

做法：

1. 在仓库 `AGENTS.md` 里增加一条明显的输出风格约束（例如“先给结论，再给步骤”）
2. 运行 `uv run bub run "请总结当前仓库"` 或进入 `bub chat`
3. 观察输出风格是否明显变化

观察点：

- 变化的是“表达与约束”
- 不变的是 Bub 内置运行时契约（例如工具调用优先策略）

### 实验 2：验证 `$name` hint 的渐进展开

做法（示例）：

```text
$fs.read 请读取 README.md 的前 20 行并总结
```

观察点：

- 模型更容易正确使用对应工具
- 与不加 hint 相比，工具选择与参数质量可能更稳定

### 实验 3：验证 `BUB_SYSTEM_PROMPT` 是“基础层”而不是唯一层

做法：

1. 在 `.env` 设置 `BUB_SYSTEM_PROMPT`
2. 保留工作区 `AGENTS.md`
3. 执行同一条任务

观察点：

- 两者会叠加，而不是二选一
- Bub 内置契约仍然存在

---

## 📝 建议记录模板（保存到 `learning/notes/`）

建议新建文件：`learning/notes/topic-prompt-layering-lab.md`

记录内容：

1. 你的 `BUB_SYSTEM_PROMPT` 配置（可隐藏敏感信息）
2. `AGENTS.md` 中你认为最影响行为的 3 条规则
3. 一次带 `$name` hint 和一次不带 hint 的对比结果
4. 你对“Bub 的灵魂是分层组合而非单文件”的理解复述

---

## ⚠️ 常见误区

### 误区 1：`AGENTS.md` 就是全部 prompt

❌ 不准确。它很重要，但只是工作区级一层。

### 误区 2：Bub 主要靠 prompt 驱动

❌ 不完整。Bub 是“prompt + runtime 规则 + 工具系统 + 路由系统”共同驱动。

### 误区 3：工具/技能提示每次都全量注入

❌ 实际上是渐进展开，以控制上下文长度与噪音。

---

## ✅ 本专题看完后的过关标准

你如果能回答下面 4 个问题，就说明掌握了：

1. `BUB_SYSTEM_PROMPT`、`AGENTS.md`、`_runtime_contract()` 三者的关系是什么？
2. 最终 system prompt 是在哪里拼接出来的？
3. 为什么 Bub 要设计 `$name` hint 的渐进展开？
4. 为什么说 Bub 的“灵魂”是分层组合，而不是单一文件？

---

## 🚀 下一步建议（与学习计划衔接）

这个专题非常适合作为第2节和第3节之间的桥梁。接下来你进入第3节时，可以重点关注：

- `src/bub/core/model_runner.py`：不仅是模型调用器，也是 prompt 组装器和多步循环控制器
- `src/bub/core/router.py`：runtime 契约中的“命令兼容路径”是如何落地的
