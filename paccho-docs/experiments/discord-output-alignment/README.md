# Discord `message` 输出对齐修复记录

最后更新：2026-03-08

## 背景

在 `message` 模式下，Bub 处理 Discord 消息时出现了两个直接影响调试和使用体验的问题：

1. 终端输出与 Discord 频道中的最终回复不一致。
2. 模型通过 `discord` skill 主动发送消息时，换行有时会变成字面量 `\n`，导致 Discord 展示质量明显差于终端输出。

这会带来两个后果：

- 用户难以判断终端看到的文本是否真的被投递到了频道。
- `proactive_response` 的语义在 Discord 路径上不清晰，且与 Telegram 行为不一致。

---

## 问题根因

### 1. Discord 适配器原先只自动发送 `immediate_output`

原先 `DiscordChannel.process_output()` 的行为是：

- 终端打印 `immediate_output + assistant_output + error`
- Discord 仅自动发送 `immediate_output`

这意味着：

- 命令路径的即时结果会自动发到频道
- 模型自然语言回复通常落在 `assistant_output`
- 因此终端和 Discord 很容易看到两份不同内容

对应旧逻辑位置：

- `src/bub/channels/discord.py`

### 2. `proactive_response` 在旧实现中是“半接通”的

旧版 `message --proactive-response` 虽然已经存在：

- CLI 参数：`src/bub/cli/app.py`
- 配置项：`src/bub/config/settings.py`

但它在 Discord 路径上并没有形成完整闭环：

- Telegram `process_output()` 会参考 `proactive_response`
- Discord `process_output()` 原先不会根据这个开关切换最终回复策略
- `ModelRunner._runtime_contract()` 也不会根据这个开关调整渠道响应约束

结果是：开关存在，但在 Discord 上的语义不完整。

### 3. channel skill 路径容易与 adapter 自动回帖路径分叉

旧版 `_runtime_contract()` 会强制模型：

1. 识别来源渠道
2. 调用对应 channel skill 发送消息
3. 然后再结束当前轮次

这会导致普通“回复文本”走到 `discord` skill 路径，而不是直接返回给 adapter。

一旦 skill 脚本对换行、转义或参数拼接处理不稳定，就会出现：

- Discord 频道文本和终端文本不一致
- Discord 里出现字面量 `\n`

---

## 修改目标

本次修复的目标不是“取消终端输出”，而是让两个目标同时成立：

1. 终端仍然保留，作为重要调试参考
2. 默认情况下，Discord 频道收到的文本与终端输出完全一致

同时保留一个明确的高级模式：

- 当用户显式启用 `--proactive-response` 时，仍允许模型通过 channel skill 主动投递消息

---

## 具体修改

### 1. `AppRuntime` 将 `proactive_response` 传入 `ModelRunner`

修改文件：

- `src/bub/app/runtime.py`

修改内容：

- `AppRuntime.get_session()` 创建 `ModelRunner` 时，传入 `self.settings.proactive_response`

意义：

- 让模型层真正知道当前是否处于主动响应模式

### 2. `ModelRunner._runtime_contract()` 改为根据 `proactive_response` 分支

修改文件：

- `src/bub/core/model_runner.py`

修改内容：

- 新增 `proactive_response` 参数
- `_render_system_prompt()` 按当前模式渲染不同的 `<response_instruct>`

调整后的语义：

#### `proactive_response = false`

- 如果是渠道消息，模型应直接返回最终回复文本
- 使用正常换行，不要输出字面量 `\n`
- 不要为了普通回复去调用 channel skill
- 由 channel adapter 将最终文本投递回同一渠道

#### `proactive_response = true`

- 维持旧思路：模型必须使用对应 channel skill 先发送消息，再结束本轮

### 3. Discord 适配器默认发送与终端完全相同的文本

修改文件：

- `src/bub/channels/discord.py`

修改内容：

- 原先默认仅发送 `immediate_output`
- 现在默认发送与终端 `print(content)` 完全相同的 `content`
- 若 `proactive_response = true`，则回到旧行为，仅兜底发送 `immediate_output` / `error`

结果：

- 默认 `uv run bub message` 下：
  - 终端输出是什么
  - Discord 收到的就是什么

---

## 修改后的行为定义

### 默认模式：`uv run bub message`

行为：

- 模型返回最终自然语言回复
- Discord adapter 自动将该回复发送到原频道
- 终端和 Discord 文本保持一致

适用场景：

- 普通频道对话
- 调试文本质量
- 需要把终端视为最终回复预览

### 主动响应模式：`uv run bub message --proactive-response`

行为：

- 模型必须自己通过 channel skill 投递渠道消息
- adapter 只处理 `immediate_output` / `error` 的兜底发送

适用场景：

- 需要主动在渠道侧执行动作
- 需要由 skill 精细控制回复位置、格式或额外操作

---

## 测试与验证

本次修改新增/调整了以下测试：

- `tests/test_discord_output.py`
- `tests/test_model_runner.py`

验证命令：

```bash
uv run pytest -q tests/test_discord_output.py tests/test_model_runner.py
```

验证结果：

- `16 passed`

测试覆盖点：

1. 默认模式下，Discord 发送的内容与终端打印完全一致
2. 默认模式下，只有 `assistant_output` 也会被发送
3. `proactive_response=true` 时，Discord 恢复为仅发送 `immediate_output`
4. `ModelRunner` 会根据 `proactive_response` 渲染不同的渠道响应契约

---

## 与此前实验记录的关系

此前的记录：

- `paccho-docs/experiments/discord-message-mode-debug-and-fix.md`

主要关注：

- Discord 基础联通性
- `immediate_output` 与 `assistant_output` 的分流
- 通过 `discord` skill 主动发消息的可行性

本次记录是在此基础上的进一步修复，重点不是“让 skill 可用”，而是：

- 让默认消息回复路径更稳定
- 让终端输出与 Discord 最终文本对齐
- 给 `proactive_response` 一个真正可解释、可验证的语义

---

## 当前结论

1. `proactive_response` 这个开关原本就存在，但在旧版 Discord 实现里并未完全打通。
2. 这次修复后，默认 `message` 模式的推荐路径是：
   - 模型返回最终文本
   - adapter 自动发回渠道
3. 如果你需要“终端输出就是 Discord 最终消息”的体验，应保持：

```bash
uv run bub message
```

而不是：

```bash
uv run bub message --proactive-response
```

4. `--proactive-response` 现在被保留为高级模式，而不是默认模式。

