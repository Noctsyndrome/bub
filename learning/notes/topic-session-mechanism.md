# 专题：Bub Session 机制详解

最后更新：2026-02-24

本文档深入解析 Bub 的 Session 机制，涵盖所有执行模式下的会话隔离、持久化和恢复原理。

**前置知识**：阅读前建议先完成第1节（架构总览）和第2节（CLI 执行模式）。

---

## 📌 核心结论速览

| 问题 | 答案 |
|------|------|
| Session 是什么？ | 独立的对话上下文，包含独立的 Tape、ModelRunner、ToolView |
| Session 如何隔离？ | 通过 `session_id` 字符串区分，每个 ID 对应独立的内存状态和持久化存储 |
| Session 数据存在哪？ | 内存：`AppRuntime._sessions` 字典；磁盘：JSONL Tape 文件 |
| 不同模式 session 有区别吗？ | ✅ 有，chat 模式固定/手动指定，message 模式动态生成 |
| 重启后 session 能恢复吗？ | ✅ 能，Tape 持久化存储，相同 session_id 会自动恢复历史 |

---

## 1️⃣ Session 的核心结构

### 源码：`src/bub/app/runtime.py`

```python
@dataclass
class SessionRuntime:
    """Runtime state for one deterministic session."""

    session_id: str
    loop: AgentLoop
    tape: TapeService
    model_runner: ModelRunner
    tool_view: ProgressiveToolView

    async def handle_input(self, text: str) -> LoopResult:
        return await self.loop.handle_input(text)

    def reset_context(self) -> None:
        """Clear volatile in-memory context while keeping the same session identity."""
        self.model_runner.reset_context()
        self.tool_view.reset()
```

### Session 包含的组件

```
SessionRuntime
├── session_id          # 会话唯一标识（字符串）
├── loop: AgentLoop     # 代理循环（核心处理逻辑）
├── tape: TapeService   # 会话记录（追加式 JSONL）
├── model_runner        # 模型执行器（维护对话上下文）
└── tool_view           # 工具视图（维护工具状态）
```

---

## 2️⃣ Chat 模式下的 Session

### Session ID 来源

```bash
# 默认 session_id = "cli"
uv run bub chat

# 自定义 session_id
uv run bub chat --session-id project-a
```

### 源码：`src/bub/cli/app.py`

```python
@app.command()
def chat(
    session_id: Annotated[str, typer.Option("--session-id", envvar="BUB_SESSION_ID")] = "cli",
    ...
) -> None:
    """Run interactive CLI."""
    with build_runtime(...) as runtime:
        cli = InteractiveCli(runtime, session_id=session_id)
        asyncio.run(cli.run())
```

### Session 创建流程

```python
# src/bub/app/runtime.py:89-108
def get_session(self, session_id: str) -> SessionRuntime:
    existing = self._sessions.get(session_id)
    if existing is not None:
        return existing  # ← 同一个 runtime 内返回已有会话

    # ← 新 session_id 创建全新会话
    tape_name = f"{self.settings.tape_name}:{_session_slug(session_id)}"
    tape = TapeService(self._llm, tape_name, store=self._store)
    tape.ensure_bootstrap_anchor()

    registry = ToolRegistry(self._allowed_tools)
    register_builtin_tools(registry, workspace=self.workspace, tape=tape, runtime=self, session_id=session_id)
    tool_view = ProgressiveToolView(registry)
    router = InputRouter(registry, tool_view, tape, self.workspace)
    runner = ModelRunner(...)
    loop = AgentLoop(router=router, model_runner=runner, tape=tape)
    runtime = SessionRuntime(session_id=session_id, loop=loop, tape=tape, model_runner=runner, tool_view=tool_view)
    self._sessions[session_id] = runtime
    return runtime
```

### Tape 名称生成

```python
# src/bub/app/runtime.py:33-34
def _session_slug(session_id: str) -> str:
    return md5(session_id.encode("utf-8")).hexdigest()[:16]  # noqa: S324
```

