# EggBloom 架构与功能全景

基于代码实测（bot_core.py 7566 行、webui_server.py 3969 行、nexsandglass 38 文件、plugins 全量）整理的架构文档。

优化建议见 `docs/OPTIMIZATION.md`。

## 1. 总体架构

```
CustomTkinter 桌面端 (main_window.py)
    │
WebUI 管理面板 (webui_server.py :8080, 手写 http.server 路由)
    │ 代理
bot_core.py (:3005)   AI 核心 + 联系人管理 API + 事件服务
    │ OneBot v11 (HTTP 动作 + WS 事件桥)
协议适配层 (protocols/): snowluma / napcat / llonebot
    │
QQ 客户端 (QQNT)
```

依赖的外部服务：
- DeepSeek/OpenAI 等云端 API（13 家供应商 + 自定义）
- 本地 LLM：Ollama / LM Studio / llama-server（`:8083` 向量、生图 prompt）
- ComfyUI（`:8188` 图片生成）
- Embedding：进程内 LiGGUF DLL 优先，llama-server HTTP 兜底（bge-small-zh）

## 2. 消息处理链路

| 环节 | 位置 | 说明 |
|------|------|------|
| 事件接收 | bot_core.py:506 | ThreadingHTTPServer，事件入队列 |
| 轮询兜底 | bot_core.py:2059 | 3s 增量拉取，按水位线去重 |
| 群消息合并缓冲 | bot_core.py:6100 | 3/5/15s 三档窗口，区分触发/会话/上下文 |
| @会话模式 | bot_core.py:7112 | 20条/5分钟窗口，期间普通消息也回复 |
| 群决策插件放行 | bot_core.py:6134 | 无触发词时判定是否自然插话 |
| 消息解析 | bot_core.py:1874 | 文本/卡片/转发/图片/引用，四层回退 |
| 过滤链 | bot_core.py:6056 | 定向他人/短消息/乱码/旁观者评论分级过滤 |
| 重复回复检测 | bot_core.py:1371 | 集合重叠>0.6 换话重试，最多2次 |
| 拟人发送 | bot_core.py:1145/1264 | 按句拆分+延时，颜文字保护，长文分段 |

## 3. AI 能力

- 对话：DeepSeek 主后端（非阻塞队列 + 3 并发 worker + 5 次重试），本地多后端可切换
- 图片理解：base64 混入 user 消息，≤3 张
- 图片生成：意图识别 → 角色资料搜索（萌娘百科/百度）→ SFW/NSFW 标签库 → 校验补全 → ComfyUI（文生图 + 图生图 denoise=0.35）
- 联网搜索：天气/股票/Bing 网页正文
- 浏览器操作：web_operator（Playwright），开页/读页/填表/点击/截图
- 问卷自动填写：强制多轮工具链（≤20轮）+ 完成验证，杜绝假装执行
- 本地生图 LLM：自动拉起 llama-server，空闲 5 分钟自动关
- 12 个工具（function calling）：search_web / open_app / take_screenshot / get_system_info / control_volume / control_power / manage_process / search_files / send_user_file / web_operate / get_group_members / generate_image

## 4. 事件处理

- 好友/加群申请自动同意（白/黑名单 + 欢迎语）
- 入群欢迎 + 自动改名片 + 通知管理员；退群清理
- 戳一戳：反戳 + 分级 AI 吐槽 + 表情包（群/私聊独立）
- 撤回反馈：群/私聊独立开关/冷却/文案，仅活跃会话触发
- 管理员通知：加好友/加群推送给 admin_qqs

## 5. 指令系统

- 管理员（本机控制）：截屏 / 打开应用(30+别名) / 系统信息 / 音量 / 关机重启(60s可取消) / 进程管理 / 文件搜索转发
- 所有人：系统信息 / 文件搜索 / 帮助 / 学习状态 / 群友画像（全群/单人）
- 群管理：@Bot + 踢人/禁言/设管/改名片/改群名/退群，admin 鉴权
- 权限分级：strict/off 两档，管理员=target_qqs + command_admins + Bot 本人

## 6. 联系人管理 API

