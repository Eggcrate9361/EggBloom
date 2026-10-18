# proactive_talk —— 主动消息引擎

私聊场景下，无人触发时按「心跳 + 状态」主动开口，补齐 EggBloom 被动响应之外的另一半。
拟人化方向（非恋爱向），人设可在 WebUI 追加配置。

## 群聊为主的环境下的设计取舍

- **主动开口只走私聊**：群里主动发言会打扰一群人，且群插话已有 `group_chat_decider` 管理，本插件不重复造轮子
- **上下文却要看群聊**：target 的言行主要在群里，所以插件会记录「目标本人在群里的发言」，主动私聊开口时能接着群里的话茬（"你刚在群里说的那个事……"）
- **冷却只认私聊**：用户在群里活跃不打断冷却——群里说过话 ≠ 私聊里聊过
- **自行排除 bot 消息**：bot_core 在自我过滤之前就派发 on_message，插件按 `bot_qq` 过滤，避免把自己说的话记成用户上下文

## 工作原理

```
心跳线程（每 heartbeat_sec 秒）
  → 决策闸门：目标存在？最小间隔？用户冷却？静默时段？概率？
  → AI 生成：注入当前时段 / 沉默时长 / 最近对话 / 上次开口 / 惦记的事
  → 按行切气泡（每行 = 一条 QQ 消息，5~25 字，无句号，禁 AI 腔）
  → 逐条 send_private_msg，气泡间按字数模拟打字延时
  → 提炼「惦记的事」存入状态，让下次开口有连续性
```

## 使用

1. WebUI 插件页启用 `proactive_talk`（默认关闭）
2. 打开插件配置页，填目标 QQ（留空则用主配置 `target_qqs` 第一个）
3. 「模拟决策」可干跑一轮看哪个闸门拦截；「立即发一条」跳过闸门直接测试

## 关键配置

| 键 | 默认 | 说明 |
|---|---|---|
| target_qq | 空 | 目标私聊 QQ，空 = target_qqs[0] |
| heartbeat_sec | 900 | 心跳间隔 |
| min_interval_min | 180 | 两次开口最小间隔 |
| active_cooldown_min | 30 | 用户说话后不打扰的冷却 |
| speak_chance | 0.35 | 每次心跳开口概率 |
| quiet_hours | 空 | 静默时段如 `2:00-7:00`，空 = 凌晨也会开口 |
| max_bubbles | 4 | 一次最多气泡数 |
| persona_extra | 空 | 追加人设 |
| nickname | 空 | 怎么称呼对方 |

## 联动 EggBloom 现有能力

EggBloom 的家底多为群聊设计，本插件是私聊场景，所以「挑着接」，全部经本机 HTTP（WebUI 通用通道 / 记忆 API），不 import 主程序，任一通道失败自动降级：

| 来源 | 通道 | 注入内容 |
|---|---|---|
| personality_engine | `POST /api/plugins/action` `get_personality` | 人设原文（10 分钟缓存），主动开口的「它」= 群聊里的「它」 |
| mood_engine | `get_moods` | 当前情绪（六档翻译），影响语气 |
| super_memory | `GET /api/memory/detail?qq=` | 最近 N 轮私聊历史（长期记忆） |

不接的：`super_memory recall` 动作写死 `scope="group"`，私聊用不上；好感度/群画像等同理。
私聊日常上下文由插件自己的 `recent_msgs` 状态记录补充。

配置页有「检测联动」按钮，可分别验证三条通道。

## 边界说明

- 发消息走 `onebot_url` 的 OneBot v11 HTTP 动作（`send_private_msg`），不 import 主程序
- 心跳为 daemon 线程，on_disable / on_unload 即停
- 主动消息不经过 bot_core 的表情包/记忆管线，`[表情:xx]` 占位符会被剥掉
- 状态存 `plugins/state/proactive_talk.json`（最近对话、历史、惦记的事）
