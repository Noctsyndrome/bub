# Paccho 已完成改造清单

最后更新：2026-03-10

本文档记录 `lab/0.2.3-paccho` 相对 Bub `0.2.3` 基线已经完成的主要功能改造、行为变化、关键代码位置与验证方式。

## 1. Discord `message` 模式联通与基础调试

### 目标

- 让 Bub 在 Discord 频道内稳定接收消息
- 通过 `discord` skill 成功回帖
- 明确 Windows / Bash / 路径环境下的调试约束

### 结果

- Discord `message` 模式已完成基础联通
- 频道内可成功触发技能回帖
- 相关实验过程已沉淀

### 记录

- `paccho-docs/experiments/discord-message-mode-debug-and-fix.md`

## 2. Discord 默认回复路径与终端输出对齐

### 原问题

- 终端看到的 `assistant_output` 与 Discord 实际回帖不一致
- `proactive_response` 在 Discord 路径上语义不完整
- 通过 skill 主动发送时，常出现字面量 `\n`

### 改造结果

- 默认 `message` 模式下：
  - 终端输出与 Discord 自动回帖使用同一份文本
- `proactive_response=false`
  - 模型直接返回最终文本，由 adapter 自动发回频道
- `proactive_response=true`
  - 保留“模型必须使用 channel skill 主动发送”的语义

### 关键代码

- `src/bub/app/runtime.py`
- `src/bub/core/model_runner.py`
- `src/bub/channels/discord.py`

### 验证

- `tests/test_discord_output.py`
- `tests/test_model_runner.py`

### 记录

- `paccho-docs/experiments/discord-output-alignment/README.md`

## 3. 频道状态检查脚本

### 目标

- 生成适合频道展示的健康检查文本
- 用于后续定时自检与消息投递

### 当前脚本

- `paccho-docs/scripts/health_check.py`

### 功能

- 检查 Bub 相关进程
- 检查关键环境变量
- 检查渠道启用状态
- 支持频道友好的简洁输出

## 4. 上游图片 URL 兼容性探针

### 目标

- 独立验证当前上游模型是否能够理解图片输入
- 将“Bub 逻辑问题”和“上游模型/网关问题”分离

### 当前脚本

- `paccho-docs/scripts/test_upstream_image_url.py`

### 已验证结论

- 当前 `openai:kimi-k2.5 + BUB_API_BASE` 可以理解公开图片 URL
- 但服务端无法稳定拉取 Discord CDN 附件 URL

### 跟进结论（2026-03-10）

- 在长时间无响应排查过程中，曾先后验证火山方舟 `coding/v3` 下的：
  - `openai:kimi-k2.5`
  - `openai:ark-code-latest`
- 现象是：
  - 最小文本请求并非始终完全不可用
  - 但 Discord 实际会话里经常出现远超单次请求时长的等待，且稳定性较差
- 最终已将上游从火山方舟迁移到阿里百炼，模型切换为：
  - `openai:qwen3.5-35b-a3b`
- 迁移后，Discord 简单消息和日常对话已恢复正常响应
- 因此这轮问题的主因更接近“火山方舟接入链路 / 模型兼容性不稳定”，而不是 Bub 本地 Discord / tape 主链路整体失效

## 5. 结构化入站载荷

### 原问题

- 旧链路默认把渠道输入压平成字符串
- 无法在保持命令边界的同时传递图片等多模态信息

### 改造结果

- 新增结构化入站模型：
  - `raw_text`
  - `model_prompt`
  - `display_text`
  - `metadata`
  - `media`
  - `is_command`
  - `immediate`
- Router 仍只用 `raw_text` 做命令边界判断
- 多模态信息只在进入模型路径时使用

### 关键代码

- `src/bub/core/inbound.py`
- `src/bub/channels/base.py`
- `src/bub/app/runtime.py`
- `src/bub/core/agent_loop.py`
- `src/bub/core/router.py`

## 6. Discord 图片消息理解

### 原问题

- 旧版 Discord 适配器只知道“有附件”，不会读图片内容
- 文字 + 图片时，图片信息会被丢掉

### 改造结果

- Discord 入站现在可同时保留：
  - 文本
  - 图片附件
  - 附件元数据
- 图片消息会立即执行，不进入 debounce 拼接
- 进入模型路径时会构造成 OpenAI-compatible 多模态消息

### 关键代码

- `src/bub/channels/discord.py`
- `src/bub/channels/runner.py`
- `src/bub/core/router.py`
- `src/bub/core/model_runner.py`

### 验证

- `tests/test_discord_session_prompt.py`
- `tests/test_session_runner.py`
- `tests/test_model_runner.py`

## 7. 多模态请求与 Tape 兼容

### 原问题

- `republic` 的 `messages=` 模式不允许同时使用 `system_prompt` 和 `tape`
- 直接复用旧的 `Tape.run_tools_async()` 会报：
  - `system_prompt and tape are not supported with messages input`

### 改造结果

- 文本路径继续走 tape
- 多模态路径改为直接调用底层 LLM
- 同时把渲染后的 system prompt 手动注入 `messages[0]`
- 首轮多模态输入的 user/assistant/tool 信息手动写回 tape

### 关键代码

- `src/bub/tape/service.py`
- `src/bub/core/model_runner.py`

## 8. Discord 附件内联、压缩与 data URL 传输

### 原问题

- 上游模型能够理解公开图片 URL
- 但无法稳定拉取 Discord CDN 附件 URL
- 典型报错：
  - `Timeout while downloading url`
  - `connection reset by peer`

