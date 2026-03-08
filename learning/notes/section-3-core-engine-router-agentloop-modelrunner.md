# 第3节教材：核心引擎（Router / AgentLoop / ModelRunner）

最后更新：2026-02-24

本节目标是把你在前两节建立的“外部行为模型 + CLI/Runtime 装配图”进一步下钻到 Bub 的核心执行引擎，回答这些关键问题：

1. 一条输入到底如何在 `InputRouter`、`AgentLoop`、`ModelRunner` 三层之间流转？
2. 为什么 Bub 能做到“命令成功直接返回 / 命令失败回退模型”？
3. 为什么模型输出还要再次经过路由器（`route_assistant`）？
4. `ModelRunner` 的“多步推进”边界是什么（工具 follow-up、命令 follow-up、`max_steps`）？
5. 你的 Discord `message` 模式实验中看到的 `immediate_output / assistant_output` 分流，核心引擎层是如何实现的？

对应计划索引：`learning/README.md`

前置建议（你已完成）：

- `learning/notes/section-1-architecture-and-behavior.md`
- `learning/notes/section-2-cli-and-runtime-assembly.md`
- `learning/notes/topic-prompt-layering-and-behavior-contract.md`
- `learning/notes/topic-session-mechanism.md`
- `learning/experiments/discord-message-mode-debug-and-fix.md`

---

## 1. 本节学习目标（完成后应达到）

1. 能用自己的话解释三层分工：
   - `InputRouter` = 路由 + 命令执行 + 失败上下文封装
   - `AgentLoop` = 单轮编排 + 停止条件 + 结果归并
   - `ModelRunner` = 模型多步循环 + 工具/命令 follow-up + prompt 渲染
2. 能画出“用户输入 -> 路由 -> 模型 -> 助手输出再路由”的单轮流转图（含回环）。
3. 能追踪两条典型路径：
   - `,not-a-command` 的失败命令回退路径
   - 普通文本提示词的模型路径（含 assistant follow-up）
4. 能从测试用例中读出行为规格（而不只靠源码猜）。
5. 能解释 `max_steps`、`TOOL_CONTINUE_PROMPT`、`<command ...>` 三者在“多步但有边界”中的作用。

---

## 2. 第3节在整体架构中的位置（承接第1/2节）

你在第2节已经看到：

- `AppRuntime.get_session()` 负责装配 `InputRouter`、`ModelRunner`、`AgentLoop`
- `SessionRuntime.handle_input()` 最终会调用 `AgentLoop.handle_input(...)`

这意味着第3节不是“新功能”，而是你前两节架构图里最核心的执行路径落地。

可以先记住这一张“最小责任图”：

```text
用户输入
  ↓
InputRouter.route_user
  ├─ 命令成功 → immediate_output（结束）
  ├─ 命令失败 → <command ...> → 交给模型
  └─ 普通文本 → 交给模型
       ↓
ModelRunner.run（多步循环）
  ↓（模型输出）
InputRouter.route_assistant
  ├─ 纯文本 → visible_text（结束）
  ├─ assistant 命令执行 → <command ...> → 再次喂回模型
  └─ quit → exit_requested
       ↓
AgentLoop 汇总为 LoopResult（immediate_output / assistant_output / steps / error）
```

---

## 3. 先看三层的数据契约（不要先陷入实现细节）

### 3.1 `InputRouter` 的输入/输出契约（`router.py`）

来源：`src/bub/core/router.py:19`-`src/bub/core/router.py:43`

关键数据类：

- `CommandExecutionResult`：一次命令执行结果（含 `status`、`output`、`elapsed_ms`）
- `UserRouteResult`：用户输入路由结果（是否进模型、即时输出、是否退出）
- `AssistantRouteResult`：助手输出路由结果（可见文本、下一轮提示词、是否退出）

最关键的方法是：

- `CommandExecutionResult.block()` 会把结果封装成结构化块：`<command name="..." status="...">...</command>`（`src/bub/core/router.py:28`-`src/bub/core/router.py:29`）

这正是第1节里你已经学过的“失败命令回退模型的结构化上下文”。

### 3.2 `AgentLoop` 的单轮结果契约（`agent_loop.py`）

来源：`src/bub/core/agent_loop.py:13`-`src/bub/core/agent_loop.py:20`

`LoopResult` 字段：

- `immediate_output`
- `assistant_output`
- `exit_requested`
- `steps`
- `error`

这就是你在 Discord 实验里间接碰到的核心结构：

- 命令路径通常落在 `immediate_output`
- 模型路径通常落在 `assistant_output`

### 3.3 `ModelRunner` 的单次模型循环结果契约（`model_runner.py`）

来源：`src/bub/core/model_runner.py:25`-`src/bub/core/model_runner.py:33`

