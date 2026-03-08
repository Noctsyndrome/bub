# 第1节教材：架构总览与外部行为

最后更新：2026-03-08

本材料是“第1节：架构总览与外部行为”的一体化学习教材。它不是导读，而是把本节需要掌握的核心内容直接整合进来：

- 原始文档中的关键定义（引用）
- 核心源码中的关键分支（引用）
- 逐段解读与术语解释
- CLI 实验步骤与观察点
- 本节产出模板与自测标准

对应计划索引：`learning/README.md`

## 1. 本节学习目标（完成后应达到）

1. 能解释 Bub 的项目定位与设计目标。
2. 能准确说出 Bub 在 `0.2.3` 中的四条核心行为原则，并说明每条的工程意义。
3. 能区分普通文本、内部命令、shell 命令三类输入路径。
4. 能解释“命令失败回退模型”的设计意图和实现痕迹。
5. 能画出单轮输入处理的最小流程图（不要求细节实现）。

## 2. 项目定位：先理解 Bub 是什么（原文 + 解读）

### 原文摘录（README）

来源：`README.md:10`、`README.md:11`

```text
Bub is a collaborative agent for shared delivery workflows, evolving into a framework that helps other agents operate with the same collaboration model.
It is not a personal-assistant shell: it is designed for shared environments where work must be inspectable, handoff-friendly, and operationally reliable.
```

### 解读

这里有两个关键词需要先固定下来：

- `collaborative agent for shared delivery workflows`：不是个人助理式 shell，而是面向共享交付环境的协作型 agent。
- `inspectable / handoff-friendly / operationally reliable`：可检查、利于交接、运行可靠。这几个词基本定义了你后面看到的所有设计（严格命令边界、显式路由、append-only tape）。

本节你要建立的不是“它能做什么”的功能清单，而是“它为什么要这样设计”的行为模型。

## 3. Bub 的四条核心行为原则：本节主线（原文 + 对照解读）

### 原文摘录（README）

来源：`README.md:18`-`README.md:24`

```text
## What Bub Provides

- Multi-operator collaboration in shared delivery environments.
- Explicit command boundaries for predictable execution.
- Verifiable history (`tape`, `anchor`, `handoff`) for audit and continuity.
- Channel-neutral behavior across CLI and message channels.
- Extensible tools and skills with a unified operator-facing workflow.
```

### 解读总览（本节先抓住四条行为主线）

1. 共享交付协作：Bub 面向多人/多阶段协作，而不是个人 shell 助手。
2. 命令边界严格：只有 `,` 开头才是命令，减少误执行。
3. 会话历史可验证：用 tape / anchor / handoff 保证可审计与可恢复。
4. CLI 与消息渠道共享一套行为边界：渠道不同，核心语义一致。

下面的展开会继续把这些原则落到你真正要掌握的运行语义上，尤其是严格命令边界、共享路由语义、失败命令回退模型和 append-only tape。

## 4. 第一条：严格命令边界（原文、代码、行为）

### 4.1 原文证据（README）

来源：`README.md:15`

```text
1. Command boundary is strict: only lines starting with `,` are treated as commands.
```

### 4.2 源码证据（InputRouter）

来源：`src/bub/core/router.py:212`-`src/bub/core/router.py:215`

```python
def _parse_comma_prefixed_command(self, stripped: str) -> DetectedCommand | None:
    if not stripped.startswith(","):
        return None
    body = stripped[1:].lstrip()
```

### 4.3 解读

这段代码把“命令识别”做成了一个非常明确的入口条件：

- 不以 `,` 开头：直接不是命令（返回 `None`）
- 以 `,` 开头：才继续进入内部命令 / shell 命令解析

这意味着 Bub 把“执行权限”的触发条件从“模型猜测意图”转移到“用户显式语法标记”。工程上这是非常重要的边界控制。

### 4.4 README 中的交互示例（行为层）

来源：`README.md:42`-`README.md:47`

```text
- `hello`: natural language routed to model.
- `,help`: internal command.
- `,git status`: shell command.
- `, ls -la`: shell command (space after comma is optional).
```

### 4.5 本条你应形成的结论

- “是否进入命令路径”首先由前缀语法决定，而不是由模型或内容语义决定。
- `,` 后可以是内部命令，也可以是 shell 命令。
- 这是一条安全边界，同时也是行为可预测性的基础。

