# 第2节教材：CLI 与运行时装配（CLI -> Runtime）

最后更新：2026-02-24

本节目标是把你在第1节建立的“行为模型”映射到实际代码入口，回答这几个问题：

1. `bub chat` / `bub run` / `bub message` / `bub idle` 在 CLI 层如何分工？
2. CLI 参数如何变成 `AppRuntime` 的配置？
3. `build_runtime()` 做了哪些“薄封装”工作，哪些工作交给 `AppRuntime`？
4. `AppRuntime` 如何装配 `TapeService`、工具注册表、`InputRouter`、`ModelRunner`、`AgentLoop`？
5. 会话是如何按 `session_id` 复用/隔离的？

对应计划索引：`learning/README.md`

## 1. 先看文档：CLI 暴露了哪些运行模式

### 原文摘录（`docs/cli.md`）

来源：`docs/cli.md:19`-`docs/cli.md:23`

```text
- `uv run bub run "summarize current repo status"`: one-shot message and exit.
- `uv run bub message`: run enabled message channels (Telegram/Discord).
- `uv run bub idle`: run scheduler only (no interactive CLI).
```

### 解读

这里已经暗示了 Bub 不是只有“聊天模式”，而是至少有四种运行姿态：

- `chat`：交互式终端会话（人主导）
- `run`：单次执行后退出（脚本化/测试友好）
- `message`：渠道常驻（Telegram/Discord）
- `idle`：只跑调度器（常驻但不开放交互）

第2节的重点，就是把这四种模式和 `src/bub/cli/app.py` 的实现一一对上。

## 2. CLI 总入口：Typer 应用与默认子命令

### 源码摘录（`src/bub/cli/app.py`）

来源：`src/bub/cli/app.py:20`、`src/bub/cli/app.py:38`-`src/bub/cli/app.py:42`

```python
app = typer.Typer(name="bub", help="Tape-first coding agent CLI", add_completion=False)

@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        chat()
```

### 解读

这段代码说明了两个事实：

1. Bub 的 CLI 是基于 `Typer` 实现的命令树。
2. 当你直接执行 `uv run bub` 而不带子命令时，会默认进入 `chat()`。

这和 README 的 Quick Start（`uv run bub`）体验是一致的。

## 3. `chat` 命令：交互模式如何接上 Runtime

### 源码摘录（`chat()`）

来源：`src/bub/cli/app.py:44`-`src/bub/cli/app.py:67`

```python
@app.command()
def chat(...):
    """Run interactive CLI."""

    configure_logging(profile="chat")
    resolved_workspace = (workspace.expanduser() if workspace else Path.cwd()).resolve()
    ...
    with build_runtime(
        resolved_workspace, model=model, max_tokens=max_tokens, enable_scheduler=not disable_scheduler
    ) as runtime:
        cli = InteractiveCli(runtime, session_id=session_id)
        asyncio.run(cli.run())
```

### 解读（第2节核心之一）

`chat()` 的职责非常清晰，基本是“装配 + 启动”：

- 解析 CLI 参数（Typer 已完成类型转换）
- 规范化工作目录 `workspace`
- 调用 `build_runtime(...)` 构造运行时
- 创建 `InteractiveCli(runtime, session_id=...)`
- 运行交互循环 `cli.run()`

这里故意没有看到 `InputRouter` / `ModelRunner` / `AgentLoop` 的直接创建，因为它们都被下沉到了 `AppRuntime` 内部。

### 参数到行为映射（`chat`）

你当前阶段重点关注这几个参数：

- `--workspace`：决定工作目录，也是工具执行与 tape 关联的重要边界
- `--model`：覆盖 `.env` 中的默认模型
- `--max-tokens`：影响单次模型调用 token 上限
- `--session-id`：决定使用哪个会话身份（关系到会话复用/tape）
- `--disable-scheduler`：关闭 runtime 内置调度器启动

## 4. `run` 命令：一次性执行模式与受限工具/技能

### 文档摘录（`docs/cli.md`）

来源：`docs/cli.md:63`-`docs/cli.md:69`

```text
uv run bub run ",help"
uv run bub run --tools fs.read,fs.glob --skills friendly-python "inspect Python layout"
uv run bub run --disable-scheduler "quick reasoning task"
```

### 源码摘录（`run()`）

来源：`src/bub/cli/app.py:98`-`src/bub/cli/app.py:144`