`ModelTurnResult` 字段：

- `visible_text`
- `exit_requested`
- `steps`
- `error`
- `command_followups`

这里的 `command_followups` 很重要，它告诉你本次模型回合里发生了多少次“需要继续跟进”的情况（工具执行后继续、assistant 命令执行后继续）。

---

## 4. 第一层：`InputRouter`（本节最高优先级）

## 4.1 `route_user()`：用户输入的三分流

来源：`src/bub/core/router.py:66`-`src/bub/core/router.py:103`

### 源码行为（按顺序）

1. 空输入：直接返回，不进模型（`src/bub/core/router.py:67`-`src/bub/core/router.py:69`）
2. 非逗号命令：作为普通文本进入模型（`src/bub/core/router.py:70`-`src/bub/core/router.py:72`）
3. 逗号命令：执行命令，然后按执行结果分流（`src/bub/core/router.py:74` 起）

### 核心分支（你必须记住）

- 命令成功：`enter_model=False`，直接走 `immediate_output`
- 命令失败：`enter_model=True`，`model_prompt=result.block()`
- `,quit` 特殊情况：返回 `exit_requested=True`（`src/bub/core/router.py:75`-`src/bub/core/router.py:82`）

### 为什么这里是 Bub 的“确定性边界”

第1节你学过“严格命令边界”和“失败回退模型”，`route_user()` 就是这两条规则在代码里的主落点：

- 是否把用户文本当作命令执行，不由模型判断，而由 `,` 前缀 + 解析规则决定
- 命令失败并不终止，而是显式转成结构化失败上下文进入模型层

### 对应测试（先看测试名就能读出规格）

来源：`tests/test_router.py:67`-`tests/test_router.py:120`

- `test_user_internal_command_short_circuits_model`：内部命令成功，不进模型
- `test_user_shell_success_short_circuits_model`：shell 成功，不进模型
- `test_user_shell_failure_falls_back_to_model`：shell 失败，进入模型，带 `<command ...>`
- `test_user_plain_shell_like_text_without_prefix_goes_to_model`：`echo hi`（无逗号）仍是普通文本
- `test_user_non_line_start_comma_text_goes_to_model`：`please run ,echo hi` 不会误执行
- `test_user_dollar_prefix_goes_to_model_as_plain_text`：`$echo hi` 也不是命令边界

这组测试直接强化了第1节的结论：Bub 的命令边界是语法边界，不是语义猜测。

---

## 4.2 `route_assistant()`：为什么模型输出也要再路由一次

来源：`src/bub/core/router.py:106`-`src/bub/core/router.py:180`

这是第3节最容易“想当然”但最关键的地方。

### 先说结论

`route_assistant()` 的作用不是“再做一遍 `route_user`”，而是：

- 解析模型输出中的可见文本 vs 可执行命令
- 执行 assistant 输出里符合规则的命令（兼容路径）
- 把执行结果封装成 `<command ...>`，形成 `next_prompt` 喂回模型
- 保持命令边界仍然是 `,` 前缀（与用户路径一致）

### 为什么这样设计（工程价值）

这对应 README 的第二条“四件事”：

- 用户输入和助手输出共享路由语义

工程意义：

- 行为一致，可预测
- 命令执行入口统一，便于审计（`command` 事件都写 tape）
- 模型可以在兼容路径下“发出命令并根据结果继续推理”，但仍受 runtime 边界约束

### `route_assistant()` 的三个关键状态

来源：`src/bub/core/router.py:107`-`src/bub/core/router.py:112`

- `visible_lines`：最终用户可见的文本
- `command_blocks`：执行命令后产生的 `<command ...>` 块（后续喂回模型）
- fenced 代码块处理状态：
  - `in_fence`
  - `pending_command_lines`
  - `pending_source_lines`

这说明它不仅处理“一行一个命令”，还处理代码块中的多行 shell 场景。

---

## 4.3 `route_assistant()` 的关键分支：普通文本、单行命令、fenced 多行命令

### 4.3.1 普通行（非命令）

来源：`src/bub/core/router.py:155`-`src/bub/core/router.py:158`

- 如果当前行不是逗号前缀命令，加入 `visible_lines`

这意味着模型输出的普通解释文本会直接进入最终 `visible_text`。

### 4.3.2 单行逗号命令

来源：`src/bub/core/router.py:155`-`src/bub/core/router.py:160`

- 检测到命令后调用 `_execute_assistant_command(...)`
- 执行结果追加到 `command_blocks`
- `visible_text` 在存在 `command_blocks` 时会被清空（`src/bub/core/router.py:172`-`src/bub/core/router.py:175`）

这一点很关键：

- Bub 会隐藏“执行阶段聊天内容”，只保留执行后的后续推理结果（如果有）