| session_id | Tape Name 示例 |
|------------|----------------|
| `cli` | `default:a0b1c2d3e4f5g6h7` |
| `project-a` | `default:1234567890abcdef` |
| `project-b` | `default:fedcba0987654321` |

---

## 3️⃣ Message 模式下的 Session

### Session ID 来源（动态生成）

```python
# src/bub/channels/telegram.py:158-161
async def get_session_prompt(self, message: Message) -> tuple[str, str]:
    chat_id = str(message.chat_id)
    session_id = f"{self.name}:{chat_id}"  # ← 动态生成！
    # 例如："telegram:123456789"
```

### Session ID 格式

| 渠道 | Session ID 格式 | 示例 |
|------|----------------|------|
| **Telegram** | `telegram:{chat_id}` | `telegram:123456789` |
| **Discord** | `discord:{channel_id}` | `discord:987654321` |

### 完整处理流程

```
Telegram 消息
    ↓
ChannelManager._process_input()
    ↓
channel.get_session_prompt(message)
    ↓
session_id = "telegram:{chat_id}"  ← 动态！
    ↓
runtime.handle_input(session_id, prompt)
    ↓
AppRuntime.get_session(session_id)  ← 创建或复用
    ↓
SessionRuntime.handle_input(prompt)
```

### 源码：`src/bub/channels/manager.py`

```python
async def _process_input[T](self, channel: BaseChannel[T], message: T) -> None:
    session_id, _ = await channel.get_session_prompt(message)
    if session_id not in self._session_runners:
        self._session_runners[session_id] = SessionRunner(
            channel,
            session_id,
            self.runtime.settings.message_debounce_seconds,
            self.runtime.settings.message_delay_seconds,
        )
    await self._session_runners[session_id].process_message(message)
```

---

## 4️⃣ SessionRunner（Message 模式特有）

### 作用

`SessionRunner` 是 message 模式特有的中间层，负责：

- 消息去重（debounce）
- 消息延迟处理（delay）
- 连续对话跟踪
- 命令立即执行 vs 自然语言批处理

### 源码：`src/bub/channels/runner.py`

```python
class SessionRunner:
    def __init__(
        self, channel: BaseChannel, session_id: str, debounce_seconds: int, message_delay_seconds: int
    ) -> None:
        self.session_id = session_id
        self.channel = channel
        self.debounce_seconds = debounce_seconds
        self.message_delay_seconds = message_delay_seconds
        self._prompts: list[str] = []
        self._event = asyncio.Event()
        self._timer: asyncio.TimerHandle | None = None
        self._last_received_at: float | None = None
        self._running_task: asyncio.Task[None] | None = None
```

### 消息处理逻辑

```python
async def process_message(self, message: Any) -> None:
    is_mentioned = self.channel.is_mentioned(message)
    _, prompt = await self.channel.get_session_prompt(message)

    if not is_mentioned and self._last_received_at is None:
        # 未提及且无历史：忽略
        logger.info("session.receive ignored session_id={} message={}", self.session_id, prompt)
        return

    self._prompts.append(prompt)

    if prompt.startswith(","):
        # 命令：立即执行
        logger.info("session.receive.command session_id={} message={}", self.session_id, prompt)
        result = await self.channel.run_prompt(self.session_id, prompt)
        await self.channel.process_output(self.session_id, result)

    elif is_mentioned:
        # 被提及：快速回复（最多 1 秒）
        self._last_received_at = now
        self.reset_timer(self.debounce_seconds)
        if self._running_task is None:
            self._running_task = asyncio.create_task(self._run())
        return await self._running_task

    elif self._last_received_at is not None and self._running_task is None:
        # 连续对话：等待更多消息（最多 30 秒）
        self.reset_timer(self.message_delay_seconds)
        self._running_task = asyncio.create_task(self._run())
        return await self._running_task
```

---

## 5️⃣ 架构对比

### Chat 模式架构

