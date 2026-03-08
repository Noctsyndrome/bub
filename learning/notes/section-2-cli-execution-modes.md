# 第2节补充教材：CLI 执行模式详解

最后更新：2026-02-24

本文档详细介绍 Bub CLI 的 4 种执行模式及其区别。对应学习计划索引：`learning/README.md` 第2节（CLI 与运行时装配）。

---

## 🚀 Bub CLI 执行模式总览

| 模式 | 命令 | 用途 | 交互性 | 持久性 |
|------|------|------|--------|--------|
| **chat** | `uv run bub chat` | 交互式 CLI | ✅ 多轮对话 | ✅ Tape 持久化 |
| **run** | `uv run bub run "消息"` | 单次执行 | ❌ 一次性 | ✅ Tape 持久化 |
| **message** | `uv run bub message` | 消息渠道服务 | ✅ 被动响应 | ✅ Tape 持久化 |
| **idle** | `uv run bub idle` | 仅调度器 | ❌ 后台运行 | ✅ 任务持久化 |

---

## 1️⃣ chat 模式 - 交互式 CLI

```bash
uv run bub chat [选项]
```

### 特点

| 属性 | 说明 |
|------|------|
| **交互方式** | 终端交互式，多轮对话 |
| **会话 ID** | 默认 `cli`，可自定义 `--session-id` |
| **Tape** | 每个 session_id 独立的追加式记录 |
| **调度器** | 默认启用（可用 `--disable-scheduler` 禁用） |

### 常用选项

```bash
--workspace /path/to/repo    # 工作区目录
--model openrouter:xxx       # 指定模型
--max-tokens 1400            # 最大 token 数
--session-id my-session      # 会话 ID
--disable-scheduler          # 禁用调度器
```

### 适用场景

- 🧑‍💻 开发时与 AI 结对编程
- 🔍 探索性任务（需要多轮对话）
- 📝 学习/实验 Bub 的行为

### 输入规则

| 输入类型 | 示例 | 行为 |
|----------|------|------|
| 普通文本 | `hello` | 发送给模型 |
| 内部命令 | `,help` | 直接执行 |
| Shell 命令 | `,git status` | 执行 shell |
| 失败命令 | `,not-a-command` | 回退到模型 |

### 源码位置

```
src/bub/cli/app.py:44-63  (chat 函数)
```

关键代码片段：
```python
@app.command()
def chat(
    workspace: Annotated[Path | None, typer.Option("--workspace", "-w")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    max_tokens: Annotated[int | None, typer.Option("--max-tokens")] = None,
    session_id: Annotated[str, typer.Option("--session-id", envvar="BUB_SESSION_ID")] = "cli",
    disable_scheduler: Annotated[bool, typer.Option("--disable-scheduler", envvar="BUB_DISABLE_SCHEDULER")] = False,
) -> None:
    """Run interactive CLI."""
    configure_logging(profile="chat")
    resolved_workspace = (workspace.expanduser() if workspace else Path.cwd()).resolve()
    with build_runtime(...) as runtime:
        cli = InteractiveCli(runtime, session_id=session_id)
        asyncio.run(cli.run())
```

---

## 2️⃣ run 模式 - 单次执行

```bash
uv run bub run "消息内容" [选项]
```

### 特点

| 属性 | 说明 |
|------|------|
| **交互方式** | 一次性输入，输出后退出 |
| **会话 ID** | 默认 `cli` |
| **工具限制** | 可用 `--tools` 限制允许的工具 |
| **技能限制** | 可用 `--skills` 限制允许的技能 |

### 常用选项

```bash
--tools fs.read,fs.glob      # 只允许这些工具
--skills friendly-python     # 只允许这些技能
--disable-scheduler          # 禁用调度器
```

### 适用场景

- ⚡ 快速测试某个命令
- 🔧 一次性任务（如 "summarize current repo status"）
- 🧪 CI/CD 中集成 Bub
- 🎯 限制工具权限的场景

### 示例

```bash
# 快速帮助
uv run bub run ",help"

# 限制工具范围
uv run bub run --tools fs.read,fs.glob "inspect Python layout"

# 禁用调度器
uv run bub run --disable-scheduler "quick reasoning task"
```

### 源码位置

```
src/bub/cli/app.py:94-124  (run 函数)
src/bub/cli/app.py:126-142 (_run_once 函数)
```