这能避免用户看到模型在执行阶段产生的中间 chatter。

### 4.3.3 fenced code block 中的多行 shell 命令

来源：`src/bub/core/router.py:119`-`src/bub/core/router.py:153`，`src/bub/core/router.py:187`-`src/bub/core/router.py:210`

这是第3节一个很值得注意的实现细节：

- 在 fenced block 内，如果遇到逗号前缀 shell 命令，会开始收集后续行（形成多行 shell 文本）
- 到 fenced block 结束或遇到下一个命令时，调用 `_flush_pending_assistant_command(...)`
- `_flush_pending_assistant_command(...)` 把多行内容组装成一个 shell `DetectedCommand` 并执行

这使得 assistant 可以输出类似：

```text
```
,echo first
echo second
```
```

然后 Bub 会把它当作一个多行 shell 命令执行（而不是仅执行第一行）。

### 对应测试（非常关键）

来源：`tests/test_router.py:129`-`tests/test_router.py:195`

- `test_assistant_plain_shell_text_is_not_executed`
- `test_assistant_non_line_start_comma_text_is_not_executed`
- `test_assistant_comma_prefixed_shell_command_is_executed`
- `test_assistant_comma_prefixed_shell_failure_still_follows_up`
- `test_assistant_internal_command_with_comma_is_executed`
- `test_assistant_fenced_multiline_comma_command_is_executed`
- `test_assistant_fenced_plain_text_is_not_executed`

这组测试基本就是 `route_assistant()` 的“可执行规格”。

---

## 4.4 `_parse_comma_prefixed_command()`：第1节“严格命令边界”的核心落点

来源：`src/bub/core/router.py:212`-`src/bub/core/router.py:227`

你在第1节已经见过其中最关键的第一行：

- 不以 `,` 开头，直接 `None`

这里第3节补充两个要点：

1. 先尝试内部命令解析（`parse_internal_command`），并检查 registry 是否存在（`src/bub/core/router.py:218`-`src/bub/core/router.py:223`）
2. 否则按 shell 命令解析（`parse_command_words`），返回 `kind="shell"`（`src/bub/core/router.py:224`-`src/bub/core/router.py:227`）

这说明 Bub 的逗号命令路径内部还分成：

- 内部命令（如 `,help`）
- shell 命令（如 `,git status`）

而且这个分流发生在 Router，不在 AgentLoop。

---

## 4.5 `_execute_shell()` / `_execute_internal()`：命令执行层（仍属于 Router）

### shell 命令执行

来源：`src/bub/core/router.py:236`-`src/bub/core/router.py:260`

关键点：

- Router 并不直接起 shell，而是通过工具注册表执行 `bash` 工具（`self._registry.execute("bash", ...)`）
- 会传入：
  - `cmd` = 命令文本
  - `cwd` = 当前 workspace
- 异常被捕获并转成 `status="error"` + 文本错误消息
- 无论成功失败都会写 tape 的 `command` 事件（`src/bub/core/router.py:252`-`src/bub/core/router.py:253`）

这也能帮助你解释 Discord 调试里看到的现象：

- 外部终端虽然是 Windows，但 Bub 的 shell 执行路径是通过 `bash` 工具抽象走的，不是直接“把模型文本扔给 cmd.exe”。

### 内部命令执行

来源：`src/bub/core/router.py:262`-`src/bub/core/router.py:306`

关键点：

- 内部命令会先做 alias 解析（见 `_resolve_internal_name`，`src/bub/core/router.py:308`-`src/bub/core/router.py:314`）
  - `,tool` -> `tool.describe`
  - `,tape` -> `tape.info`
- 解析键值参数（`parse_kv_arguments`）
- 对部分命令做兼容/默认参数注入：
  - `tool.describe` 支持位置参数映射到 `name`
  - `handoff` 会自动补默认 `name`（`src/bub/core/router.py:316`-`src/bub/core/router.py:323`）
- 未知内部命令会返回结构化错误结果（不是抛异常）
- 成功执行后会更新 `tool_view` 的选择状态（非 `help/tools`）

这一层体现了一个关键设计点：

- `InputRouter` 不只是“判路由”，它还承担了命令语义兼容与执行结果规范化。

---

## 4.6 `_record_command()`：Router 为什么是审计入口

来源：`src/bub/core/router.py:325`-`src/bub/core/router.py:345`

所有命令（用户发起和 assistant 发起）最终都会记录为 tape 事件：

- 事件名：`command`
- 字段含义包括：
  - `origin`（`human` / `assistant`）
  - `kind`（`internal` / `shell`）
  - `raw`
  - `name`
  - `status`
  - `elapsed_ms`
  - `output`