```python
@app.command()
def run(
    message: Annotated[str, typer.Argument()],
    ...
    tools: Annotated[list[str] | None, typer.Option("--tools", ...)] = None,
    skills: Annotated[list[str] | None, typer.Option("--skills", ...)] = None,
    ...
) -> None:
    ...
    allowed_tools = _parse_subset(tools)
    allowed_skills = _parse_subset(skills)
    ...
    with build_runtime(
        resolved_workspace,
        model=model,
        max_tokens=max_tokens,
        allowed_tools=allowed_tools,
        allowed_skills=allowed_skills,
        enable_scheduler=not disable_scheduler,
    ) as runtime:
        asyncio.run(_run_once(runtime, session_id, message))
```

### 配套源码摘录（`_parse_subset()`）

来源：`src/bub/cli/app.py:25`-`src/bub/cli/app.py:35`

```python
def _parse_subset(values: list[str] | None) -> set[str] | None:
    if values is None:
        return None
    names: set[str] = set()
    for raw in values:
        for part in raw.split(","):
            name = part.strip()
            if name:
                names.add(name)
    return names or None
```

### 解读

`run` 和 `chat` 的差别不在“核心 agent 能力”，而在“驱动方式”：

- `chat`：`InteractiveCli` 持续接收用户输入
- `run`：通过 `_run_once(...)` 执行一条输入并退出

同时 `run` 额外支持：

- `--tools`：限制允许工具集合
- `--skills`：限制允许技能集合

这是非常适合做“受控实验”和“回归测试”的入口（你后续学习时会常用）。

## 5. `_run_once()`：CLI 如何消费 `LoopResult`

### 源码摘录

来源：`src/bub/cli/app.py:146`-`src/bub/cli/app.py:157`

```python
async def _run_once(runtime: AppRuntime, session_id: str, message: str) -> None:
    async with runtime.graceful_shutdown():
        try:
            result = await runtime.handle_input(session_id, message)
            if result.error:
                ...
            else:
                rich.print(result.assistant_output or result.immediate_output or "")
        except asyncio.CancelledError:
            ...
```

### 解读

这里是第1节和第2节的连接点：

- 第1节你已经知道 `LoopResult` 里有 `assistant_output` / `immediate_output`
- 第2节这里看到 CLI 最终如何展示它们

显示逻辑非常直接：

- 有错误：打印错误
- 无错误：优先显示 `assistant_output`，否则显示 `immediate_output`

这也解释了为什么用户在体验上会感觉“有时像命令行，有时像聊天”：因为最终输出来源可能不同，但 CLI 消费方式统一。

## 6. `message` 命令：渠道常驻模式如何接入同一 Runtime

### 源码摘录（`message()`）

来源：`src/bub/cli/app.py:160`-`src/bub/cli/app.py:183`

```python
@app.command()
def message(...):
    """Run message channels with the same agent loop runtime."""
    ...
    with build_runtime(resolved_workspace, model=model, max_tokens=max_tokens) as runtime:
        runtime.settings.proactive_response = proactive_response
        manager = ChannelManager(runtime)
        asyncio.run(_serve_channels(manager))
```

### 源码摘录（`_serve_channels()`）

来源：`src/bub/cli/app.py:185`-`src/bub/cli/app.py:197`

```python
async def _serve_channels(manager: ChannelManager) -> None:
    task = asyncio.create_task(manager.run())
    try:
        async with manager.runtime.graceful_shutdown() as stop_event:
            task.add_done_callback(lambda t: stop_event.set())
            await stop_event.wait()
    ...
    finally:
        task.cancel()
        ...
```

### 解读

这一段回答了一个关键问题：Bub 的渠道模式不是“另起一套 agent 系统”，而是“同一 Runtime，不同输入源”。

- `ChannelManager` 负责监听渠道消息
- 渠道收到消息后，最终仍然会回到 `runtime.handle_input(session_id, text)`
- 因此和 `chat/run` 在核心引擎层共享同一套 `Router + ModelRunner + AgentLoop + Tape`

这就是 Bub 能同时支持 CLI、Telegram、Discord 且行为语义一致的基础。

## 7. `idle` 命令：只启动调度器的最小常驻形态

### 文档摘录（`docs/cli.md`）

来源：`docs/cli.md:23`

```text
- `uv run bub idle`: run scheduler only (no interactive CLI).
```

### 源码摘录（`idle()`）

来源：`src/bub/cli/app.py:69`-`src/bub/cli/app.py:95`