关键代码片段：
```python
@app.command()
def run(
    message: Annotated[str, typer.Argument()],
    workspace: Annotated[Path | None, typer.Option("--workspace", "-w")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    max_tokens: Annotated[int | None, typer.Option("--max-tokens")] = None,
    session_id: Annotated[str, typer.Option("--session-id", envvar="BUB_SESSION_ID")] = "cli",
    tools: Annotated[list[str] | None, typer.Option("--tools")] = None,
    skills: Annotated[list[str] | None, typer.Option("--skills")] = None,
    disable_scheduler: Annotated[bool, typer.Option("--disable-scheduler")] = False,
) -> None:
    """Run a single message and exit, useful for quick testing or one-off commands."""
    allowed_tools = _parse_subset(tools)
    allowed_skills = _parse_subset(skills)
    with build_runtime(..., allowed_tools=allowed_tools, allowed_skills=allowed_skills) as runtime:
        asyncio.run(_run_once(runtime, session_id, message))
```

---

## 3️⃣ message 模式 - 消息渠道服务

```bash
uv run bub message [选项]
```

### 特点

| 属性 | 说明 |
|------|------|
| **交互方式** | 被动响应外部消息（Telegram/Discord） |
| **会话 ID** | 由渠道决定（如 Telegram 用户 ID） |
| **主动响应** | 可用 `--proactive-response` 启用 |
| **长运行** | 持续监听渠道消息 |

### 常用选项

```bash
--workspace /path/to/repo    # 工作区目录
--model openrouter:xxx       # 指定模型
--proactive-response         # 启用主动响应
```

### 适用场景

- 📱 Telegram Bot 服务
- 💬 Discord Bot 服务
- 🔔 被动响应用户消息
- 🤖 多渠道客服/助手

### 环境变量要求

```bash
# Telegram
BUB_TELEGRAM_ENABLED=true
BUB_TELEGRAM_TOKEN=your_token
BUB_TELEGRAM_ALLOW_FROM=user1,user2

# Discord
BUB_DISCORD_ENABLED=true
BUB_DISCORD_TOKEN=your_token
```

### 源码位置

```
src/bub/cli/app.py:144-167  (message 函数)
src/bub/cli/app.py:169-184  (_serve_channels 函数)
src/bub/channels/manager.py (ChannelManager)
```

关键代码片段：
```python
@app.command()
def message(
    workspace: Annotated[Path | None, typer.Option("--workspace", "-w")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    max_tokens: Annotated[int | None, typer.Option("--max-tokens")] = None,
    proactive_response: Annotated[bool, typer.Option("--proactive-response", envvar="BUB_PROACTIVE_RESPONSE")] = False,
) -> None:
    """Run message channels with the same agent loop runtime."""
    with build_runtime(resolved_workspace, model=model, max_tokens=max_tokens) as runtime:
        runtime.settings.proactive_response = proactive_response
        manager = ChannelManager(runtime)
        asyncio.run(_serve_channels(manager))
```

---

## 4️⃣ idle 模式 - 仅调度器

```bash
uv run bub idle [选项]
```

### 特点

| 属性 | 说明 |
|------|------|
| **交互方式** | 无交互，后台运行 |
| **功能** | 只运行 APScheduler 调度器 |
| **任务存储** | JSON 文件 (`~/.bub/jobs.json`) |
| **长运行** | 持续运行直到停止 |

### 适用场景

- ⏰ 定时任务（cron 风格）
- 🔄 自主代理（autonomous agent）
- 📅 周期性检查/通知

### 源码位置

```
src/bub/cli/app.py:65-87  (idle 函数)
src/bub/app/jobstore.py   (JSONJobStore)
```

关键代码片段：
```python
@app.command()
def idle(
    workspace: Annotated[Path | None, typer.Option("--workspace", "-w")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    max_tokens: Annotated[int | None, typer.Option("--max-tokens")] = None,
) -> None:
    """Start the scheduler only, this is a good option for running a completely autonomous agent."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from bub.app.jobstore import JSONJobStore
    
    settings = load_settings(resolved_workspace)
    job_store = JSONJobStore(settings.resolve_home() / "jobs.json")
    scheduler = BlockingScheduler(jobstores={"default": job_store})
    scheduler.start()
```

---

## 📊 模式对比总结

| 维度 | chat | run | message | idle |
|------|------|-----|---------|------|
| **交互性** | ✅ 多轮 | ❌ 单次 | ✅ 被动 | ❌ 无 |
| **持久会话** | ✅ | ✅ | ✅ | ✅ (任务) |
| **渠道集成** | ❌ | ❌ | ✅ | ❌ |
| **调度器** | ✅ | ✅ | ✅ | ✅ 仅调度器 |
| **典型用途** | 开发/学习 | 测试/CI | Bot 服务 | 定时任务 |