这正是 Bub “inspectable / recoverable” 的核心实现痕迹之一。

---

## 5. 第二层：`AgentLoop`（编排层，不做路由判定）

来源：`src/bub/core/agent_loop.py:23`-`src/bub/core/agent_loop.py:70`

## 5.1 `AgentLoop.handle_input()` 的职责边界

你在第1节已经看过它的主流程，这里第3节要更明确地讲“它不做什么”：

- 不解析命令边界（交给 `InputRouter`）
- 不执行命令（仍由 `InputRouter` 调 registry）
- 不拼接 system prompt（交给 `ModelRunner`）
- 不解释模型输出中的命令（交给 `InputRouter.route_assistant`，但这是 `ModelRunner` 在调用）

它只负责：

1. 调 `route_user(raw)` 获取路由结果（`src/bub/core/agent_loop.py:33`）
2. 根据 `exit_requested` / `enter_model` 分支做编排（`src/bub/core/agent_loop.py:34`-`src/bub/core/agent_loop.py:50`）
3. 如需模型，则调 `model_runner.run(...)`（`src/bub/core/agent_loop.py:52`）
4. 汇总成 `LoopResult` 并记录 `loop.result` 事件（`src/bub/core/agent_loop.py:53`-`src/bub/core/agent_loop.py:70`）

## 5.2 为什么 `LoopResult` 同时有 `immediate_output` 和 `assistant_output`

来源：`src/bub/core/agent_loop.py:13`-`src/bub/core/agent_loop.py:20`，`src/bub/core/agent_loop.py:54`-`src/bub/core/agent_loop.py:60`

这是你 Discord 实验里最值得固化的理解：

- 命令失败回退模型时，可能同时存在：
  - `immediate_output`：命令错误文本（原始执行反馈）
  - `assistant_output`：模型根据 `<command ...>` 给出的解释/建议

CLI / 渠道适配层可以根据各自策略决定显示哪个或如何组合。

你实验里观察到“命令型消息能自动回帖、自然语言消息不自动回帖”的差异，本质就是下游适配层消费 `LoopResult` 的策略差异，而不是核心引擎语义不一致。

## 5.3 对应测试：`AgentLoop` 只测编排，不测路由细节

来源：`tests/test_agent_loop.py:41`-`tests/test_agent_loop.py:76`

两个测试已经足够说明设计意图：

- `test_loop_short_circuit_without_model`
- `test_loop_runs_model_when_router_requests`

测试方式也很有代表性：

- 用 `FakeRouter` 和 `FakeRunner` 注入固定结果
- 只验证 Loop 是否按契约组装输出

这说明作者刻意把“路由规则”和“loop 编排”分层测试，避免交叉耦合。

---

## 6. 第三层：`ModelRunner`（模型多步循环 + 提示词渲染 + follow-up 控制）

来源：`src/bub/core/model_runner.py:46`-`src/bub/core/model_runner.py:255`

## 6.1 先看 `run()`：本节第二重点

来源：`src/bub/core/model_runner.py:83`-`src/bub/core/model_runner.py:142`

### `run()` 的总体职责

`ModelRunner.run(prompt)` 不是“一次模型调用”，而是一个受边界约束的多步循环：

- 输入：初始 prompt（可能是普通用户文本，也可能是 `<command ...>`）
- 输出：`ModelTurnResult`
- 中间会处理三类推进：
  1. 工具执行后继续（tool auto follow-up）
  2. assistant 输出命令后继续（router follow-up）
  3. 直到得到最终可见文本或触发边界

### 关键循环条件

来源：`src/bub/core/model_runner.py:87`

- `while state.step < self._max_steps and not state.exit_requested`

这就是 Bub “多步但有边界”的硬限制之一。

### `run()` 的关键阶段（建议按顺序读）

1. 预激活 hint（用户输入中的 `$name`）  
   来源：`src/bub/core/model_runner.py:84`-`src/bub/core/model_runner.py:85`

2. 记录 `loop.step.start` 事件并调用 `_chat(state.prompt)`  
   来源：`src/bub/core/model_runner.py:89`-`src/bub/core/model_runner.py:98`

3. 若 `_chat()` 返回错误，记录 `loop.step.error` 并退出  
   来源：`src/bub/core/model_runner.py:98`-`src/bub/core/model_runner.py:107`

4. 若 `_chat()` 返回 `followup_prompt`（通常来自工具执行），直接继续下一步  
   来源：`src/bub/core/model_runner.py:109`-`src/bub/core/model_runner.py:121`

5. 若拿到 assistant 文本：
   - 空文本则记录 `loop.step.empty`
   - 非空则激活 assistant 文本中的 hint，并进入 `route_assistant()`  
   来源：`src/bub/core/model_runner.py:123`-`src/bub/core/model_runner.py:134`

