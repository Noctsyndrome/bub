# Bub 项目学习计划

最后更新：2026-02-24

本目录用于保存本仓库的学习计划，以及后续产生的读码笔记、实验记录、流程图与阶段总结。

## 目录说明

- `README.md`：学习计划总览与索引
- `notes/`：按模块/主题整理的学习笔记与解读材料
- `experiments/`：CLI 实验、命令追踪、行为复现记录
- `diagrams/`：架构图、流程图、时序图草稿
- `checkpoints/`：阶段总结与自测结果

## 学习目标

1. 理解 Bub 的核心设计：严格命令边界、共享路由语义、命令失败回退模型、tape/anchor/handoff。
2. 能追踪一次用户输入从 CLI 到 `AgentLoop` 再到工具/模型执行的完整路径。
3. 能在不破坏语义的前提下修改代码（工具/命令/渠道）并补充测试。

## 建议周期

- 总周期：4-6 周（可按“节”推进，不强绑定自然周）
- 每天投入：45-90 分钟
- 时间紧张时：优先完成第1节至第3节（核心路径）

## 核心路径（优先阅读）

- 文档：`README.md`, `docs/architecture.md`, `docs/cli.md`, `docs/features.md`
- 入口：`src/bub/cli/app.py`, `src/bub/app/bootstrap.py`, `src/bub/app/runtime.py`
- 核心循环：`src/bub/core/agent_loop.py`, `src/bub/core/router.py`, `src/bub/core/model_runner.py`
- Tape：`src/bub/tape/service.py`, `src/bub/tape/store.py`, `src/bub/tape/anchors.py`, `src/bub/tape/context.py`
- 工具/技能：`src/bub/tools/registry.py`, `src/bub/tools/progressive.py`, `src/bub/skills/loader.py`, `src/bub/skills/view.py`
- 渠道层：`src/bub/channels/manager.py`, `src/bub/channels/telegram.py`, `src/bub/channels/discord.py`

## 学习章节安排

### 第1节：架构总览与外部行为

1. 阅读 `README.md` 与 `docs/architecture.md`
2. 实际运行并尝试：
   - 普通文本：`hello`
   - 内部命令：`,help`
   - shell 命令：`,git status`
   - 故意失败命令：`,not-a-command`
3. 本节产出：
   - 用自己的话解释 README 中的“四件事”
   - 画出一张简化运行流转图
4. 配套解读材料：
   - `learning/notes/section-1-architecture-and-behavior.md`

### 第2节：CLI 与运行时装配（CLI -> Runtime）

1. 阅读 `src/bub/cli/app.py`
2. 阅读 `src/bub/app/bootstrap.py` 与 `src/bub/app/runtime.py`
3. 阅读测试：
   - `tests/test_cli_app.py`
   - `tests/test_cli_interactive.py`
   - `tests/test_runtime_event_loop.py`
   - `tests/test_graceful_shutdown.py`
4. 练习：
   - 对比 `bub run` 与 `bub chat`
   - 建立“CLI 参数 -> runtime 配置 -> 行为变化”对照表
5. 配套解读材料：
   - `learning/notes/section-2-cli-and-runtime-assembly.md`

### 第3节：核心引擎（Router / AgentLoop / ModelRunner）

1. 精读 `src/bub/core/router.py`（优先级最高）
2. 精读 `src/bub/core/agent_loop.py`
3. 精读 `src/bub/core/model_runner.py`
4. 阅读测试：
   - `tests/test_router.py`
   - `tests/test_agent_loop.py`
   - `tests/test_model_runner.py`
   - `tests/test_command_detector.py`
5. 练习：
   - 追踪失败命令的 `<command ...>` 回退路径
   - 追踪普通用户提示词的模型路径

### 第4节：Tape 系统（可恢复性核心）

1. 阅读：
   - `src/bub/tape/service.py`
   - `src/bub/tape/store.py`
   - `src/bub/tape/context.py`
   - `src/bub/tape/anchors.py`
2. 阅读测试：
   - `tests/test_tape_store.py`
   - `tests/test_tape_service.py`
   - `tests/test_tape_context.py`
3. 练习：
   - 使用 `,handoff`, `,anchors`, `,tape.info`, `,tape.search`, `,tape.reset`
   - 说明哪些信息被持久化、为什么这样设计

### 第5节：工具与技能扩展模型

1. 阅读：
   - `src/bub/tools/registry.py`
   - `src/bub/tools/builtin.py`
   - `src/bub/tools/progressive.py`
   - `src/bub/tools/view.py`
   - `src/bub/skills/loader.py`
   - `src/bub/skills/view.py`
2. 阅读测试：
   - `tests/test_tool_registry.py`
   - `tests/test_tools_builtin.py`
   - `tests/test_tools_schedule.py`
   - `tests/test_skills_loader.py`
   - `tests/test_skill_path_expansion.py`
3. 练习：
   - 新增一个小型内部工具/命令
   - 为其补 1-2 个测试

### 第6节：渠道适配与实战贡献

1. 阅读：
   - `src/bub/channels/base.py`
   - `src/bub/channels/manager.py`
   - `src/bub/channels/telegram.py`
   - `src/bub/channels/discord.py`
   - `src/bub/channels/runner.py`
2. 阅读测试：
   - `tests/test_channels.py`
   - `tests/test_channels_proxy.py`
   - `tests/test_telegram_*.py`
   - `tests/test_discord_*.py`
3. 阅读文档：
   - `docs/telegram.md`
   - `docs/discord.md`
   - `docs/deployment.md`
4. 任选一项贡献：
   - 修一个小 bug
   - 补一段缺失文档
   - 增强测试覆盖

## 每日学习节奏（建议）

1. 10 分钟：阅读文档/代码
2. 20 分钟：运行测试并观察行为
3. 20 分钟：写笔记（输入、输出、状态变化）
4. 10-30 分钟：做一个小实验或一次追踪

## 理解检查清单

- 为什么逗号前缀命令检测是关键边界？
- `route_user` 与 `route_assistant` 的核心差异是什么？
- 为什么失败命令要包装为 `<command ...>` 块？
- tape + handoff 在实际工程里解决了什么问题？
- 能否在不改变现有语义的前提下加一个小功能并补测试？

## 常用命令

```bash
uv sync
uv run bub chat
uv run bub run "hello"
uv run pytest -q
uv run pytest -q tests/test_router.py
uv run ruff check .
uv run mypy
```

## 笔记模板（建议）

每读完一个模块，记录：

- 模块职责（输入/输出）
- 依赖关系（谁调用它 / 它调用谁）
- 状态变化与副作用
- 失败处理与边界情况
- 对应测试文件
- 未解决问题 / 后续验证点