```
┌─────────────────────────────────────────────────────────┐
│                   AppRuntime                            │
│  ┌─────────────────────────────────────────────────┐   │
│  │ _sessions: dict[str, SessionRuntime]            │   │
│  │                                                 │   │
│  │  "cli"         → SessionRuntime(tape_cli)       │   │
│  │  "project-a"   → SessionRuntime(tape_project_a) │   │
│  │  "project-b"   → SessionRuntime(tape_project_b) │   │
│  └─────────────────────────────────────────────────┘   │
│                         ↓                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │ TapeStore (持久化 JSONL 文件)                    │   │
│  │  - default_a0b1c2d3...jsonl  ← cli              │   │
│  │  - default_12345678...jsonl  ← project-a        │   │
│  │  - default_fedcba09...jsonl  ← project-b        │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### Message 模式架构

```
┌─────────────────────────────────────────────────────────────┐
│                      ChannelManager                         │
│  ┌───────────────────────────────────────────────────────┐ │
│  │ _session_runners: dict[str, SessionRunner]            │ │
│  │                                                       │ │
│  │  "telegram:123456" → SessionRunner(telegram, ...)     │ │
│  │  "telegram:789012" → SessionRunner(telegram, ...)     │ │
│  │  "discord:111222"  → SessionRunner(discord, ...)      │ │
│  └───────────────────────────────────────────────────────┘ │
│                           ↓                                 │
│  ┌───────────────────────────────────────────────────────┐ │
│  │ AppRuntime._sessions: dict[str, SessionRuntime]       │ │
│  │                                                       │ │
│  │  "telegram:123456" → SessionRuntime(tape_tg_123456)   │ │
│  │  "telegram:789012" → SessionRuntime(tape_tg_789012)   │ │
│  │  "discord:111222"  → SessionRuntime(tape_dc_111222)   │ │
│  └───────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## 6️⃣ 模式对比总结

| 维度 | Chat 模式 | Message 模式 |
|------|-----------|--------------|
| **Session ID 来源** | 命令行参数 `--session-id` | 渠道动态生成（chat_id） |
| **Session 数量** | 通常 1 个（手动指定） | 动态多个（每用户/群组） |
| **Session 管理器** | `AppRuntime._sessions` | `ChannelManager._session_runners` + `AppRuntime._sessions` |
| **隔离粒度** | 按命令行会话 | 按聊天对象（用户/群组） |
| **典型场景** | 单人开发 | 多人 Bot 服务 |
| **额外层** | 无 | SessionRunner（消息去重/延迟） |

---

## 7️⃣ 持久化与恢复

### Tape 存储位置

```bash
# 假设 tape 存储目录（根据配置）
~/.bub/tapes/
  default_a0b1c2d3...jsonl  ← session_id = "cli"
  default_11111111...jsonl  ← session_id = "telegram:123456"
  default_22222222...jsonl  ← session_id = "telegram:789012"
  default_33333333...jsonl  ← session_id = "discord:111222"
```

---

## 8️⃣ 长期活跃渠道的上下文膨胀风险（你提出的关键问题）

### 结论先说 ✅

如果 Discord / Telegram 某个 `channel` 长期活跃，并且持续复用同一个 `session_id`，那么：

- 对应 `session tape` 会持续累积（append-only）
- 后续模型上下文存在变长风险
- 如果不做治理，确实可能触发上下文过长、模型调用失败或性能下降

这不是 Bub 的缺陷，而是长期运行 agent 的共性问题。Bub 的设计思路是：

- **保留完整可审计历史（tape）**
- **提供显式治理工具（handoff / info / reset）**

而不是默认“偷偷自动删历史”。

### 为什么会发生（源码视角）

#### 1) 渠道侧会持续复用相同 `session_id`

来源：`src/bub/channels/manager.py:68`-`src/bub/channels/manager.py:77`

```python
async def _process_input[T](self, channel: BaseChannel[T], message: T) -> None:
    session_id, _ = await channel.get_session_prompt(message)
    if session_id not in self._session_runners:
        self._session_runners[session_id] = SessionRunner(...)
    await self._session_runners[session_id].process_message(message)
```

这意味着同一个 Discord channel（例如 `discord:111222`）会反复进入同一个会话管线。

#### 2) 会话输入最终进入同一个 SessionRuntime / Tape

`SessionRunner` 会持续用同一个 `self.session_id` 调用运行时。

