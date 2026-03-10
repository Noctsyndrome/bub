# Paccho Branch

最后更新：2026-03-10

`lab/0.2.3-paccho` 不再保留 `0.2.3` 基线学习材料。这个分支只承载基于 Bub `0.2.3` 的定制改造、实验记录和辅助脚本。

## 目录说明

- `README.md`
  - 分支总览、目录索引、已完成改造入口
- `records/`
  - 分支级改造清单、设计决策、关键行为变化说明
- `experiments/`
  - 联调过程、问题复现、修复记录、实验结论
- `scripts/`
  - 运行状态检查、上游兼容性探针、频道联调脚本

## 当前重点

1. Discord `message` 模式行为修复
2. `proactive_response` 语义与默认自动回帖路径对齐
3. 多模态入站载荷与图片理解支持
4. Discord 附件内联、压缩与 data URL 传输
5. 上游模型兼容性验证、迁移结论与回归测试补齐
6. 长任务状态驱动、软超时与 Discord 进度提示

## 当前上游结论

- 2026-03-10 这轮排障后，Paccho 已从火山方舟迁移到阿里百炼
- 当前稳定使用的模型为 `qwen3.5-35b-a3b`
- 迁移后 Discord 日常对话已恢复正常响应
- 相关背景和结论已记录到 `records/completed-modifications.md`

## 已完成记录

- `paccho-docs/records/completed-modifications.md`
- `paccho-docs/records/long-running-task-observability-plan.md`
- `paccho-docs/experiments/discord-message-mode-debug-and-fix.md`
- `paccho-docs/experiments/discord-output-alignment/README.md`
- `paccho-docs/experiments/long-running-task-observability/README.md`

## 当前脚本

- `paccho-docs/scripts/health_check.py`
- `paccho-docs/scripts/test_discord_newline.py`
- `paccho-docs/scripts/test_upstream_image_url.py`

## 使用原则

- 与 `0.2.3` 基线理解相关的材料，不再在本分支内维护
- 这里只记录“相对 0.2.3 增加了什么、为什么这样改、如何验证”
- 任何后续定制功能，都应优先补到 `paccho-docs/records/` 或 `paccho-docs/experiments/`