6. 若到达 `max_steps` 且无其他错误，设置 `max_steps_reached=...`  
   来源：`src/bub/core/model_runner.py:136`-`src/bub/core/model_runner.py:138`

### 你要特别注意的一点

`ModelRunner` 并不直接执行 assistant 命令，它只是：

- 拿到 assistant 文本
- 调 `router.route_assistant(...)`
- 根据 `AssistantRouteResult.next_prompt` 决定是否继续

这保证了命令语义仍然统一收敛在 Router。

---

## 6.2 `_consume_route()`：把 assistant 路由结果合并进本轮状态

来源：`src/bub/core/model_runner.py:148`-`src/bub/core/model_runner.py:161`

职责很集中：

- 把 `route.visible_text` 追加到 `state.visible_parts`
- 把 `route.exit_requested` 合并到状态
- 记录 `loop.step.finish`（含是否有 visible_text / followup）

这里也解释了为什么最终 `ModelTurnResult.visible_text` 可能是多段拼接（`run()` 末尾会 `"\n\n".join(...)`）。

---

## 6.3 `_chat()`：模型调用封装层（不是业务规则层）

来源：`src/bub/core/model_runner.py:163`-`src/bub/core/model_runner.py:182`

关键点：

- 每一步都会重新渲染 `system_prompt`（`_render_system_prompt()`）
- 通过 `self._tape.tape.run_tools_async(...)` 调模型与工具自动执行
- 使用 `asyncio.timeout(...)` 实施超时边界
- 把底层异常统一映射为 `_ChatResult(error=...)`

这层的重点不是“模型怎么回答”，而是：

- 给 `run()` 一个统一的返回结构 `_ChatResult`
- 把“文本 / 工具 follow-up / 错误”三类结果标准化

---

## 6.4 `_ChatResult.from_tool_auto()`：为什么会出现 `TOOL_CONTINUE_PROMPT`

来源：`src/bub/core/model_runner.py:214`-`src/bub/core/model_runner.py:231`

### 关键行为

- 如果 `ToolAutoResult.kind == "text"`：直接返回文本
- 如果 `kind == "tools"`：返回 `followup_prompt=TOOL_CONTINUE_PROMPT`
- 如果存在 `tool_calls` 或 `tool_results`（即使 kind 不直接是 tools）：同样 follow-up

常量来源：`src/bub/core/model_runner.py:22`

- `TOOL_CONTINUE_PROMPT = "Continue the task."`

### 为什么这很重要

这意味着 Bub 不会把工具调用细节原样拼成大段上下文再喂回模型（容易脏、长、难控）；
它采用一个简洁的继续提示词，让模型在 tape/工具自动结果的上下文基础上继续推进。

对应测试（非常关键）：

- `test_model_runner_continues_after_tool_execution`（`tests/test_model_runner.py:142`-`tests/test_model_runner.py:173`）
- `test_model_runner_tool_followup_does_not_inline_tool_payload`（`tests/test_model_runner.py:176` 起）

这组测试明确验证了“工具 follow-up 用常量提示，不内联危险/嘈杂 payload”。

---

## 6.5 `ModelRunner` 为什么是 prompt 分层真正的组装点

来源：`src/bub/core/model_runner.py:184`-`src/bub/core/model_runner.py:196`

你在专题《Prompt 分层与行为约束来源》中已经学过结论，这里第3节把它放回执行路径：

`_render_system_prompt()` 按顺序拼接：

1. `BUB_SYSTEM_PROMPT`（`base_system_prompt`）
2. 工作区 `AGENTS.md`（`get_workspace_system_prompt()`）
3. `_runtime_contract()`
4. 工具提示（`render_tool_prompt_block(self._tool_view)`）
5. 技能提示（`render_compact_skills(...)`）

这说明：

- `ModelRunner` 不只是“模型调用器”，它还是运行时提示词装配器。
- 你在 Discord 实验里通过提示词约束环境、用 `$fs.read $discord` 提示扩展工具/技能，本质上就是在影响 `ModelRunner` 每一步渲染出的系统提示。

---

## 6.6 `_activate_hints()`：你 Discord 调试成功经验的代码落点

来源：`src/bub/core/model_runner.py:198`-`src/bub/core/model_runner.py:209`

### 行为

`_activate_hints(text)` 会扫描文本中的 `$name`：

- 对工具：`self._tool_view.note_hint(hint)`
- 对技能：若命中 skill index，则加入 `self._expanded_skills`

而且它会在两个阶段被调用：

1. 初始用户 prompt（`run()` 开头，`src/bub/core/model_runner.py:85`）
2. assistant 文本输出后（`src/bub/core/model_runner.py:128`）

这就解释了你实验里的经验：