---

## 🎯 选择建议

| 你的需求 | 推荐模式 |
|----------|----------|
| 日常开发、结对编程 | `chat` |
| 快速测试一个命令 | `run` |
| 集成到 CI/CD | `run` |
| 搭建 Telegram/Discord Bot | `message` |
| 定时任务/自主代理 | `idle` + `schedule.add` |

---

## 💡 进阶技巧

### 1. 会话隔离

不同项目使用不同的 session-id 进行隔离：

```bash
# 不同项目用不同 session-id
uv run bub chat --session-id project-a
uv run bub chat --session-id project-b
```

### 2. 工具权限控制

限制 Bub 可使用的工具范围：

```bash
# 只读模式
uv run bub run --tools fs.read,fs.glob "analyze codebase"
```

### 3. 渠道组合

可以同时运行多个模式（在不同终端）：

```bash
# Terminal 1: uv run bub chat
# Terminal 2: uv run bub message
```

### 4. 与调度器配合

在 chat 中添加定时任务，然后用 idle 模式运行：

```bash
# 在 chat 中
,schedule.add --interval 3600 "check logs"

# 然后启动纯调度器
uv run bub idle
```

---

## 🔍 源码阅读指引

### 核心文件

| 文件 | 内容 |
|------|------|
| `src/bub/cli/app.py` | CLI 入口和 4 种模式定义 |
| `src/bub/app/runtime.py` | AppRuntime 构建 |
| `src/bub/app/bootstrap.py` | 运行时装配 |
| `src/bub/channels/manager.py` | 渠道管理（message 模式） |
| `src/bub/app/jobstore.py` | 任务存储（idle 模式） |

### 阅读顺序建议

1. 先读 `src/bub/cli/app.py` 了解 4 种模式的入口
2. 再读 `src/bub/app/bootstrap.py` 了解运行时如何装配
3. 最后读 `src/bub/app/runtime.py` 了解核心运行时逻辑

---

## ✅ 本节自测题

1. `chat` 模式和 `run` 模式的核心区别是什么？
2. 为什么 `run` 模式支持 `--tools` 和 `--skills` 限制，而 `chat` 不支持？
3. `message` 模式需要哪些环境变量才能正常工作？
4. `idle` 模式与其他三种模式的本质区别是什么？
5. 如何在不同项目之间隔离会话上下文？

---

## 📝 实验建议

### 实验 1：对比 chat 和 run

```bash
# chat 模式（交互式）
uv run bub chat
# 输入：,help
# 输入：hello
# 按 Ctrl+D 退出

# run 模式（一次性）
uv run bub run ",help"
uv run bub run "hello"
```

观察点：
- chat 模式保持会话，可以连续对话
- run 模式输出后立即退出

### 实验 2：测试工具限制

```bash
# 限制只能用 fs.read
uv run bub run --tools fs.read "list files in current directory"
```

观察点：
- Bub 只能使用指定的工具
- 尝试使用其他工具时会发生什么

### 实验 3：会话隔离

```bash
# 两个不同会话
uv run bub chat --session-id test-a
# 输入一些内容，退出

uv run bub chat --session-id test-b
# 输入 ,tape.info 查看是否是空的
```

观察点：
- 不同 session-id 的 tape 是独立的

---

## 🔗 相关文档

- `docs/cli.md` - CLI 详细文档
- `docs/architecture.md` - 架构总览
- `docs/Telegram.md` - Telegram 渠道配置
- `docs/discord.md` - Discord 渠道配置
- `learning/README.md` - 学习计划总览

---

## 📌 与第1节的关联

本节内容与第1节的核心概念对应关系：

| 第1节概念 | 本节体现 |
|-----------|----------|
| 命令边界（`,` 前缀） | 所有模式共享相同的路由规则 |
| 路由语义一致 | `chat` 和 `run` 使用相同的 `InputRouter` |
| Tape 持久化 | 所有模式都写入 tape（session_id 隔离） |
| 显式分层 | `build_runtime()` 显式装配各组件 |

---

**下一步**：完成本节后，建议继续第2节的主体内容——阅读 `src/bub/cli/app.py`、`src/bub/app/bootstrap.py` 和 `src/bub/app/runtime.py`，深入理解 CLI 参数如何影响运行时行为。
