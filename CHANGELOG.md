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
- **重名群友识别**：人物画像新增 `groups` 维度（记录每个 QQ 出现在哪些群），
  同群有两个不同 QQ 用同一显示名时，画像里会陈述「本群有同名：小明（2 人）」。
  只陈述事实、不带任何"应当如何称呼"的指令 —— 真实的人遇到同名只会知道有两个人，
  必要时自然问一句「哪个小明」，不需要机器式规则。
- **群聊人物画像注入**：此前群聊路径**完全没有画像**（只有私聊走
  `get_memory_prompt_block`），Bot 在群里不知道谁是谁。现补上 `build_people_block`。
- **Bot 跨群自知**：`log_bot_reply` 写入时补 `[群:gid]` 标记，使 Bot 能检索到
  自己在各群说过的话（此前 agent 行无归属，被作用域过滤全部排除）。
  检索仍严格按群隔离 —— 有自知，但不把 A 群内容带到 B 群。
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

- **群消息循环引用未定义变量，每条群消息都抛 NameError**（自 1.5.5 首发即存在）。
  `bot_core.main()` 里 `if is_at_bot or has_xiao_a or has_bot_nick or quote_forces:`
  —— `quote_forces` 只在 `process_buffered_message()` 内定义。日志实证：
  `NameError: name 'quote_forces' is not defined`。因为它在"缓冲区建好之后"抛出，
  异常跳过了 `continue` 与"立即建立会话"那段 → **「被@/叫名字/引用 → 立刻开会话」
  从来没生效过**，且每条群消息白打一次堆栈。
  修：主循环改用自己算的 `_quote_me`（引用的原主是 Bot 才算"引用我必回"），
  并把结果写进缓冲区的 `quote_me` 字段（此前 `buffer.get("quote_me")` 恒 False）。
- **「@蛋 叫不动 Bot」**（2026-10-19 线上实证）。同一个 QQ 账号在不同群可以挂
  不同群名片 —— 实测本机 bot（10000001）：群 `200000002` 名片 `蛋`、
  群 `200000001` 名片为空（回退全局昵称 `占位名片A`）。
  旧逻辑只比 `bot_group_nick` **一个值** → 用户在 200000001 用「@蛋」叫它时，
  `_is_at_others` 判成"@别人"→ 整条消息被跳过。
  修：新增 `_bot_self_names()` 汇总 QQ 号 + 配置昵称 + **各群真实名片**，
  `_is_at_others` / @ 检测 / `has_bot_nick` 全部改用它。
- **跨群串名：本群名片被别群昵称顶掉**。`_display_name` 只走
  `resolve_qq_nick`（内部 `get_stranger_info` 只回全局昵称，无群维度），
  于是 A 群的名字被带进 B 群。修：`_display_name(sender, group_id)` **群名片优先**，
  回落全局昵称，最后兜底裸 QQ；群消息入口实时用 `sender.card` 刷新
  `GROUP_MEMBERS[gid][uid]`。引用消息的说话人也改走同一函数。
- **全局昵称缓存被群名片污染**：拉群成员列表时写的是 `display`（card 优先）→
  某群名片进了全局 `QQ_NICK_CACHE`，别的群/私聊跟着串。改为只写全局昵称
  （`nick or display`），群维度显示由 `GROUP_MEMBERS` 独立承担。
- **「越来越像机器人」的根因：长期人格里存满了对话碎片**（严重）。
  `super_memory.extract_facts` 曾有一条过宽正则 `([^，。！？\s]+?)是(.+?)` ——
  **任何含「是」的短句都被抽成「偏好」**，实测把 `这：啥`、`你的谁呢`、`在叫我`、
  `我这：战略性休息` 这类碎片当人格存了 75 条（其中 72 条是垃圾）。
  这些经 `get_prompt_block` **每轮**注入 prompt（`【长期人格】user_preferences`）→
  模型被无意义碎片带偏，回复明显机器化。
  处置：① 删掉那条宽泛正则；② 新增 `_is_meaningful_fact` 质量闸（拦空泛主语、
  疑问尾缀、括号残片）；③ 同内容去重；④ 历史库清理 75 → 3 条（备份在
  `D:\workbuddy_tmp\super_memory_backup\`）。
- **`prefetch` 每轮注入原始消息碎片**（2026-10-19 引入后自行修正）。
  薄适配时曾把「偏移+情绪」改写成「最近 3 条原始消息」，实测含测试数据与
  `[Bot回复]` 旧格式垃圾，且它是**每轮**注入（放 user 同轮保前缀缓存）→
  严重干扰对话连贯。现改为自研情绪风向：只看对方发言、跳过旧格式、
  **无信号返回空**（零 token 成本）。
- **「协议开关」显示未启用，但协议其实在线**。该卡片读的是 `protocol_enabled`
  配置标记，而它**只在「切换协议驱动」时**才被写成 True，点「启动协议层」不写它 →
  用户明明启动了 NapCat 却永远显示未启用。现改为**真实探测** OneBot
  `/get_status`（3s 缓存），字段 `protocol_online` / `protocol_url`，
  卡片文案改为「协议连接 · 在线/离线」。
- **群聊记忆降级路径跨群泄漏**。`bot_core` 的群聊主路径抛异常时会降级到
  `recall_history_block`，而该函数与其内部检索**都不传 scope** → 跨群/私聊记忆
  会被注入本群 prompt（与 2026-10-11 修过的"私聊泄漏"同类，当时只修了主路径）。
  现 `_search_context_for_message` / `recall_history_block` /
  `get_memory_prompt_block` 全部支持并透传 `scope`，群聊降级调用点传
  `("group", group_id)`；补 3 个测试覆盖。
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