### 改造结果

- Bub 先本地读取 Discord 附件
- 超过阈值时进行缩放/压缩
- 再将图片转成 data URL，作为当前这轮模型请求的 `image_url`
- data URL 只存在于本轮请求，不写入 tape / metadata / 日志历史

### 跟进修正（2026-03-10）

- 早期实现虽然把 Discord 附件转成 data URL 再发给上游模型，但在多模态首轮结束后，`ModelRunner._record_multimodal_turn()` 仍会把原始 `messages` 整体写回 tape
- 结果是：内联的 `data:image/...;base64,...` 实际会进入 tape 历史，导致会话文件膨胀，也和上面的设计目标不一致
- 现已补齐这一层：
  - 写回 tape 前先对多模态消息做清洗
  - 内联 `data:` 图片块替换为占位文本 `[inline image omitted from tape history]`
  - 外部图片 URL 与普通文本块保持原样
- 这样现在才真正满足“data URL 只存在于本轮请求，不进入 tape 历史”的约束

### 关键代码

- `src/bub/channels/image_payloads.py`
- `src/bub/channels/discord.py`
- `src/bub/core/inbound.py`
- `src/bub/core/router.py`
- `src/bub/core/model_runner.py`

### 关键策略

- 默认内联上限：`1.5 MB`
- 超限时压缩/缩放
- 读取附件时使用缓存优先策略
- 增加附件读取超时，避免整条消息卡死

### 验证

- `tests/test_image_payloads.py`
- `tests/test_discord_session_prompt.py`
- `tests/test_model_runner.py`

## 9. 当前新增/增强测试

- `tests/test_discord_output.py`
- `tests/test_discord_session_prompt.py`
- `tests/test_image_payloads.py`
- `tests/test_model_runner.py`
- `tests/test_session_runner.py`
- `tests/test_agent_loop.py`
- `tests/test_channels.py`
- `tests/test_graceful_shutdown.py`
- `tests/test_telegram_session_prompt.py`

## 10. 长任务状态驱动、软超时与 Discord 进度提示

### 原问题

- 旧逻辑把 `BUB_MODEL_TIMEOUT_SECONDS=90` 直接当作硬超时
- 终端只能看到 `model.runner.step`，无法判断模型是否仍在处理中
- Discord 频道侧在长任务期间没有明确进度反馈

### 改造结果

- `BUB_MODEL_TIMEOUT_SECONDS`
  - 现在表示软超时阈值，默认 `90`
- 新增 `BUB_MODEL_HARD_TIMEOUT_SECONDS`
  - 表示模型调用最终硬上限，默认 `600`
- 新增 `BUB_MODEL_PROGRESS_UPDATE_SECONDS`
  - 表示进入慢任务状态后的进度更新间隔，默认 `30`
- 模型调用改为后台任务 + 轮询观察
- 终端新增：
  - `model.call.start`
  - `model.call.waiting`
  - `model.call.finish`
  - `model.call.error`
  - `model.call.hard_timeout`
- Discord 达到软超时后会发送并编辑单条状态消息
- 成功时删除状态消息，失败或硬超时时将其更新为最终说明

### 关键代码

- `src/bub/config/settings.py`
- `src/bub/core/progress.py`
- `src/bub/core/model_runner.py`
- `src/bub/core/agent_loop.py`
- `src/bub/app/runtime.py`
- `src/bub/channels/base.py`
- `src/bub/channels/runner.py`
- `src/bub/channels/discord.py`

### 验证

- `tests/test_model_runner.py`
- `tests/test_discord_output.py`
- `tests/test_agent_loop.py`
- `tests/test_session_runner.py`

### 记录

- `paccho-docs/records/long-running-task-observability-plan.md`
- `paccho-docs/experiments/long-running-task-observability/README.md`

## 11. 上游模型迁移结论

### 最终结论（2026-03-10）

- 本次排障后，Paccho 当前稳定使用的上游已从火山方舟迁移到阿里百炼
- 当前确认可用的组合为：
  - 平台：阿里百炼
  - 模型：`qwen3.5-35b-a3b`
- 迁移后的直接结果：
  - Discord 简单问候可正常返回
  - 会话不再反复陷入长时间“仍在处理”状态
  - notes 更新、日常聊天等常见路径已恢复可用

### 备注

- 这不等于此前所有问题都来自上游；Paccho 在这轮排障中仍修复了：
  - data URL 被写入 tape 的问题
  - 部分手工写入 tape 时的编码问题
- 但从最终恢复效果看，上游迁移是“恢复整体可用性”的决定性步骤

## 12. 当前分支的代码关注点

如果后续继续扩展功能，优先从这些入口继续：

- 渠道入站：
  - `src/bub/channels/discord.py`
  - `src/bub/channels/runner.py`
- 结构化输入：
  - `src/bub/core/inbound.py`
  - `src/bub/core/router.py`
- 模型执行：
  - `src/bub/core/model_runner.py`
  - `src/bub/tape/service.py`
- 图片处理：
  - `src/bub/channels/image_payloads.py`

## 13. 后续建议

1. 为“压缩后仍超限”的图片返回更明确的用户提示
2. 继续补充 `paccho-docs/experiments/`，把每次改造的实验过程沉淀下来
3. 补一份“火山方舟 -> 阿里百炼”迁移记录，明确失效现象、对照测试和最终稳定配置
4. 在条件允许时，把当前行为与上游 `main` / 新架构路线做一次对照评估