```python
@app.command()
def idle(...):
    """Start the scheduler only, this is a good option for running a completely autonomous agent."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    ...
    settings = load_settings(resolved_workspace)
    job_store = JSONJobStore(settings.resolve_home() / "jobs.json")
    scheduler = BlockingScheduler(jobstores={"default": job_store})
    try:
        scheduler.start()
    finally:
        logger.info("idle.stop workspace={}", str(resolved_workspace))
```

### 解读

`idle` 并不构造完整的 `AppRuntime`，它只做一件事：启动持久化调度器。

这意味着：

- `idle` 是“运行计划任务”的轻量模式
- 即使没有交互 CLI 或消息渠道，调度任务依然可运行
- Job 存储使用 `JSONJobStore(.../jobs.json)`，重启后仍可恢复

这也是你前面提到“常驻执行能力”的一个实现支点。

## 8. `build_runtime()`：薄封装层（不要高估，也不要忽视）

### 源码摘录（`src/bub/app/bootstrap.py`）

来源：`src/bub/app/bootstrap.py:21`-`src/bub/app/bootstrap.py:48`

```python
def build_runtime(
    workspace: Path,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    allowed_tools: set[str] | None = None,
    allowed_skills: set[str] | None = None,
    enable_scheduler: bool = True,
) -> AppRuntime:
    ...
    settings = load_settings(workspace)
    updates: dict[str, object] = {}
    if model:
        updates["model"] = model
    if max_tokens is not None:
        updates["max_tokens"] = max_tokens
    if updates:
        settings = settings.model_copy(update=updates)
    _runtime = AppRuntime(
        workspace,
        settings,
        allowed_tools=allowed_tools,
        allowed_skills=allowed_skills,
        enable_scheduler=enable_scheduler,
    )
    return _runtime
```

### 解读

`build_runtime()` 做的事其实很克制：

1. 从 `.env` + 环境变量加载基础设置（`load_settings(workspace)`）
2. 用 CLI 参数覆盖部分设置（目前重点是 `model`、`max_tokens`）
3. 把控制项（允许工具/技能、是否启用调度器）传给 `AppRuntime`

换句话说：

- `bootstrap.py` 负责“配置合并 + 实例创建”
- 真正的系统装配逻辑在 `AppRuntime`

## 9. `AppRuntime`：第2节最重要的装配器

### 9.1 `AppRuntime.__init__` 做了什么（全局资源层）

### 源码摘录

来源：`src/bub/app/runtime.py:61`-`src/bub/app/runtime.py:80`

```python
def __init__(..., settings: Settings, ..., enable_scheduler: bool = True) -> None:
    self.workspace = workspace.resolve()
    self.settings = settings
    self._allowed_skills = _normalize_name_set(allowed_skills)
    self._allowed_tools = _normalize_name_set(allowed_tools)
    self._store = build_tape_store(settings, self.workspace)
    self.scheduler = self._default_scheduler()
    self._llm = build_llm(settings, self._store)
    self._sessions: dict[str, SessionRuntime] = {}
    self._active_inputs: set[asyncio.Task[LoopResult]] = set()
    self._enable_scheduler = enable_scheduler
```

### 解读

这里是“全局 runtime”层的资源准备，不是单会话层：

- `workspace`：整个运行时关联的工作区
- `_store`：持久化 tape store（全局共享）
- `scheduler`：调度器实例（可选启动）
- `_llm`：模型客户端（由 settings 构建）
- `_sessions`：会话缓存表（按 `session_id` 延迟创建）

关键理解：

- `AppRuntime` 管理多个 `SessionRuntime`
- 不是每次输入都重新创建一套 session 组件

### 9.2 上下文管理器语义：进入时启动 scheduler，退出时关闭

### 源码摘录

来源：`src/bub/app/runtime.py:85`-`src/bub/app/runtime.py:93`

```python
def __enter__(self) -> AppRuntime:
    if not self.scheduler.running and self._enable_scheduler:
        self.scheduler.start()
    return self

def __exit__(self, exc_type, exc_val, exc_tb) -> None:
    if self.scheduler.running and self._enable_scheduler:
        with suppress(Exception):
            self.scheduler.shutdown()
```

### 解读

这就是为什么 CLI 层统一使用 `with build_runtime(...) as runtime:`：

- 进入 `with`：自动启动调度器（除非禁用）
- 离开 `with`：自动关闭调度器

这是典型的资源生命周期封装，让 `chat` / `run` / `message` 三种模式都能复用相同的启动/清理逻辑。

## 10. `get_session(session_id)`：单会话组件如何被组装出来

