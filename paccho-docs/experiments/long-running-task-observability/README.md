# 长任务状态驱动与超时语义实现记录

最后更新：2026-03-09

## 背景

此前 `lab/0.2.3-paccho` 在 Discord `message` 模式下沿用单次模型调用 90 秒硬超时：

- 超过 90 秒直接返回 `model_timeout`
- 终端只能看到 `model.runner.step`
- Discord 频道没有“仍在处理中”的明确说明

这会把慢任务和异常混为一谈。

## 本次实现

### 1. 超时语义调整

- `BUB_MODEL_TIMEOUT_SECONDS`
  - 改为软超时阈值，默认 `90`
- 新增 `BUB_MODEL_HARD_TIMEOUT_SECONDS`
  - 单次模型调用最终硬上限，默认 `600`
- 新增 `BUB_MODEL_PROGRESS_UPDATE_SECONDS`
  - 慢任务状态更新间隔，默认 `30`

### 2. ModelRunner 状态观察

模型调用现在改为后台 `Task` + 前台轮询观察：

- 开始时发送 `started`
- 达到软超时时发送 `soft_timeout_reached`
- 后续按固定间隔发送 `progress_update`
- 成功完成时发送 `completed`
- 抛出异常时发送 `failed`
- 达到硬上限时取消任务并发送 `hard_timeout`

终端新增日志：

- `model.call.start`
- `model.call.waiting`
- `model.call.finish`
- `model.call.error`
- `model.call.hard_timeout`

### 3. Discord 进度提示

Discord 适配器现在会把进度事件映射成单条状态消息：

- 达到软超时后，reply 一条“还在处理中”
- 后续只编辑同一条消息
- 成功完成后删除这条状态消息
- 失败或硬超时后，把这条状态消息改成最终失败说明

同时避免重复错误消息：

- 如果 Discord 状态消息已经成功更新为失败说明
- `process_output()` 不再重复发送同一条错误文本

## 关键代码

- `src/bub/config/settings.py`
- `src/bub/core/progress.py`
- `src/bub/core/model_runner.py`
- `src/bub/core/agent_loop.py`
- `src/bub/app/runtime.py`
- `src/bub/channels/base.py`
- `src/bub/channels/runner.py`
- `src/bub/channels/discord.py`

## 验证

执行：

```bash
uv run pytest -q tests/test_model_runner.py tests/test_discord_output.py tests/test_agent_loop.py tests/test_session_runner.py
uv run ruff check src/bub/core/progress.py src/bub/core/model_runner.py src/bub/channels/base.py src/bub/channels/runner.py src/bub/channels/discord.py src/bub/app/runtime.py src/bub/config/settings.py tests/test_model_runner.py tests/test_discord_output.py tests/test_agent_loop.py tests/test_session_runner.py
```

结果：

- `29 passed`
- `All checks passed!`

## 当前结论

这次改造后，Paccho 分支的长任务处理已经从“固定 90 秒硬超时”变成：

- 90 秒后提示“仍在处理中”
- 继续运行到最终硬上限
- 终端与 Discord 都能看到明确状态
- 只有达到硬上限或真实异常时才视为失败