来源：`src/bub/channels/runner.py:54`-`src/bub/channels/runner.py:58`

```python
if prompt.startswith(","):
    ...
    result = await self.channel.run_prompt(self.session_id, prompt)
    await self.channel.process_output(self.session_id, result)
```

自然语言路径同样是基于同一 `session_id` 处理，因此会持续写入同一会话 tape。

#### 3) Tape 本身是 append-only

来源：`src/bub/tape/service.py`

- `TapeService.append_event(...)`
- `TapeService.handoff(...)`
- `TapeService.reset(...)`

设计上就是“追加记录”，不会自动删除旧历史。

---

## 9️⃣ 这会不会导致后续上下文超限？答案是：会有风险，但 Bub 已内建治理路径

### 证据 1：Bub 的 runtime contract 明确承认这个风险

来源：`src/bub/core/model_runner.py:244`-`src/bub/core/model_runner.py:247`

```python
"Excessively long context may cause model call failures. In this case, you SHOULD first use "
"tape.handoff tool to shorten the length of the retrieved history. The current limit is 200k tokens."
```

这段话非常关键，说明 Bub 的作者明确考虑过上下文长度问题。

### 证据 2：Bub 提供了可观测指标 `tape.info`

来源：`src/bub/tools/builtin.py:450`-`src/bub/tools/builtin.py:470`

`tape.info` 会输出：

- `entries`
- `anchors`
- `entries_since_last_anchor`
- `approximate_context_length`

其中 `approximate_context_length` 虽然不是精确 token 数，但非常适合做日常预警指标。

---

## 🔧 推荐治理策略（面向 Discord 长期活跃频道）

### 策略 1：阶段化 handoff（首选）

在一个阶段结束后主动执行：

```text
,tape.handoff name=phase-x summary="已完成xxx" next_steps="下一步做yyy"
```

作用：

- 建立阶段边界（anchor）
- 给后续压缩/检索提供“语义锚点”
- 配合 Bub 的 runtime contract，帮助模型在上下文过长时更合理地收缩历史

✅ 这是 Bub 推荐的“温和治理”方式。  
❌ 不会清空历史，只是让历史更可管理。

### 策略 2：定期检查 `tape.info`（建立预警）

建议在长期运行频道里周期性查看：

```text
,tape.info
```

关注字段：

- `entries_since_last_anchor`：如果持续增长且无阶段切分，说明你需要 handoff
- `approximate_context_length`：快速判断上下文是否明显膨胀

可作为经验阈值（示例，不是硬编码标准）：

- `entries_since_last_anchor` 很高（如数百）时考虑 handoff
- `approximate_context_length` 增长过快时考虑 handoff / reset

### 策略 3：主题切换时 `tape.reset archive=true`（强治理）

如果频道话题已经完全切换，且不需要继续携带旧上下文：

```text
,tape.reset archive=true
```

作用：

- 先归档当前 tape
- 再清空活跃上下文
- 并重新创建 `session/start` anchor

这是最强的上下文治理手段，适合：

- 周期性维护（例如每周清理）
- 从“讨论问题 A”切换到“完全无关的问题 B”
- 已经出现上下文膨胀导致的模型调用不稳定

### 策略 4：按周期/主题拆分 `session_id`（架构级治理）

对特别活跃的 Discord channel，可以考虑不要永远绑定一个 session。

例如按周期拆分：

- `discord:111222:2026w08`
- `discord:111222:2026w09`

或按任务拆分：

- `discord:111222:incident-123`
- `discord:111222:feature-abc`

优点：

- 从源头限制单个 tape 膨胀
- 保留长期服务能力，同时避免单 session 无限增长

这是运营长期 agent 服务时很常见的做法。

---

## 📊 你可以怎么判断“现在该治理了”

