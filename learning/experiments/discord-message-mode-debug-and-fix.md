# Discord `message` 模式调试与成功验证记录

最后更新：2026-02-24

## 实验目标

验证 Bub 在 `message` 模式下接入 Discord 后，能否在频道中完成：

1. 命令型回复（`,help`、`,tape.info`）
2. 自然语言对话回复（通过 `discord` 技能发送到频道）

并记录调试过程中的失败现象、原因分析与可复用解决方案。

---

## 环境信息（本次实验）

- 操作系统：Windows 11（外部终端为 `cmd.exe`）
- Bub 运行模式：`uv run bub message`
- 已启用渠道：Discord（`discord.ready` 日志可见）
- 模型：阿里云百炼 `qwen3.5-plus`（OpenAI 兼容方式接入）
- 关键配置：
  - `BUB_DISCORD_ENABLED=true`
  - `BUB_DISCORD_TOKEN=***`（已脱敏）
  - `BUB_DISCORD_ALLOW_FROM=[...]`
  - `BUB_DISCORD_ALLOW_CHANNELS=[...]`

---

## 初始现象

### 现象 A：命令型消息可以在频道正常回复 ✅

示例：

```text
!bub ,help
!bub ,tape.info
```

结果：

- 频道内能收到 Bub 的回复
- 说明 Discord 集成本身、权限配置、allowlist、消息触发条件都正常

### 现象 B：自然语言消息终端有结果，但频道无回复 ❌

示例：

```text
!bub 你好，请回复一句测试信息
```

结果：

- 终端日志中可见模型输出（自然语言结果）
- Discord 频道中没有出现回复消息

---

## 原因分析（关键结论）

### 原因 1：Discord 适配器默认只自动发送 `immediate_output`

命令型输入通常产出 `immediate_output`，所以会被适配器发送到频道。  
自然语言对话通常产出 `assistant_output`，不会被 Discord 适配器自动转发。

因此，自然语言路径下要想在频道真正出现回复，需走项目设计的主路径：

- 模型读取并遵循 `discord` 技能
- 通过 `discord` 技能脚本主动发送消息到频道

### 原因 2：模型在技能执行阶段对 shell 环境判断不稳定（Windows/cmd 与 bash 混用）

终端错误特征（调试过程中观察到）：

- `/bin/bash: line 1: uv: command not found`
- `/bin/bash: line 1: where: command not found`
- `/bin/bash: line 1: python: command not found`
- 模型混用路径风格：`C:\...` 与 `/mnt/c/...`

结论：

- 外部终端虽为 Windows `cmd.exe`
- 但 Bub 的 `bash` 工具实际运行环境是 `bash`（日志中可见 `/bin/bash`）
- 模型在未被明确约束时，会交替尝试 Windows 命令与类 Linux/WSL 路径，导致技能执行失败

---

## 调试过程记录

### 阶段 1：确认 Discord 基础联通性

执行：

```text
!bub ,help
!bub ,tape.info
```

结论：

- Discord 集成和命令路径联通正常
- 问题不在 Bot 在线状态、权限、allowlist、频道触发条件

### 阶段 2：确认 `discord` 技能已被发现

执行：

```text
!bub ,skills.list
!bub ,skills.describe name=discord
```

结论：

- `discord` 技能可见
- 问题不在技能注册/发现
- 问题发生在“技能执行阶段”

### 阶段 3：定位为 shell 环境与路径假设错误

终端日志观察到：

- 模型反复调用 `bash` 工具做环境探测
- 使用了不适合 bash 的命令（如 `where`）
- 假设 `uv/python` 在当前 bash `PATH` 中可用
- 混用 Windows 路径与 `/mnt/c/...` 路径

结论：

- 需要在提示词中明确环境约束，让模型优先走 `fs.read` 和 `discord` 技能，而不是乱探测 shell

---

## 关键修复策略（不改源码）

### 策略：在消息中显式声明执行环境约束 + 用 `$hint` 引导工具/技能展开

使用的关键约束方向：

1. 明确 shell 工具运行于 `bash`，不是 `cmd`
2. 禁止使用 `where/dir`
3. 不假设 `uv/python` 在 `PATH`
4. 优先使用 `fs.read`
5. 使用工作区相对路径
6. 明确要求“必须通过 `discord` 技能向频道发送消息”
7. 使用 `$fs.read $discord` hint 提示展开对应工具/技能说明

---

## 成功测试消息（实测有效范式）

> 注：以下为结构化模板，可按需调整文案。核心是“环境约束 + 明确要求发送 + `$hint`”。

```text
!bub 请严格按 discord 技能执行并把回复真正发送到当前频道。
要求：
1) shell 工具是 bash，不是 cmd
2) 不要使用 where/dir
3) 不要假设 uv/python 在 PATH
4) 优先使用 fs.read，使用工作区相对路径
5) 先读取 src/bub/skills/discord/SKILL.md
6) 然后使用 discord 技能向当前频道发送这句消息：环境识别成功，Discord 技能发送测试通过。
7) 不要只在终端输出总结；必须把消息发到频道。
$fs.read $discord
```

---

## 成功结果（观察）

### 频道侧 ✅

- Bub 在 Discord 频道中成功发送回复
- 回复内容符合提示要求

### 终端日志侧 ✅

可观察到以下成功信号（示意）：

- 模型步骤推进日志（`model.runner.step`）
- `fs.read` 工具调用成功
- `bash` 调用技能脚本（如 `discord_send.py`）成功
- 终端出现类似“消息已成功发送到 Discord 频道”的提示

---

## 经验总结（本次实验最重要的结论）

### 结论 1：自然语言回复在 Discord 中的设计主路径是“技能发送”，不是适配器自动转发

- 命令型消息成功回帖，不代表自然语言路径也会自动回帖
- 自然语言路径要依赖 `discord` 技能完成消息发送

### 结论 2：在 Windows 主机上运行 Bub 时，应显式约束模型对 shell 环境的假设

推荐长期复用的约束要点：

- shell 工具 = `bash`
- 非 `cmd`
- 禁止 `where/dir`
- 不假设 `uv/python` 在 `PATH`
- 优先 `fs.read`
- 使用工作区相对路径

### 结论 3：`$hint`（如 `$fs.read`, `$discord`）在技能调试场景中非常有价值

作用：

- 提升模型选择正确工具/技能的概率
- 降低模型在无关 shell 探测上的尝试成本

---

## 可复用测试清单（后续回归测试）

1. `!bub ,help`（命令路径）
2. `!bub ,tape.info`（session/tape 可观测性）
3. `!bub ,skills.list`（技能发现）
4. `!bub ,skills.describe name=discord`（技能说明可读）
5. 发送“环境约束 + `$fs.read $discord`”测试消息（技能发送路径）
6. 在频道确认收到回复（最终验收）

---

## 后续改进建议（不涉及源码修改）

1. 将“Windows + bash 工具环境约束”写入工作区 `AGENTS.md`
2. 将常用 Discord 测试提示词保存为本地模板
3. 定期用 `,tape.info` 观察 Discord channel 对应 session 的上下文增长情况

---

## 附：本次问题的标准化描述（可用于笔记/分享）

> Bub 在 Discord `message` 模式下，命令型消息可直接通过 `immediate_output` 回帖；自然语言消息需要模型通过 `discord` 技能主动发送。初始失败的主要原因是模型对 shell 执行环境（bash vs cmd）和路径风格（Windows 路径 vs `/mnt/c/...`）假设不稳定。通过在提示词中显式约束环境并使用 `$fs.read $discord` hint，可显著提高 Discord 技能执行成功率并最终完成频道回复。

