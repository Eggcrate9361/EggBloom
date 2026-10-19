# 变更日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 `package.json` 的 `version`（唯一真相源）。

## [Unreleased]

## [1.5.6] - 2026-10-19

### 新增

- **`sandglass_lite.py`**：沙漏记忆引擎自研实现（落沙写入 / 原文检索 / 按日期回忆 / 决策粒子）。
  替代原先内嵌的上游 `nexsandglass/`，**数据格式完全兼容**（同一份 `sandglass.txt` / `sandglass.idx`）。
  含一项上游没有的修正：**覆盖度闸** —— 上游英文 2~4 字符滑窗会产生大量碎片匹配，
  导致"检索无命中却返回无关记录"（实测搜不存在的词返回 3 条无关内容）。
- `tests/_sandbox.py`：测试用沙漏路径隔离（缺陷修复见下）。
- `scripts/dev_watch.py`：改 `.py` 自动同步 → 重启后端 → 等 WebUI 就绪（支持 `--dry-run`）。
- `persona_tag.py`：按配置/派生标签给特定发送者追加一句语气提示（默认关闭）。
- 应用图标：`scripts/make_icons.py` 按 v4 色板代码生成像素沙漏图标（`icon.png` / `icon.ico` / `tray-icon.png`），窗口与托盘开始使用。

### 变更

- **移除内嵌的 `nexsandglass/`（上游 NexSandglass 源码树，31 个模块约 7,500 行）**。
  审计确认 EggBloom 实际只用到 6 模块 16 符号，其余（MCP Server / TTY Wrapper / 思维层 /
  偏移率 / 情绪熵 / 画像 / 织布机，约 1,805 行）从不触发 → 改由 `sandglass_lite.py` 自研实现。
  随之更新：`README.md` 架构图、`THIRD_PARTY.md`（改列「设计参考」）、
  `PORTABLE_STRUCTURE.md`、`docs/RELEASE_CHECKLIST.md`、`scripts/sync-dist.ps1`、
  `scripts/activate.py`、`scripts/dev_watch.py`、`pyproject.toml`、`harness/agent_project.py`。
  如要回退：从 git 历史恢复该目录（存在于 `4d2446c` 及之前）并将 `_USE_LITE` 置 False。
- **`NexSandglass` 归属修正**：确认为独立上游项目
  [`lovevin1314-tech/NexSandglass-Agent-DedicatedMemory`](https://github.com/lovevin1314-tech/NexSandglass-Agent-DedicatedMemory)
  （MIT，`Copyright (c) 2026 NexSandglass`）。
  此前写作「本项目自有」「无上游引用」，已改为正确标注；
  移除源码后不再分发其代码，故按「设计参考」署名即可。
- 沙漏数据清理：删除 1254 行测试探针（5334 → 4080 行），并重建倒排索引。
- 代码与文档中的真实 QQ / 群号 / 人名全部替换为占位符；默认值不再指向任何真实账号。
- `group_trigger_words` 默认改为 `["小a"]`（为空时仍自动派生 Bot 昵称）。
- `.gitignore` 补齐 `chat_logs/`、`personality/`、`logs/`、`image_cache/`、`self_learning_db/` 等隐私目录。
- 打包排除 `__pycache__` / `*.pyc`（Standard 少 818 个文件、约 12 MB）。

### 修复

- **测试污染生产记忆**（严重）：6 个测试（`test_memory_feed` / `test_sandglass_dedup` /
  `test_memory_stage_a` / `test_agent_service` / `test_agent_landing` / `test_agent_integration`）
  **没有沙漏路径隔离** —— 记忆系统就绪时会真写 `~/.neurobase/sandglass.txt`。
  实测跑一次全量回归，生产沙漏末尾多出 20 行探针（`帮我搜一下天气` / `答案` / `u` / `a` …），
  这些污染会进倒排索引与召回。已加 `tests/_sandbox.py` 统一隔离（tempfile + `NEXSANDBASE_HOME`
  + 模块常量改写 + atexit 清理），回归验证：跑测试前后行数一致，零污染。
- 沙漏检索「无命中却返回无关记录」：倒排检索的英文滑窗碎片命中噪声 + 全量扫描兜底
  用整串匹配（长查询必然匹配不到 → 退化成任意命中）。两处均已修。
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