| 现象 | 可能原因 | 建议动作 |
|------|----------|----------|
| 回答变慢明显 | 上下文变长、工具调用链增长 | `,tape.info` 检查长度，补 `handoff` |
| 模型调用失败/超时 | 上下文过长或复杂度过高 | 先 `,tape.handoff`，必要时 `,tape.reset archive=true` |
| 经常引用很久之前的无关内容 | 会话历史过长、主题漂移 | 新建阶段 anchor，必要时切换 session_id |
| 长期频道里任务混杂 | 单 session 承担多个主题 | 按主题/周期拆分 session_id |

---

## 🧪 实验建议（建议补进你的学习记录）

### 实验：模拟长期频道上下文膨胀

1. 用同一个 `session_id` 连续进行多轮对话/命令（可用 `bub run --session-id test-long ...`）
2. 定期执行 `,tape.info`
3. 记录 `entries_since_last_anchor` 与 `approximate_context_length` 的变化
4. 执行一次 `,tape.handoff ...`
5. 再观察后续行为是否更稳定/更聚焦

建议记录到：

- `learning/experiments/session-context-growth.md`

---

## 📝 给你的问题的标准化回答（可写入笔记）

> 在 Discord 集成里，如果 channel 长期存在且活跃，对应 session tape 会持续累积，确实可能带来后续上下文超限风险。Bub 的策略不是自动删除历史，而是通过 `tape.info`（观测）、`tape.handoff`（阶段化压缩治理）、`tape.reset archive=true`（强制重置归档）以及必要时拆分 `session_id` 来进行显式管理。

---

### 恢复机制

```python
# src/bub/app/runtime.py:89-92
def get_session(self, session_id: str) -> SessionRuntime:
    existing = self._sessions.get(session_id)
    if existing is not None:
        return existing  # ← 内存中已有，直接返回

    # ← 内存中没有，创建新 SessionRuntime
    # 但 TapeService 会从磁盘加载已有记录
    tape_name = f"{self.settings.tape_name}:{_session_slug(session_id)}"
    tape = TapeService(self._llm, tape_name, store=self._store)
```

### 恢复场景

| 场景 | 结果 |
|------|------|
| 同一进程内切换 session_id | ❌ 新会话（内存隔离） |
| 重启后用相同 session_id | ✅ 恢复（从 tape 加载） |
| 重启后用不同 session_id | ❌ 新会话（tape 独立） |

---

## 8️⃣ 实际场景分析

### 场景 1：多项目开发隔离

```bash
# 项目 A 开发
uv run bub chat --session-id bub-project

# 项目 B 开发
uv run bub chat --session-id my-app

# 两个项目完全隔离，tape 独立
```

### 场景 2：Telegram Bot 多用户

```
用户 A (chat_id: 111) 发送 "hello"
  → session_id = "telegram:111"
  → 创建独立 tape
  → 独立的对话历史

用户 B (chat_id: 222) 发送 "hi"
  → session_id = "telegram:222"
  → 另一个独立 tape
  → 完全隔离，A 看不到 B 的对话
```

### 场景 3：私聊 vs 群聊（同一用户）

```
同一用户 (user_id: 111)：
  私聊：session_id = "telegram:111"
  群组：session_id = "telegram:-1001234"
  → 即使是同一人，在不同场景下也是独立 session！
```

### 场景 4：会话恢复验证

```bash
# 第1次：使用 cli session
uv run bub chat --session-id cli
# 对话内容写入 tape...
# Ctrl+D 退出

# 第2次：重新启动 cli session
uv run bub chat --session-id cli
# ✅ 会从 tape 恢复之前的对话历史！

# 第3次：使用新 session
uv run bub chat --session-id project-a
# ❌ 全新的会话，没有 cli 的记忆
```

---

## 9️⃣ 常用命令

### 查看当前 Session 信息

```bash
# 在 chat 中
,tape.info
```

### 搜索 Session 历史

```bash
# 搜索当前 session 的历史
,tape.search query=error

# 搜索特定 session 需要重新启动该 session
uv run bub chat --session-id project-a
,tape.search query=error
```

### 重置 Session 上下文

```bash
# 清空当前 session 的 tape（可选归档）
,tape.reset archive=true
```

### 查看 Anchors（阶段标记）

```bash
# 查看当前 session 的所有 anchors
,anchors
```

---

## 🔟 实验建议

