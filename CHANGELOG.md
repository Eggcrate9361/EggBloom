# 变更日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 `package.json` 的 `version`（唯一真相源）。

## [Unreleased]

## [1.5.6] - 2026-10-19

### 新增

- **`sandglass_lite.py`**：沙漏记忆引擎的实现替换（落沙写入 / 原文检索 / 按日期回忆 / 决策粒子）。
  ⚠ **不是原创设计** —— 复刻的是上游 NexSandglass 的**数据格式与检索思路**
  （`sandglass.txt` 行格式、`sandglass.idx` 倒排结构、中文 2-gram + 英文 n-gram 分词、
  密度×信任融合评分），见 `THIRD_PARTY.md`「设计参考」。
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
  偏移率 / 情绪熵 / 画像 / 织布机，约 1,805 行）从不触发 → 改由 `sandglass_lite.py`
  复刻这四个被使用的能力（**非原创设计**，借鉴自上游，见 `THIRD_PARTY.md`）。
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

- **人格文件 33% 是自动学习产生的污染**（2026-10-19，用户："现在的人格是怎么样"）。
  `personality.txt` 里【自主学习成果】段 240/719 字符，三条根因：

  1. **学群里另一个 AI 机器人**。实测群里跑着 `另一个AI机器人`
     （全局昵称就是模型名），**107 条消息**被当成"群友表达风格"
     写进人格 → EggBloom 开始模仿另一个 AI 的腔调，这是"越来越像机器人"
     最直接的来源。
     ⚠ 注意区分：另一个成员（真人）的**群名片**也叫 `另一个AI机器人`，但它的
     全局昵称是人名 —— **是真人 cosplay**，不能误杀（走白名单/昵称判定）。
     修：新增 `_is_ai_bot_sender()` —— 配置 `self_learn_ignore_qqs` 黑名单 +
     `self_learn_allow_qqs` 白名单 + `self_learn_exclude_ai_names` 自动识别。
     **自动识别只看全局昵称，不看群名片**（名片是每个群可随时改的 cosplay）。
  1b. **严格化：一次性的句子不准进人格**（用户："有的 AI 昵称就像真人啊，
     能不能自学习严格呢"）。行为特征实测**分不开**（真实语料校准：均长
     45 vs 真人 17~220、句末标点 0.03 vs 0.02~0.18、AI 腔词 0.02 vs 0~0.09、
     语气词 0.26 vs 0.05~0.27，全重叠），所以改为**用重复性做硬闸**：
     被污染的 5 条"群表达风格"**全是 count=1** 的一次性句子。
     修：`_select_promotable_expressions()` 要求 `count >= self_learn_min_count`
     （默认 2），且不足 3 条就不写人格。
  2. **把 bot 自己的内部标注当用户原话**。采集时 `text` 已带上程序拼的引用注记
     （`[引用:xxx｜他在引用xxx，**不是在叫我**，别抢话] ...`），存库后被黑话挖掘成
     「当前」「别抢话」「引用」「他在引用」「不是在叫」。
     修：新增 `_strip_bot_annotation()` —— 只采集**用户当前这句原话**。
  3. **场景关键词裸子串匹配**。「关不了」命中"拒绝"的 `不了`、「你不是要关机吗」
     命中"反对"的 `不是`、「这猫笑得比早上好还瘆人」命中"问候"的 `早` ——
     5 条"群表达风格"里 3 条标着"问候时该群常用"，全是风马牛不相及。
     修：`_starts_phrase()` 要求关键词在**句首或标点之后**；另拒收含 `[]【】`、
     `@` 开头、问号结尾的句子。

  已清理存量污染（备份在 `D:\workbuddy_tmp\persona_cleanup\`）：
  `personality.txt` 719 → **478 字符**（回到纯基础人设）、
  messages 559 → 354（删 207 条 AI 发言）、expressions 33 → 0（旧标签全错，重学）、
  jargon 61 → 51（删 10 个标注碎片）、affection 删 2 条 AI 好感度。

- **`image_reply_mode` 默认值改为 `describe`**。`native`（原图塞主模型）实测经
  sensenova 中转连续两次 HTTP 429（TPM/RPM 超限），随后模型自称"看不到图"；
  `describe`（视觉转文字）实测能准确认出歌词截图并说出歌名，且对任何主模型成立。
- **群消息循环引用未定义变量，每条群消息都抛 NameError**（自 1.5.5 首发即存在）。
  `bot_core.main()` 里 `if is_at_bot or has_xiao_a or has_bot_nick or quote_forces:`
  —— `quote_forces` 只在 `process_buffered_message()` 内定义。日志实证：
  `NameError: name 'quote_forces' is not defined`。因为它在"缓冲区建好之后"抛出，
  异常跳过了 `continue` 与"立即建立会话"那段 → **「被@/叫名字/引用 → 立刻开会话」
  从来没生效过**，且每条群消息白打一次堆栈。
  修：主循环改用自己算的 `_quote_me`（引用的原主是 Bot 才算"引用我必回"），
  并把结果写进缓冲区的 `quote_me` 字段（此前 `buffer.get("quote_me")` 恒 False）。
- **「@蛋 叫不动 Bot」**（2026-10-19 线上实证）。同一个 QQ 账号在不同群可以挂
  不同群名片 —— 实测本机 bot 在 A 群名片为 `蛋`、在 B 群名片为空（回退全局昵称）。
  旧逻辑只比 `bot_group_nick` **一个值** → 用户在 B 群用「@蛋」叫它时，
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