这是第2节必须吃透的核心函数。

### 源码摘录（主干）

来源：`src/bub/app/runtime.py:101`-`src/bub/app/runtime.py:136`

```python
def get_session(self, session_id: str) -> SessionRuntime:
    existing = self._sessions.get(session_id)
    if existing is not None:
        return existing

    tape_name = f"{self.settings.tape_name}:{_session_slug(session_id)}"
    tape = TapeService(self._llm, tape_name, store=self._store)
    tape.ensure_bootstrap_anchor()

    registry = ToolRegistry(self._allowed_tools)
    register_builtin_tools(...)
    tool_view = ProgressiveToolView(registry)
    router = InputRouter(registry, tool_view, tape, self.workspace)
    runner = ModelRunner(...)
    loop = AgentLoop(router=router, model_runner=runner, tape=tape)

    runtime = SessionRuntime(...)
    self._sessions[session_id] = runtime
    return runtime
```

### 装配顺序解读（建议背下来）

1. 先看缓存：同一个 `session_id` 已存在就直接复用
2. 创建 `TapeService`（绑定全局 LLM、持久化存储、会话 tape 名称）
3. 确保 bootstrap anchor 存在（初始化会话阶段标记）
4. 创建 `ToolRegistry` 并注册内置工具
5. 创建 `ProgressiveToolView`（给模型/用户渐进展示工具信息）
6. 创建 `InputRouter`
7. 创建 `ModelRunner`
8. 创建 `AgentLoop`
9. 封装成 `SessionRuntime` 并缓存

这条链路实际上把你在第1节看到的架构图落到了代码层。

## 11. `SessionRuntime`：会话级运行时的职责（不是全局 runtime）

### 源码摘录

来源：`src/bub/app/runtime.py:39`-`src/bub/app/runtime.py:56`

```python
@dataclass
class SessionRuntime:
    session_id: str
    loop: AgentLoop
    tape: TapeService
    model_runner: ModelRunner
    tool_view: ProgressiveToolView

    async def handle_input(self, text: str) -> LoopResult:
        return await self.loop.handle_input(text)

    def reset_context(self) -> None:
        self.model_runner.reset_context()
        self.tool_view.reset()
```

### 解读

`SessionRuntime` 是“会话级容器”，把本会话需要长期保留的对象集中起来：

- `loop`
- `tape`
- `model_runner`
- `tool_view`

它和 `AppRuntime` 的分工是：

- `AppRuntime`：全局管理、多会话管理、生命周期管理
- `SessionRuntime`：单会话执行入口与会话级上下文复位

## 12. `handle_input(session_id, text)`：从全局 runtime 跳到会话 runtime

### 源码摘录

来源：`src/bub/app/runtime.py:138`-`src/bub/app/runtime.py:145`

```python
async def handle_input(self, session_id: str, text: str) -> LoopResult:
    session = self.get_session(session_id)
    task = asyncio.create_task(session.handle_input(text))
    self._active_inputs.add(task)
    try:
        return await task
    finally:
        self._active_inputs.discard(task)
```

### 解读

这一层做了两件事：

1. 确保 `session_id` 对应的会话 runtime 已存在（不存在就装配）
2. 跟踪当前进行中的输入任务（`_active_inputs`），为优雅关闭做准备

这解释了为什么 Bub 在 `chat`、`run`、`message` 三种模式下都能使用同一核心入口处理输入。

## 13. 优雅关闭（graceful shutdown）：为什么不是简单 Ctrl+C 就结束

### 源码摘录（主干）

来源：`src/bub/app/runtime.py:165`-`src/bub/app/runtime.py:190`

```python
@contextlib.asynccontextmanager
async def graceful_shutdown(self) -> AsyncGenerator[asyncio.Event, None]:
    stop_event = asyncio.Event()
    ...
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
            ...
    ...
    try:
        yield stop_event
    finally:
        future.cancel()
        cancelled = await self._cancel_active_inputs()
        ...
```

### 解读

`graceful_shutdown()` 的目标不是“拦截信号很酷”，而是确保：

- 收到停止信号时有明确退出路径
- 正在运行的输入任务（模型调用、命令执行）能被取消并回收
- 渠道模式和单次执行模式都能复用同一个关闭机制

这是 Bub 作为工程型 runtime 的一个细节优势。

## 14. 测试如何定义第2节的“可执行规格”

第2节不只是读源码，还要学会从测试理解设计意图。

### 14.1 `chat` 会调用交互运行器