### 实验 1：验证 Session 隔离

```bash
# Terminal 1
uv run bub chat --session-id test-a
# 输入一些内容，退出

# Terminal 2
uv run bub chat --session-id test-b
,tape.info
# 验证是否是空的
```

### 实验 2：验证 Session 恢复

```bash
# 第1次
uv run bub chat --session-id restore-test
# 输入：hello
# 输入：,tape.info
# 退出

# 第2次
uv run bub chat --session-id restore-test
,tape.info
# 验证是否能看到之前的记录
```

### 实验 3：验证 Message 模式多用户隔离

```bash
# 启动 message 模式
uv run bub message

# 用两个不同的 Telegram 账号发送消息
# 观察 log 中不同的 session_id
```

---

## 1️⃣1️⃣ 常见问题解答

### Q1: 为什么 chat 模式默认 session_id 是 "cli"？

**A**: 这是最简单的默认配置，适合单人开发场景。用户可以根据需要自定义。

### Q2: Session 数据会无限增长吗？

**A**: Tape 是追加式的，会持续增长。可以使用 `,tape.reset archive=true` 归档并清空。

### Q3: 如何在不同 session 之间迁移数据？

**A**: 目前没有内置的迁移工具。可以通过导出 tape 文件手动处理。

### Q4: Message 模式下，如何查看某个用户的会话历史？

**A**: 需要知道该用户的 session_id（如 `telegram:123456`），然后在 tape 文件中搜索。

### Q5: Session 会占用多少内存？

**A**: 每个 SessionRuntime 包含 TapeService、ModelRunner、ToolView 等，具体取决于对话长度和工具数量。长期运行的 message 模式应监控内存使用。

---

## 1️⃣2️⃣ 最佳实践

### 1. 项目隔离

为不同项目使用不同的 session_id：

```bash
uv run bub chat --session-id {project-name}
```

### 2. 定期归档

长期运行的 session 定期归档：

```bash
,tape.reset archive=true
```

### 3. 有意义的命名

使用有意义的 session_id 便于识别：

```bash
# 好
uv run bub chat --session-id bub-learn-2024
uv run bub chat --session-id myapp-dev

# 不好
uv run bub chat --session-id a
uv run bub chat --session-id test
```

### 4. Message 模式监控

长期运行 message 模式时，定期检查活跃 session 数量：

```bash
# 查看 tape 文件数量
ls -la ~/.bub/tapes/ | wc -l
```

---

## 📚 相关源码文件

| 文件 | 内容 |
|------|------|
| `src/bub/app/runtime.py` | AppRuntime、SessionRuntime 定义 |
| `src/bub/cli/app.py` | CLI 入口和 session_id 参数 |
| `src/bub/channels/manager.py` | ChannelManager、_process_input |
| `src/bub/channels/runner.py` | SessionRunner（message 模式特有） |
| `src/bub/channels/telegram.py` | TelegramChannel、get_session_prompt |
| `src/bub/channels/base.py` | BaseChannel 接口 |
| `src/bub/tape/service.py` | TapeService 实现 |

---

## 🔗 相关文档

- `learning/notes/section-1-architecture-and-behavior.md` - 第1节：架构总览
- `learning/notes/section-2-cli-execution-modes.md` - 第2节：CLI 执行模式
- `docs/cli.md` - CLI 详细文档
- `docs/architecture.md` - 架构总览
- `docs/Telegram.md` - Telegram 渠道配置

---

## ✅ 自测题

1. Session 的核心组件有哪些？
2. Chat 模式和 Message 模式的 session_id 来源有什么区别？
3. 为什么 Message 模式需要 SessionRunner 这一层？
4. Tape 名称是如何从 session_id 生成的？
5. 重启后用相同 session_id 启动，会话历史能恢复吗？为什么？
6. 同一用户在 Telegram 私聊和群聊中是同一个 session 吗？
7. 如何清空一个 session 的上下文？
8. Session 数据存储在哪些地方（内存 + 磁盘）？

---

**文档版本**: 1.0  
**最后更新**: 2026-02-24  
**作者**: Bub Learning Notes