- 用户显式写 `$fs.read $discord` 会提升模型选择正确工具/技能的稳定性
- assistant 后续如果自己提到 `$friendly-python` / `$fs.read`，后续步也能展开更多细节

对应测试：

- `test_model_runner_expands_skill_from_hint`
- `test_model_runner_expands_skill_from_assistant_hint`
- `test_model_runner_expands_tool_from_user_hint`
- `test_model_runner_expands_tool_from_assistant_hint`

来源：`tests/test_model_runner.py:215`-`tests/test_model_runner.py:333`

---

## 6.7 `_runtime_contract()`：第3节里把专题知识落回执行循环

来源：`src/bub/core/model_runner.py:234`-`src/bub/core/model_runner.py:255`

你前面的专题已系统讲过，这里只强调与第3节直接相关的三点：

1. “优先 tool calls，不要正常流输出逗号命令”  
   说明 assistant 走 `route_assistant` 的逗号命令路径主要是兼容层，而非推荐主路径。

2. “不要伪造 `<command ...>` 块”  
   这些块是 Router 执行真实命令后的 runtime 数据结构，不是模型写作文案。

3. “上下文过长优先 `tape.handoff`”  
   这和你 session 专题中对长会话上下文治理的理解完全一致。

另外，`_runtime_contract()` 中还包含渠道响应约束（`response_instruct`）：

- 这与你的 Discord/Telegram message 模式行为直接相关（例如“如需响应，必须向对应 channel 发送消息”）。

---

## 7. 第3节的“完整单轮路径”拆解（两条典型链路）

## 7.1 路径 A：失败命令回退模型（`,not-a-command`）

建议你把这条路径当作第3节第一条主线。

### 逻辑链（按层）

1. 用户输入 `,not-a-command`
2. `InputRouter.route_user()` 识别为 shell 命令（`src/bub/core/router.py:70`, `src/bub/core/router.py:224`-`src/bub/core/router.py:227`）
3. `_execute_shell()` 调 `bash` 工具执行失败（`src/bub/core/router.py:236`-`src/bub/core/router.py:260`）
4. 生成 `CommandExecutionResult`，并写 tape `command` 事件（`src/bub/core/router.py:252`-`src/bub/core/router.py:253`, `src/bub/core/router.py:325`-`src/bub/core/router.py:345`）
5. `route_user()` 返回：
   - `enter_model=True`
   - `model_prompt=result.block()`（`<command ... status="error">`）
   - `immediate_output=<错误文本>`（`src/bub/core/router.py:98`-`src/bub/core/router.py:103`）
6. `AgentLoop.handle_input()` 进入 `ModelRunner.run(route.model_prompt)`（`src/bub/core/agent_loop.py:52`）
7. `ModelRunner` 可能给出解释/修复建议，结果进入 `assistant_output`
8. `AgentLoop` 返回 `LoopResult`，同时包含：
   - `immediate_output`（原始错误）
   - `assistant_output`（模型解释）

### 对应测试（关键证据）

来源：`tests/test_router.py:84`-`tests/test_router.py:90`

测试明确断言：

- 命令失败时 `enter_model is True`
- `result.model_prompt` 中包含 `<command name="bash" status="error">`

---

## 7.2 路径 B：普通文本 -> 模型 -> assistant 输出再路由

建议你把这条路径当作第3节第二条主线。

### 逻辑链（按层）

1. 用户输入普通文本（如 `hello`）
2. `route_user()` 发现不是逗号命令，返回 `enter_model=True` 且 `model_prompt=原文本`（`src/bub/core/router.py:70`-`src/bub/core/router.py:72`）
3. `AgentLoop` 调 `ModelRunner.run(...)`
4. `ModelRunner._chat()` 发起模型调用（`src/bub/core/model_runner.py:163`-`src/bub/core/model_runner.py:174`）
5. 若模型返回文本，`ModelRunner` 调 `router.route_assistant(assistant_text)`（`src/bub/core/model_runner.py:123`-`src/bub/core/model_runner.py:130`）
6. `route_assistant()` 决定：
   - 是纯文本（结束）
   - 还是包含可执行命令（继续 follow-up）
7. `ModelRunner` 根据 `AssistantRouteResult.next_prompt` 决定是否继续下一步（`src/bub/core/model_runner.py:131`-`src/bub/core/model_runner.py:134`）
8. 最终 `AgentLoop` 返回 `assistant_output`

### 对应测试（从测试看语义）

- `test_model_runner_follows_command_context_until_stop`（`tests/test_model_runner.py:111`-`tests/test_model_runner.py:139`）
  - 演示“assistant 第一次输出触发 follow-up，第二次输出收敛结束”