来源：`tests/test_cli_app.py:63`-`tests/test_cli_app.py:85`

解读要点：

- `chat --workspace ...` 会调用 `build_runtime(...)`
- 会创建 `InteractiveCli`
- 最终执行 `run()`

这说明 `chat` 的职责确实是“拼装与启动”，不是在 CLI 层实现 agent 逻辑。

### 14.2 `run` 会正确传递 `--tools/--skills`

来源：`tests/test_cli_app.py:127`-`tests/test_cli_app.py:180`

解读要点：

- `--tools` 支持重复传入 + 逗号分隔
- `--skills` 同理
- CLI 最终传给 `build_runtime(...)` 的是去重后的集合

这正是 `_parse_subset()` 的行为规格。

### 14.3 `session_id` 的来源优先级

来源：`tests/test_cli_app.py:183`-`tests/test_cli_app.py:229`

解读要点：

- 默认可从环境变量 `BUB_SESSION_ID` 读取
- 显式 `--session-id` 会覆盖环境变量

这和“会话复用 / tape 复用”的学习非常相关，因为 `session_id` 直接影响会话身份。

### 14.4 runtime 对活动任务取消的保障

来源：`tests/test_runtime_event_loop.py:30`-`tests/test_runtime_event_loop.py:50`

解读要点：

- `_cancel_active_inputs()` 会取消正在运行的任务
- 被取消任务能抛出 `CancelledError`

这说明优雅关闭不仅是日志层行为，而是有实际任务回收保障。

## 15. 本节必须掌握的“层级图”（文字版）

建议你先记这张图，再回头对源码：

```text
CLI(Typer)
  ├─ chat    -> build_runtime -> AppRuntime -> InteractiveCli -> runtime.handle_input(...)
  ├─ run     -> build_runtime -> AppRuntime -> _run_once -> runtime.handle_input(...)
  ├─ message -> build_runtime -> AppRuntime -> ChannelManager -> runtime.handle_input(...)
  └─ idle    -> BlockingScheduler + JSONJobStore（不走 AppRuntime 主装配）
```

## 16. 第2节实践任务（建议你现在就做）

### 任务 A：比较 `chat` 与 `run`

执行：

```bash
uv run bub chat
uv run bub run "hello"
```

记录：

- 哪个是持续交互
- 哪个是单次退出
- 输出来源是否都来自同一 `LoopResult` 语义（命令输出/模型输出）

### 任务 B：验证 `session_id` 对上下文延续的影响

执行：

```bash
uv run bub run --session-id s1 "记住：苹果"
uv run bub run --session-id s1 "我刚刚让你记住了什么？"
uv run bub run --session-id s2 "我刚刚让你记住了什么？"
```

观察点：

- `s1` 与 `s2` 的上下文表现差异（如果模型与 tape 行为正常）
- 这能帮助你建立“会话身份 -> 会话组件/tape”的直觉

### 任务 C：验证 `--disable-scheduler`

执行：

```bash
uv run bub run --disable-scheduler "hello"
```

记录：

- 功能是否仍可正常使用
- 说明调度器不是基本对话路径的前置条件

## 17. 本节笔记模板（建议保存到 `learning/notes/`）

建议新建：`learning/notes/session-<日期>-section-2.md`

建议记录结构：

1. `chat` / `run` / `message` / `idle` 的定位差异（你自己的话）
2. `build_runtime()` 做什么、不做什么
3. `AppRuntime.get_session()` 的装配顺序（按步骤写）
4. `session_id` 对会话复用的影响
5. `graceful_shutdown()` 为什么必要
6. 本节未理解点（留给第3节/第4节）

## 18. 本节自测题（过关标准）

1. 为什么 `build_runtime()` 被称为“薄封装层”？
2. `chat`、`run`、`message` 三个命令共享的核心入口是什么？
3. `idle` 为什么不走完整 `AppRuntime` 装配？
4. `AppRuntime` 与 `SessionRuntime` 的职责边界是什么？
5. `get_session(session_id)` 中哪些对象是“会话级”的，哪些是“全局级”的？

## 19. 下一节预告（第3节）

第3节会深入核心引擎：

- `src/bub/core/router.py`
- `src/bub/core/agent_loop.py`
- `src/bub/core/model_runner.py`

重点回答：

- 一条输入到底如何在路由层、执行层、模型层之间流转？
- 模型为什么能“多步自推进”但仍然有边界（`max_steps`）？
- 失败命令如何通过结构化上下文回到推理循环？