## 5. 第二条：用户输入与助手输出使用相同路由语义（文档、代码、意义）

### 5.1 原文证据（README）

来源：`README.md:16`

```text
2. The same routing model is applied to both user input and assistant output.
```

### 5.2 原文证据（架构文档）

来源：`docs/architecture.md:7`-`docs/architecture.md:10`

```text
1. One session, one append-only tape.
2. Same routing rules for user input and assistant output.
3. Command execution and model reasoning are explicit layers.
4. Phase transitions are represented by `anchor/handoff`, not hidden state jumps.
```

### 5.3 源码证据（同一个类里同时提供两条路由）

来源：`src/bub/core/router.py:66`、`src/bub/core/router.py:106`

```python
async def route_user(self, raw: str) -> UserRouteResult:
    ...

async def route_assistant(self, raw: str) -> AssistantRouteResult:
    ...
```

### 5.4 解读

注意这里不是说“实现完全相同”，而是说“规则语义一致”：

- 用户输入会被命令边界规则解析
- 模型输出也会被命令边界规则解析

这样做的价值：

- 减少双轨逻辑（用户一套、模型一套）带来的不可预测行为
- 更容易测试（路由规则只需理解一套）
- 更容易审计（命令执行在统一入口发生）

后面读 `route_assistant` 时你会看到它处理多行文本、代码块、命令块等情况更复杂，但命令识别的底层边界仍然是 `,` 前缀。

## 6. 第三条：成功直接返回，失败回退模型（文档、代码、结构化上下文）

### 6.1 原文证据（README）

来源：`README.md:17`

```text
3. Successful commands return directly; failed commands fall back to the model with structured context.
```

### 6.2 原文证据（架构文档单轮流程）

来源：`docs/architecture.md:29`-`docs/architecture.md:33`

```text
1. `InputRouter.route_user` checks whether input starts with `,`.
2. If command succeeds, return output directly.
3. If command fails, generate a `<command ...>` block for model context.
4. `ModelRunner` gets assistant output.
5. `route_assistant` applies the same command parsing/execution rules.
```

### 6.3 源码证据（成功时不进模型）

来源：`src/bub/core/router.py:74`-`src/bub/core/router.py:96`

```python
result = await self._execute_command(command, origin="human")
if result.status == "ok" and result.name != "bash":
    ...
    return UserRouteResult(
        enter_model=False,
        model_prompt="",
        immediate_output=result.output,
        exit_requested=False,
    )

if result.status == "ok" and result.name == "bash":
    return UserRouteResult(
        enter_model=False,
        model_prompt="",
        immediate_output=result.output,
        exit_requested=False,
    )
```

### 6.4 源码证据（失败时进入模型）

来源：`src/bub/core/router.py:98`-`src/bub/core/router.py:103`

```python
# Failed command falls back to model with command block context.
return UserRouteResult(
    enter_model=True,
    model_prompt=result.block(),
    immediate_output=result.output,
    exit_requested=False,
)
```

### 6.5 源码证据（结构化上下文长什么样）

来源：`src/bub/core/router.py:18`-`src/bub/core/router.py:29`

```python
@dataclass(frozen=True)
class CommandExecutionResult:
    ...
    def block(self) -> str:
        return f'<command name="{self.name}" status="{self.status}">\n{self.output}\n</command>'
```

### 6.6 解读

这部分是 Bub 的关键工程设计之一：

- 成功命令：直接把输出返回给用户，避免无意义地把结果再喂给模型（降低成本与不确定性）。
- 失败命令：不是直接终止，而是把失败信息组织成结构化块交给模型，进入“推理/诊断”阶段。

为什么要结构化（`<command ...>`）而不是纯文本拼接？

- 模型更容易识别“这是一条命令执行结果”
- 保留命令名和状态字段，减少上下文歧义
- 为后续自动化处理（例如多轮命令跟进）保留规范格式

这不是“失败隐藏”，而是“失败上交给推理层处理”。

## 7. 第四条：Tape / Anchor / Handoff 是显式状态机制（文档 + 代码定位）

### 7.1 原文证据（README）

来源：`README.md:18`

```text
4. Session context is append-only tape with explicit `anchor/handoff` transitions.
```

### 7.2 原文证据（架构文档）

来源：`docs/architecture.md:36`-`docs/architecture.md:41`