- `test_model_runner_continues_after_tool_execution`（`tests/test_model_runner.py:142`-`tests/test_model_runner.py:173`）
  - 演示工具执行后通过 `TOOL_CONTINUE_PROMPT` 继续推进

---

## 8. 补充：`command_detector.py` 与 Router 的关系（第3节可选延伸）

来源：`src/bub/core/command_detector.py:17`-`src/bub/core/command_detector.py:91`

`command_detector.py` 是一个更通用的“单行命令检测”模块，特点是：

- 支持无逗号 shell 检测（通过 `shutil.which(...)` 与 path-like 规则）
- 支持 `FOO=bar echo hello` 这类 env 前缀命令
- 对补丁文本/长赋值文本做了误判防护

对应测试：`tests/test_command_detector.py:4`-`tests/test_command_detector.py:36`

### 为什么第3节要看它（但优先级低于 Router）

因为你在 `router.py` 里看到的用户命令边界是“严格逗号前缀”，而 `command_detector.py` 的策略更宽。
这有助于你理解：

- Bub 的不同模块可能有不同检测策略
- 第1节强调的“严格命令边界”是 Router 路径的设计选择，不代表所有命令检测工具都必须同样严格

如果你想进一步深挖，可以后续追一下 `command_detector.py` 在哪些位置被使用（不属于第3节主线）。

---

## 9. 测试如何定义第3节的“可执行规格”

本节建议你把测试按模块职责分组阅读，而不是按文件顺序盲读源码。

### 9.1 Router 规格（边界 + 分流 + assistant 兼容执行）

来源：`tests/test_router.py`

你应该从这组测试提炼出以下规格：

- 只有逗号前缀进入命令路径（用户侧）
- 无逗号 shell-like 文本不误执行
- assistant 侧也遵守逗号边界
- assistant fenced 多行 shell 命令可执行
- assistant 执行命令时 `visible_text` 会被压制，转而生成 `next_prompt`

### 9.2 AgentLoop 规格（只编排，不判语义）

来源：`tests/test_agent_loop.py`

提炼出的规格：

- Router 说不进模型，Loop 就不进模型
- Router 说进模型，Loop 才调用 Runner
- Loop 负责把 route/model 结果归并成 `LoopResult`

### 9.3 ModelRunner 规格（多步推进 + 边界 + hint 展开）

来源：`tests/test_model_runner.py`

提炼出的规格：

- 可以跨多步消费 command follow-up
- 工具自动执行后会继续推进，不内联工具 payload
- 用户与 assistant 两侧的 `$hint` 都能触发工具/技能展开
- 技能列表是动态 provider，每次 run 都会刷新（`test_model_runner_refreshes_skills_from_provider_between_runs`）

---

## 10. 与你现有 Discord 实验的直接映射（把“经验”变成“源码理解”）

你的实验记录：`learning/experiments/discord-message-mode-debug-and-fix.md`

### 你已经验证到的点，在第3节的代码落点

1. 命令消息容易回帖，自然语言消息不一定自动回帖  
   对应：
   - `AgentLoop` 结果结构里 `immediate_output` / `assistant_output` 双槽位（`src/bub/core/agent_loop.py:13`-`src/bub/core/agent_loop.py:20`）

2. `$fs.read $discord` 能显著提高技能执行成功率  
   对应：
   - `ModelRunner._activate_hints()`（`src/bub/core/model_runner.py:198`-`src/bub/core/model_runner.py:209`）
   - `ModelRunner._render_system_prompt()` 的工具/技能提示拼接（`src/bub/core/model_runner.py:184`-`src/bub/core/model_runner.py:196`）

3. 需要显式约束“通过技能发消息”，而不是只在终端输出  
   对应：
   - `_runtime_contract()` 中的 `response_instruct`（`src/bub/core/model_runner.py:248`-`src/bub/core/model_runner.py:255`）

### 第3节要把你的实验升级成的能力

不是“会调提示词”，而是：

- 能指出提示词为何有效（`_activate_hints` + prompt 分层）
- 能指出模型为什么会继续推进（tool follow-up / assistant follow-up）
- 能指出最终为什么某些结果出现在 `assistant_output` 而非 `immediate_output`

---

## 11. 第3节建议实操任务（强烈建议边读边做）

本节练习的目标是“追踪路径”，不是追求模型回答质量。

## 11.1 任务 A：追踪失败命令回退路径（主线）

执行（建议 CLI 或 `run` 都可）：

```bash
uv run bub run ",not-a-command"
```

你要记录的不是文案，而是路径：

1. `route_user` 是否识别为命令？
2. `_execute_shell` 成功还是失败？
3. 是否生成 `<command ... status="error">`？
4. `AgentLoop` 是否进入 `ModelRunner`？
5. 最终 `LoopResult` 的两个输出槽位各有什么？

