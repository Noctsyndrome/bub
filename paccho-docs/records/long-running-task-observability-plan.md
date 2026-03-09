# Paccho 长任务状态驱动与超时语义调整计划

最后更新：2026-03-09

## 背景

此前 `lab/0.2.3-paccho` 的 `message` 路径沿用 Bub `0.2.3` 的单次模型硬超时思路：

- `BUB_MODEL_TIMEOUT_SECONDS` 默认 `90`
- 超过 90 秒直接返回 `model_timeout`
- 终端在模型调用期间缺少明确的进行中状态
- Discord 用户侧也只能看到最终失败，没有“仍在工作中”的说明

这会把“慢任务仍在正常执行”和“模型调用异常”混为一谈。

## 本次调整目标

1. 不再把 90 秒直接视为异常
2. 用执行状态区分：
   - 正在进行中
   - 已正常完成
   - 已异常失败
   - 已超过最终硬上限
3. 终端和 Discord 都明确告诉用户当前仍在处理，避免无声等待

## 调整方案

### 1. 软超时 + 硬上限

- `BUB_MODEL_TIMEOUT_SECONDS`
  - 现在表示软超时阈值，默认 `90`
- `BUB_MODEL_HARD_TIMEOUT_SECONDS`
  - 新增，表示模型调用最终硬上限，默认 `600`
- `BUB_MODEL_PROGRESS_UPDATE_SECONDS`
  - 新增，表示进入慢任务状态后的进度更新间隔，默认 `30`

行为定义：

- 小于软超时：正常等待
- 达到软超时：进入“仍在处理中”状态，不立即失败
- 达到硬上限：取消本轮模型调用并返回 `model_hard_timeout`

### 2. 模型调用观察方式

`ModelRunner` 不再用一层 `asyncio.timeout()` 直接包住模型调用，而是：

- 将真实模型调用放入后台 `asyncio.Task`
- 在前台按秒观察任务状态
- 根据任务是否完成、是否抛错、是否超过软/硬阈值决定后续动作

这让 Bub 可以真正回答：

- 现在是不是还在跑
- 已经等了多久
- 是不是应该继续等

### 3. 终端日志

新增生命周期日志：

- `model.call.start`
- `model.call.waiting`
- `model.call.finish`
- `model.call.error`
- `model.call.hard_timeout`

终端现在可以明确显示：

- 哪一步开始了模型调用
- 已经等待了多久
- 最终是成功、失败还是达到硬上限

### 4. Discord 进度提示

当任务达到软超时后，Discord 会：

- 回复一条“还在处理中”的状态消息
- 后续按固定间隔编辑同一条消息
- 成功时删除状态消息，再发送最终结果
- 硬超时或失败时，把这条状态消息改成最终失败说明

这样用户不会陷入完全无提示的等待，也不会被多条进度消息刷屏。

## 关键代码位置

- `src/bub/config/settings.py`
- `src/bub/core/progress.py`
- `src/bub/core/model_runner.py`
- `src/bub/core/agent_loop.py`
- `src/bub/app/runtime.py`
- `src/bub/channels/base.py`
- `src/bub/channels/runner.py`
- `src/bub/channels/discord.py`

## 关键测试

- `tests/test_model_runner.py`
- `tests/test_discord_output.py`
- `tests/test_agent_loop.py`
- `tests/test_session_runner.py`

## 当前结论

这次调整的重点不是单纯“把超时调大”，而是把长任务处理改成状态驱动：

- 软超时用于提示“还在工作中”
- 硬上限用于判断“本轮必须结束”
- 终端与 Discord 都具备可观测性