- 群：列表/成员(按 owner/admin 排序)/回复开关/新成员名片/改名/禁言/踢人/全员禁言/设撤管
- 好友：列表/回复开关/改备注
- 全部落盘 + 热重载，按群/按人隔离

## 7. 特殊功能（拟人化）

- 人格注入：EGO personality_engine 优先，personality.txt 兜底，mtime 缓存
- 身份自识别 + 说话多样性 + 去 AI 味（prompt + 规则双通道）
- 表情包：`[表情:xx]` 精确/情绪检测/记忆推断三档匹配
- 空闲退场：20+ 条碎碎念文案 + 崩溃表情包，概率触发
- 群聊时间线/群摘要/成员按需注入（省 token）
- boss 特殊人格、网络用语注入、私聊记忆召回
- 聊天记录转存（Markdown 按日期/QQ 分文件）

## 8. 配置体系

- 入口 config.py:400（弱缓存，程序目录优先 + AppData 补缺 + .env + Windows 凭据管理器）
- 热重载：24 个可重载全局变量（端口类需重启）
- 密钥存 Windows 凭据管理器，网页只回显 `_has_api_key` 布尔

## 9. WebUI（15 页面）

仪表盘 / 事件通知(CPU内存趋势) / 日志 / 控制台 / 生图 / 记忆 / 插件 / 配置中心(15 子面板) / 远程控制 / Bot 控制 / 协议 / 群聊行为 / 联系人管理 / Token 消费 / 表情包管理。

API 路由：`/api/status` `/api/groups*` `/api/friends*` `/api/plugins*` `/api/config*` `/api/llama/*` `/api/imagegen/*` `/api/memes/*` `/api/protocol/*` `/api/bot/*` `/api/command` `/api/memory*` `/api/chatlogs` `/api/gallery` `/api/token-stats`。

## 10. 插件体系

- PluginManager：自动发现 PLUGIN_META 子包，priority 升序派发 before_reply/after_reply，错误隔离，启停持久化
- plugins/ego（priority=20，聚合）：mood_engine 情绪状态机（六档 + 温度偏移）/ personality_engine（会话状态 + 示例检索 + 夜间学习闭环）/ super_memory（SQLite 五表 + 向量召回 + 三层分级）/ chat_style / humanlike_rhythm / slang_learner
- plugins/group_chat_decider（priority=5）：注意力追踪 + 价值/时机/风险多维评分
- plugins/web_operator / web_search
- 插件可带独立 ui.html 挂载进 WebUI（PLUGIN_ACTIONS 通用通道）

## 11. 记忆体系（外部依赖：NexSandglass + super_memory）

EggBloom 使用两套并行记忆系统，均为外部开源项目：

- **NexSandglass（沙漏记忆）**：独立开源项目（`nexsandglass/` 目录为其源码副本），提供分层渐进式长期记忆（L0 缓冲 → L1 落沙 → L2 检索 → L3 蒸馏）、SimHash 语义检索、画像/偏移率/决策粒子/织布机等 L3 思维能力。EggBloom 通过 `sandglass_bridge.py` 集成，调用入口：消息落沙、对话同步、私聊 prompt 注入（light 模式）、情绪推断。详细架构见 NexSandglass 自身文档。

- **super_memory（plugins/ego/super_memory/）**：EGO 插件内置，SQLite 五表 + bge-small-zh 向量召回，三层分级（永久/长期/短期自动分级）、规则式事实/事件提取、好感度系统。语义召回由 super_memory 承担，关键词召回由 NexSandglass 承担。

与 bot_core 集成：`sandglass_bridge.py` 8 处调用（群/私聊消息落沙、回复同步、私聊 light 注入、表情包情绪推断、状态查询）。

## 12. 设计特征（可对外讲述的点）

1. **前缀缓存工程化**：static_prompt 稳定 + 滚动摘要 + dynamic 内容后置，命中 DeepSeek 前缀缓存省钱，token 命中率可观测
2. **容错降级**：所有外部依赖 try-import 兜底，LLM 失败降级本地统计，requests 降级 urllib
3. **问卷强制工具链**：多轮真实操作 + 完成验证，防假装执行
4. **安全分层**：私聊全工具/群聊白名单 + 命令权限分级 + 密钥入凭据管理器