```text
- Tape is workspace-level JSONL for replay and audit.
- `handoff` writes an anchor with optional `summary` and `next_steps`.
- `anchors` lists phase boundaries.
- `tape.reset` clears active context (optionally archiving first).
```

### 7.3 源码信号（AgentLoop 对结果写入 tape）

来源：`src/bub/core/agent_loop.py:62`-`src/bub/core/agent_loop.py:70`

```python
def _record_result(self, result: ModelTurnResult) -> None:
    self._tape.append_event(
        "loop.result",
        {
            "steps": result.steps,
            "followups": result.command_followups,
            "exit_requested": result.exit_requested,
            "error": result.error,
        },
    )
```

### 7.4 解读

本节不需要你马上读懂 tape 的全部实现，但你应该先建立正确直觉：

- tape 不是“临时聊天上下文”这么简单，它是可回放、可审计的事件轨迹。
- `handoff`/`anchor` 是把阶段边界显式写入上下文，而不是靠模型“记住现在到了哪个阶段”。
- 这类设计非常适合真实工程工作流，因为失败恢复、多人协作、问题追踪都需要可检查轨迹。

第4节会系统展开 tape 体系；第1节只需先抓住它在整体设计中的位置。

## 8. 架构总图：把文档里的“图”翻译成人话

### 原文摘录（架构文档 Runtime Topology）

来源：`docs/architecture.md:14`-`docs/architecture.md:17`

```text
input -> InputRouter -> AgentLoop -> ModelRunner -> InputRouter(assistant output) -> ...
                \-> direct command response
```

### 解读（你可以这样理解）

- `input`：用户输入（可能是普通文本，也可能是 `,` 命令）
- `InputRouter`：先判断是否命令、能否直接执行、是否需要进模型
- `AgentLoop`：控制单轮处理流程与停止条件
- `ModelRunner`：当需要模型时，负责模型回合执行
- `InputRouter(assistant output)`：模型输出也要再过一遍同样的命令规则
- `direct command response`：命令成功时绕过模型，直接回用户

这张图的重点不是组件数量，而是“显式分层”和“可预期分流”。

## 9. 单轮流程：文档步骤与源码分支一一对应

### 9.1 原文摘录（Single Turn Flow）

来源：`docs/architecture.md:27`-`docs/architecture.md:34`

```text
1. `InputRouter.route_user` checks whether input starts with `,`.
2. If command succeeds, return output directly.
3. If command fails, generate a `<command ...>` block for model context.
4. `ModelRunner` gets assistant output.
5. `route_assistant` applies the same command parsing/execution rules.
6. Loop ends on plain final text, explicit quit, or `max_steps`.
```

### 9.2 源码证据（AgentLoop 如何接住 route_user 的结果）

来源：`src/bub/core/agent_loop.py:31`-`src/bub/core/agent_loop.py:60`

```python
async def handle_input(self, raw: str) -> LoopResult:
    with self._tape.fork_tape():
        route = await self._router.route_user(raw)
        if route.exit_requested:
            ...

        if not route.enter_model:
            return LoopResult(
                immediate_output=route.immediate_output,
                assistant_output="",
                exit_requested=False,
                steps=0,
                error=None,
            )

        model_result = await self._model_runner.run(route.model_prompt)
        self._record_result(model_result)
        return LoopResult(...)
```

### 9.3 解读（这是第1节最重要的源码阅读结论）

`AgentLoop.handle_input()` 并不负责“判断命令”本身，它负责根据 `route_user()` 的结果做流程控制：

- `exit_requested=True`：直接结束
- `enter_model=False`：直接返回即时输出（命令成功常见路径）
- `enter_model=True`：把 `model_prompt` 交给 `ModelRunner`

也就是说：

- 路由判断是 `InputRouter` 的职责
- 回合编排是 `AgentLoop` 的职责

这就是架构文档说的“Command execution and model reasoning are explicit layers（执行层与推理层显式分层）”。

## 10. 输入类型到路径的对照表（本节必须掌握）

你可以先用下面这张表记忆，后续再用源码验证细节。

| 输入示例 | 首要判定 | 典型路径 | 是否进入模型 |
|---|---|---|---|
| `hello` | 不以 `,` 开头 | `route_user -> enter_model=True -> ModelRunner` | 是 |
| `,help` | 以 `,` 开头，内部命令 | 执行内部命令成功后直接返回 | 否 |
| `,git status` | 以 `,` 开头，shell 命令 | 执行 shell 成功后直接返回 | 否 |
| `,not-a-command` | 以 `,` 开头，shell 执行失败 | 失败结果封装成 `<command ...>` -> ModelRunner | 是 |