建议保存到：

- `learning/experiments/section-3-command-fallback-trace.md`

## 11.2 任务 B：追踪普通文本的模型路径（含多步）

执行（任选）：

```bash
uv run bub run "hello"
uv run bub run "请总结当前仓库的核心模块"
```

观察点：

- 初始 prompt 是否直接进入模型
- 是否出现多步（`steps > 1`）
- 是否有工具调用后继续推进（如果有）
- 最终是否只在 `assistant_output` 有内容

## 11.3 任务 C：验证 `$hint` 对工具/技能展开的影响（与你的实验衔接）

对比两次输入（可以用 `run`）：

```text
请读取 README 前 20 行并总结
```

```text
$fs.read 请读取 README 前 20 行并总结
```

可选（若继续做 Discord/渠道实验）：

```text
$discord 请通过 discord 技能向当前频道发送一条测试消息
```

记录点：

- 工具选择稳定性是否提升
- 是否更少出现无关 shell 探测

## 11.4 任务 D：有意识触发 `max_steps`（可选进阶）

如果你愿意，可以临时用较小 `max_steps`（需看配置/CLI 支持情况）或构造容易触发多步的任务，观察：

- `ModelRunner` 是否返回 `max_steps_reached=...`
- tape 中是否出现 `loop.max_steps`

本任务是为了强化“多步但有边界”的理解。

---

## 12. 第3节笔记模板（建议保存到 `learning/notes/`）

建议新建：

- `learning/notes/session-<日期>-section-3.md`

建议结构：

1. 三层职责图（`InputRouter / AgentLoop / ModelRunner`）
2. 两条主路径追踪（失败命令回退、普通文本模型路径）
3. `route_user` 与 `route_assistant` 的输入/输出契约对照表
4. `ModelRunner.run()` 的多步循环状态机（你自己的文字版）
5. 与你 Discord 实验的映射（`immediate_output / assistant_output`、`$hint`、`response_instruct`）
6. 当前仍未搞清的点（留给第4节 Tape 或后续实战验证）

---

## 13. 第3节自测题（能答出来基本就过关）

1. `InputRouter.route_user()` 和 `InputRouter.route_assistant()` 的职责差异是什么？
2. 为什么 Bub 要把 assistant 输出再次经过路由器，而不是直接展示？
3. 失败命令为什么要封装成 `<command ...>` 而不是直接拼成自然语言错误文本？
4. `AgentLoop` 为什么说是“编排层”而不是“命令执行层”？
5. `ModelRunner` 中工具执行后的 follow-up 为什么使用 `TOOL_CONTINUE_PROMPT`，而不是内联工具 payload？
6. `max_steps` 解决了什么问题？如果没有它，可能出现什么风险？
7. `$hint` 为什么既在用户 prompt 阶段生效，也在 assistant 输出阶段生效？
8. 你的 Discord 实验里“命令能回帖、自然语言不自动回帖”的现象，如何用 `LoopResult` 的字段解释？

---

## 14. 本节常见误区（修正用）

### 误区 A：`AgentLoop` 是核心逻辑最多的一层

修正：`AgentLoop` 很重要，但主要负责编排；复杂规则主要在 `InputRouter` 和 `ModelRunner`。

### 误区 B：`ModelRunner` 只负责“调用模型 API”

修正：它还负责：

- 多步循环控制
- system prompt 分层渲染
- tool/skill hint 激活
- 错误/超时/`max_steps` 边界处理

### 误区 C：assistant 命令路径是 Bub 的主推荐路径

修正：`_runtime_contract()` 明确推荐优先使用 tool calls；assistant 逗号命令路径是兼容层，但仍需存在以保证共享路由语义。

### 误区 D：看到 `command_detector.py` 就以为 Router 也按同样策略识别 shell 命令

修正：Router 用户侧命令入口仍是严格逗号前缀；`command_detector.py` 是更通用的检测模块，不能替代对 Router 实际行为的理解。

---

## 15. 下一节预告（第4节 Tape 系统）

第3节你已经看到很多 tape 事件写入点，但还没系统展开：

- Router 写 `command`
- ModelRunner 写 `loop.step.start` / `loop.step.finish` / `loop.step.error` / `loop.max_steps`
- AgentLoop 写 `loop.result`

第4节（Tape 系统）会把这些“事件痕迹”串起来，重点回答：

1. 这些事件具体存在哪里（store / JSONL）？
2. `anchor/handoff` 如何影响上下文恢复与压缩？
3. `tape.info` / `tape.search` / `tape.reset` 是如何与这些事件配合的？
4. 长期运行渠道（Telegram/Discord）如何做上下文治理？

这也正好承接你已经完成的 session 专题和 Discord 实战经验。

