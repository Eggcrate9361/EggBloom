# 变更日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 `package.json` 的 `version`（唯一真相源）。

## [Unreleased]

### 新增

- `scripts/dev_watch.py`：改 `.py` 自动同步 → 重启后端 → 等 WebUI 就绪（支持 `--dry-run`）。
- `persona_tag.py`：按配置/派生标签给特定发送者追加一句语气提示（默认关闭）。
- 应用图标：`scripts/make_icons.py` 按 v4 色板代码生成像素沙漏图标（`icon.png` / `icon.ico` / `tray-icon.png`），窗口与托盘开始使用。

### 变更

- **`NexSandglass` 归属修正**：确认为独立上游项目
  [`lovevin1314-tech/NexSandglass-Agent-DedicatedMemory`](https://github.com/lovevin1314-tech/NexSandglass-Agent-DedicatedMemory)
  （MIT，`Copyright (c) 2026 NexSandglass`，内嵌 v2.9.10）。
  `README.md` / `THIRD_PARTY.md` / `docs/RELEASE_CHECKLIST.md` 三处此前写作「本项目自有」「无上游引用」，已改为上游来源标注。
- 代码与文档中的真实 QQ / 群号 / 人名全部替换为占位符；默认值不再指向任何真实账号。
- `group_trigger_words` 默认改为 `["小a"]`（为空时仍自动派生 Bot 昵称）。
- `.gitignore` 补齐 `chat_logs/`、`personality/`、`logs/`、`image_cache/`、`self_learning_db/` 等隐私目录。
- 打包排除 `__pycache__` / `*.pyc`（Standard 少 818 个文件、约 12 MB）。

### 修复

- 标准版统一以桌面壳 `EggBloom Desktop.exe` 作为唯一启动入口（README / USAGE / 打包校验同步更新）。
- `activate.py` 不认 `sync-dist.ps1` 的排除名单，v3 前端一改就误报「不一致」。
- `pyproject.toml` 版本号与 `package.json` 不一致（0.7.0 → 1.5.5）。

### 安全

- 不再在源码中硬编码个人账号；「老大」语气提示改用配置 + PBKDF2 派生标签。

## [1.5.5] - 2026-10-14

### 新增

- 引用回复：群聊支持按 QQ 引用回复（`quote_me` 语义）。
- 决策「接话窗口」与「潜水前观察」：回完 45s 内追问可再答一次；判不接后 25s 再看一眼。
- 工具成败上报：`send_user_file` 等工具不再「假成功」。
- 出站文本按场景脱敏。

### 修复

- 引用消息里嵌套 `sender` 归一化 ——「引用我必回」不再静默失效。
- `@提及` 不再从文本蒸发，指代不清的问题收敛。
- 残句判定让位给事实信号，完整句不再被截断。
- 推理模型思维链吃光输出预算 → 加倍重试一次。
- 搜索链重做：搜狗优先 + `b_algo` 严格解析。
- 说话人标签统一 `_display_name`，合并窗口不再出现裸 QQ 号。

### 性能

- 历史摘要改 append-only 账本；准静态块挪进可缓存前缀（缓存命中区从 3.2K 扩大）。