## 11. 实验部分（建议直接照做）

本节目标是“验证行为模型”，不是追求输出内容多漂亮。你要观察的是路径与分流。

### 11.1 实验前准备

```bash
uv sync
uv run bub chat
```

如果你尚未配置模型环境变量（如 `OPENROUTER_API_KEY`），则“进入模型”的实验可能无法完整跑通，但“命令边界”和“命令分流”仍然可以先验证。

### 11.2 实验 A：普通文本进入模型路径

输入：

```text
hello
```

观察点：

- 没有被当作命令执行
- 走自然语言回答路径（需要模型配置可用）

记录模板（示例）：

- 输入：`hello`
- 路径判断：普通文本 -> `route_user` 判定非命令 -> 进入模型
- 结论：`，` 前缀是命令边界

### 11.3 实验 B：内部命令直接返回

输入：

```text
,help
```

观察点：

- 立即返回帮助文本
- 通常不需要模型参与

记录模板（示例）：

- 输入：`,help`
- 路径判断：内部命令成功 -> 直接返回
- 结论：成功命令不进入模型

### 11.4 实验 C：shell 命令直接返回

输入：

```text
,git status
```

观察点：

- 能执行 shell
- 输出是 shell 结果而非模型解释

记录模板（示例）：

- 输入：`,git status`
- 路径判断：shell 命令成功 -> 直接返回
- 结论：shell 命令也适用“成功直接返回”规则

### 11.5 实验 D：失败命令触发回退模型

输入：

```text
,not-a-command
```

观察点：

- 命令执行失败（shell 报错）
- 系统后续可能基于失败信息给出解释或建议（依赖模型配置）

你本节要抓住的是流程，不是具体文案：

- 命令失败 -> 结构化 `command block` -> 进入模型路径

## 12. 本节术语表（建议先背熟）

- `InputRouter`：输入路由器，负责命令识别、执行分流与失败上下文包装
- `AgentLoop`：单轮处理的流程编排器（是否退出、是否进模型、记录结果）
- `ModelRunner`：模型回合执行器（第3节重点）
- `tape`：追加式会话/事件记录（第4节重点）
- `anchor/handoff`：显式阶段边界与阶段切换机制

## 13. 本节产出要求（建议保存在 `learning/experiments/`）

建议新建文件：`learning/experiments/section-1-cli-behavior.md`

建议内容结构：

1. 环境说明（是否配置模型 API）
2. 4 个实验输入与关键输出
3. 每个实验的路径判断（命令路径 / 模型路径 / 失败回退路径）
4. 你对“四条核心行为原则”的复述版本（中文）
5. 一张简化流程图（文字版也可以）

## 14. 本节自测题（能答出来就算过关）

1. 为什么 Bub 要用 `,` 作为命令边界，而不是让模型自己识别“像命令的话”？
2. “用户输入”和“助手输出”共享路由规则的工程价值是什么？
3. 为什么命令失败不是直接终止，而是包装成 `<command ...>` 再交给模型？
4. `AgentLoop` 和 `InputRouter` 的职责边界是什么？
5. tape/anchor/handoff 在第1节层面，你目前应掌握到什么深度？

## 15. 本节常见误区（修正用）

### 误区 A：把 README 的四条当作“宣传语”

修正：这四条几乎都能在 `docs/architecture.md` 和 `src/bub/core/router.py`、`src/bub/core/agent_loop.py` 中找到结构性对应。

### 误区 B：认为“失败回退模型”会降低确定性

修正：确定性来自“失败先被显式执行并结构化记录”，不是来自“失败后完全不做推理”。

### 误区 C：第1节就试图读完整个 `router.py`

修正：第1节只需要抓住关键分支和行为边界。完整细节留到第3节精读。

## 16. 下一节（第2节）你将解决的问题

第2节会把本节的行为模型映射到代码入口，重点回答：

1. `bub chat` / `bub run` / `bub message` 在 `src/bub/cli/app.py` 中如何分工？
2. `build_runtime()` 如何把配置、工具、路由、loop、tape 装配起来？
3. CLI 参数（如 `--workspace`, `--model`, `--max-tokens`）如何影响运行时行为？
