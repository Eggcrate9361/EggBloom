# 1.5.8 后续修复计划（群聊回复流水线收口）

目标版本：`1.5.8`（原地覆盖，不新增版本号）
依据：2026-10-25 对 1.5.8 运行版的日志与源码核对
范围：四个代码提交 + 一个文档提交

2026-10-25 已定口径：
- 戳一戳并入缓冲区，由群聊决策器报告动作（合并成一轮，或不回复）。
- 戳一戳表情包并入主链的表情流程，不再单独发。
- 戳一戳与同类动作进日志页。

本文每条结论都标注了仓库行号或日志原文。行号以本文件所在提交的 `bot_core.py` 为准；
运行版 `E:\EggBloom水星\EggBloom Desktop-new\resources\app\bot_core.py` 的对应逻辑
在 `7507` 行，与本仓库 `7668` 行同构（行号差 161），下文只引用仓库行号。

---

## 0. 版本号

`package.json` 的 `version` 保持 `1.5.8`（2026-10-25 定）。本次修复随 1.5.8
重新打包，发行页与 tag 原地覆盖，不新增版本号。

代价与做法：

- 已下载过 1.5.8 的用户收不到升级提示，需要手动重新下载。发行页要写明
  "同日重新打包"，三个附件的时间与 digest 都会变。
- `v1.5.8` 需要再次强制移动（这个 tag 已经移动过一次），三个附件整体替换。
- 仓库里没有 `chore(release)` 提交，`package.json` 与 `pyproject.toml` 都不动；
  `CHANGELOG.md` 的 1.5.8 段追加一节"重新打包"，写明这次多修了什么。
- 回滚：把 `v1.5.8` 移回上一提交并重新打包该提交。旧附件如果没有留底，
  digest 无法复原，只能以新产物为准。

---

## 1. 结论

1. 三个现象：戳一戳绕过缓冲区自行回复、同群能并发生成两条回复、日志页看不到戳一戳。
2. 三个现象不是三个独立 bug，是同一条链路上的五个缺口。一条群消息从进来到发出去，
   在代码里被拆成几段互不相识的实现：

   | 阶段 | 现有实现 | 缺的东西 |
   |---|---|---|
   | 入站 | `main()` 群消息分支内联（`11930-12284`）；`_handle_poke_event`（`7668`）自带 prompt、模型调用与发送 | 没有统一的事件结构，戳不是"一种消息" |
   | 状态 | `GROUP_MSG_BUFFER`（`600`）、`GROUP_SESSION`（`594`）、`GROUP_RECENT_MSGS`（`766`）、`GROUP_BOT_REPLIES`（`775`）、`GROUP_OBSERVE`（`765`）五张表各自维护 | 没有"这个群现在处于哪一步" |
   | 决策 | 只有 `10216` 的无触发分支进决策器；被点名、会话内、戳一戳都直接进模型 | 决策不是必经点，无法在一处观察和改口径 |
   | 出口 | `process_buffered_message`（`10130`）有四个直呼点：`12156`、`12167`、`12248`、`9838` | 没有互斥，同群可并行 |
   | 观测 | `collect_rounds`（`webui/webui_server.py:1391`）13 条正则（`1305-1323`） | 阶段与 notice 类事件不可见 |

3. 因此本次不按"三个补丁"编排，按一条流水线编排：C1 建管道（唯一入站、每群相位、
   单一决策点、串行出口），C2 把戳接到管道的入站口，C3 让日志页能看见管道里的动作。
4. 提交顺序按依赖排，不按现象排。第一稿是先接戳（C1）再加闸（C2），这等于把新入口
   接到一条还没有闸的管道上：戳接入后仍可能与在途批次并发，只是并发窗口变小。
   本稿把两者对调，编号与串行顺序不变，内容互换。
5. MaiBot 把"收消息 / 判该不该说 / 生成 / 分条发"分给不同阶段，按聊天流隔离会话状态
   （见 3.4 的对照与出处）。本项目缺的正是这层阶段划分，不是缺一个戳一戳分支。
   本次只借用它的阶段口径，不引入它的频率窗口、重推理打断、长期记忆与插件生态（3.6）。
6. 不动的东西：`smart_send_group`（`2239`）的分条规则、`is_similar_to_recent`（`2464`）
   的去重池、观察二次决策（`9810`）、静默时段（`10179`）、私聊链路。

---

## 2. 证据

### 2.1 戳一戳通道（源码）

| 事实 | 位置 |
|---|---|
| 事件在主循环里同步分发 | `bot_core.py:11540-11544`，`main()` 定义于 `11036` |
| 处理函数 | `bot_core.py:7668` `_handle_poke_event` |
| 反戳动作 | `7668` → `_send_poke_back` `7616` |
| 写会话额度与戳主 | `7690-7697`，`poke_session_replies` 默认 2 |
| 直接调模型 | `7734` `call_deepseek` |
| 直接发送 | `7748` `smart_send_group` |
| 再发表情包 | `7752-7758` |
| 只写去重池、不查去重池 | `7738` `track_bot_reply`；`is_similar_to_recent` 只在 `10715` / `10730` 被调用 |

对照主链，戳一戳绕开了 `_batch_delay`（`3113`）、`_batch_deadline`（`3171`）、
`GROUP_MSG_BUFFER`、`is_similar_to_recent`（`2464`）四处。

### 2.2 群回复并发（源码）

- `process_buffered_message`（`10130`）入口即 `pop`：`10135-10136`，注释写明锁只包住 pop。
- 两个 `Timer` 创建点：`12156`（合并进已有批次）、`12248`（新建批次），回调都是
  `process_buffered_message(gid)`，函数内没有任何"已在处理"的判断。
- 直接调用点另有两处：`12167`（缓冲区过期立即处理）、`9838`（观察窗口二次决策）。
- 模型侧最坏耗时：`providers.py:218` 等 `timeout=120`，`max_attempts` 取 `max_retries`
  （`providers.py:148`，默认 3），重试走 `harness/retry_policy.py` 指数退避、
  单次封顶 8 秒（`harness/retry_policy.py:58`），单轮最坏约 `3×120+2×8 ≈ 376` 秒。

### 2.3 日志页缺 notice（源码）

- 前端唯一数据源：`webui/webui_artistic_v4.html:7777` `API('/rounds', ...)`，页面函数
  `pageChatlogs` 在 `7687`。
- 服务端解析器：`webui/webui_server.py:1391` `collect_rounds`，全部正则在 `1305-1323`：
  `_RND_EVT` / `_RND_ASK_PM` / `_RND_ASK_QUOTE` / `_RND_ASK_GRP` / `_RND_ROUTE` /
  `_RND_TOOLS` / `_RND_SEARCH` / `_RND_TOOLREC` / `_RND_DECISION` / `_RND_REPLY_PM` /
  `_RND_REPLY_GRP` / `_RND_SEND` / `_RND_COST`。没有一条匹配 `戳一戳`。
- 不匹配的行没有兜底分支，循环体末尾只处理 `[发送]`（`1532-1541`），其余直接进入下一行。
- `_RND_REPLY_GRP`（`1321`）要求行首是 `群回复:`；戳一戳回复写作
  `群[829854136] 戳一戳回复:`（`7749`），不匹配。
- `_RND_ASK_GRP`（`1314`）要求 `群\[(\d+)\]`；事件里的群号可能是显示串
  `远望酒庄(991461217)`（见 2.4 日志原文），带名字的群一律不匹配。
- 附带影响：`_rnd_scope_from_event`（`1348`）把 `gid=远望酒庄(991461217)` 原样当 scope key
  写进 `last_scope`（`1424-1428`），后续无法判定 scope 的轮次会继承这个假 key。
- `/api/events`（`collect_events` `1284`，分类函数 `_classify_event` `1149`）其实已经能
  把戳一戳归进 `message`，`tests/test_event_classify.py:63-74` 有用例钉着；但前端没有调用它。

### 2.4 真机日志时间线

数据源：`%LOCALAPPDATA%\EggBloom_v3\qq_bot.log`，2,784,318 B / 29,692 行，覆盖
`09:05:24`–`17:06:01`。该文件跨多个构建累积（例如 `14:47:34` 的
`戳一戳触发会话模式` 是旧版文案，现行代码写作 `戳一戳解锁追问通道`），
因此**跨天的次数统计不能当作单一版本的指标**；下文只引用 17:03-17:06 这一段，
其格式与当前 HEAD 一致。

```
[17:04:06] 群[829854136] 新建缓冲区 delay=5.0s in_session=True
[17:04:11] 群[829854136] 合并消息: [(/ω＼)] 你是谁啊？我不认识你
[17:04:19] 群[829854136] 新建缓冲区 delay=5.0s in_session=True      ← 上一批还在生成
[17:04:24] 群[829854136] 合并消息: [碱式碳酸大脑] #sl
[17:04:44] AI 回复 (7622 tokens, ...)                               ← 上一批才返回
[17:04:51] 群回复: #sl又刷上了，闲的是吧
[17:05:02] 群[829854136] 新建缓冲区 delay=5.0s in_session=True
[17:05:10] 群[829854136] 合并消息: [外莱塔尼亚] 孩子们，体测完感觉自己有点死了
[17:05:14] 群[829854136] 新建缓冲区 delay=5.0s in_session=True      ← 又一批并行
[17:05:19] 群[829854136] 合并消息: [外莱塔尼亚] 我恨体育
[17:05:22] [事件] 戳一戳: user_id=(/ω＼)(3150007406) group_id=829854136
[17:05:22] 群[829854136] 戳一戳解锁追问通道(仅戳主 (/ω＼)(3150007406)，额度2)
[17:05:31] 群回复: 草
[17:05:31] 群[829854136] 戳一戳回复: 刚问完我是谁又来戳我                ← 同秒两路
[17:05:32] 群[829854136] 戳一戳表情包: 烦躁
[17:05:37] 群[829854136] 新建缓冲区 delay=3.0s in_session=False back_grab=7
[17:05:47] 群回复: 恨也没用，下周还得跑
[17:05:51] AI 回复 (7312 tokens, ...)
[17:05:54] AI 回复 (7040 tokens, ...)                              ← 同一批两次生成
[17:05:59] 群回复: 不是不骂
[17:06:00] 群回复: 骂谁？
```

可复现的读取方式（先复制，再读副本）：

```powershell
Copy-Item "$env:LOCALAPPDATA\EggBloom_v3\qq_bot.log" "$env:TEMP\opencode\qq_bot_real.log" -Force
```

注意：直接用 Store 版 Python 打开 `%LOCALAPPDATA%` 会命中 MSIX 重定向
（`D:\WpSystem\...\LocalCache\Local\...`），拿到的是旧快照，不能用于统计。

`17:05:51` 与 `17:05:54` 两次生成之间没有 `新建缓冲区`、也没有
`重复回复` / `重试` 日志，具体是哪两个 Timer 撞上的无法从日志断定；
"同群同窗口可以并发产生两次生成"这一条是确定的，归因需要 C1 落地后的相位日志来确认。

### 2.5 五个缺口与三个提交的对应关系

| # | 缺口 | 证据 | 提交 |
|---|---|---|---|
| 1 | 入站不归一 | 2.1：戳一戳自带 prompt 与发送 | C2 |
| 2 | 每群无相位 | 2.2：四个直呼点互相不知道对方在跑 | C1 |
| 3 | 决策不是必经点 | `10216` 只在无触发分支进决策器 | C1 |
| 4 | 出口不互斥 | 2.2：入口即 pop，生成在锁外 | C1 |
| 5 | 观测面缺 notice | 2.3：13 条正则无 `戳一戳` | C3 |

缺口 2、3、4 在同一段调度里，拆成三个提交会反复改同一个函数；合成一个 C1，
用"相位 + 唯一出口"一次解决。缺口 1 只改入站，独立成 C2。缺口 5 只改解析，独立成 C3。

---

## 3. 目标流水线

### 3.1 四个执行相位 + 一个收批窗口

```
[群消息]  ─┐
[戳一戳]  ─┼─→ INBOUND ─→ ACCUMULATE ─→ DECIDE ─→ GENERATE ─→ DELIVER
[观察回看]─┘   归一化        收批/定关窗      单一决策点    模型        分条发送
                    │             │              │            │            │
                    │        GROUP_MSG_BUFFER    └─ GROUP_PIPELINE 相位 ────┘
                    │        + deadline
                    └─ 只做归一化与查重，不持有相位
```

相位只描述"执行中的那一段"，共四个，存在 `GROUP_PIPELINE[gid]["phase"]`。
收批不设相位：批次是否在等，由 `GROUP_MSG_BUFFER` 里有没有该群、以及 `deadline`
决定。这样互斥判断只有一个条件，不必在"正在收批"和"正在生成"之间加特例。

| 相位 | 含义 | 进入条件 | 离开条件 |
|---|---|---|---|
| `idle` | 该群没有批次在执行 | 初始状态、上一批结束 | 批次到期 |
| `deciding` | 决策中 | deadline 到、或过期批次被立即结算 | `_decide` 返回 |
| `generating` | 模型生成中 | act=reply | 生成返回 |
| `sending` | 分条发送中 | 生成有内容 | 发送完成 |

转移规则：

- 批次未过 deadline、同群又来新事件：合并正文并按 `_batch_delay`（`3113`）重算 deadline，
  相位不变（仍可能是 `idle`，因为收批不占相位）。
- deadline 到：`idle` → `deciding`。
- `idle` 时发现缓冲区已过期（新事件到达时读到旧批次）：先结算旧批，再给新事件建批。
- 非 `idle` 时又有批次到期：保持原相位，`pending=True`，不另起生成。
- 一批结束且 `pending=True`：立即再跑一轮，不再等窗口（窗口已经过了）。
- `act=skip` → `idle`；`act=observe` → `idle`，另起观察 Timer（`9791`）。

### 3.2 两个数据结构

入站事件（`_pipeline_upsert` 的入参，C1 引入）：

```python
{
    "kind": "message",              # message / poke；后续可扩 recall / member
    "group_id": "829854136",
    "sender": "3150007406",
    "text": "[昵称] 正文",           # 已带说话人前缀的整行
    "images": [],
    "flags": {"at_bot": False, "xiao_a": False, "bot_nick": False,
              "quote": False, "quote_me": False, "text_at": False},
    "forced": False,                # True = 被点名/引用我/会话内，决策器直接放行
    "source": "message",            # 只进日志，不参与判断
}
```

每群相位（C1 引入）：

```python
GROUP_PIPELINE: Dict[str, Dict[str, Any]] = {}
# {gid: {"phase": "idle|deciding|generating|sending",
#        "since": float,        # 进入当前相位的时间，超时接管与日志用
#        "pending": bool,       # 在途期间又有批次到期
#        "last_reply_at": float}}
```

相位与载荷分开：`GROUP_PIPELINE` 只存相位与互斥标记，批次正文仍在
`GROUP_MSG_BUFFER`（`600`）。这样 C1 不必搬动 `11930-12284` 的 230 行解析逻辑，
也保住现有测试对缓冲区结构的断言。

### 3.3 决策点契约

`_decide(group_id, batch) -> dict`，返回值固定四个键：

```python
{"act": "reply" | "skip" | "observe",
 "reason": str,          # 进日志与日志页
 "ctx": str,             # 传给生成端的氛围/策略结论
 "after": float}         # act=observe 时的回看秒数
```

- `forced=True`：不调决策插件，直接 `{"act": "reply", "reason": "触发"}`，
  日志记 `[决策放行] 触发`。这是新增的一行，用来让"为什么回"在日志里可查。
- `forced=False`：跑现有 `10216-10295` 的逻辑（决策插件 + typed 派发 + observe），
  结论翻译成上面四个键。
- 副作用（写 `GROUP_OBSERVE`、打日志）留在调用点，`_decide` 本身只读数据，便于单测。
- 其余闸门不动，仍在生成前：开机冷却（`10163`）、静默时段（`10179`）、
  会话配额（`10297`）。它们不是"该不该说"的判断，是"现在能不能说"的闸。

### 3.4 与 MaiBot 的对照

MaiBot 的公开文档把链路写成：消息进聊天流 → Planner 判断该不该说 → Replyer 生成 →
回复分割器分条发送；会话状态按聊天流隔离。这五段在本项目里其实都在，只是没有分开命名，
也没有共同的相位。

| 阶段 | MaiBot | 本项目 1.5.8 | 本次改法 | 说明 |
|---|---|---|---|---|
| 入站 | 消息进聊天流，按 `scope` 归属 | 群消息在 `main()` 内联；戳走独立通道 | `_pipeline_upsert(event)` | 戳成为 `kind="poke"` 的事件 |
| 是否开口 | Planner + `talk_value` + `reply_trigger_mode`（frequency/dynamic）+ `inevitable_at_reply` | 决策器只在无触发分支 | `_decide` 成为必经点，forced 短路 | 不引入频率窗口，理由见 3.6 |
| 打断 | `planner_interrupt_max_consecutive_count`；快速反应模式：思考、输出或等待期间收到新消息立即重新推理 | 每次到期新起 Timer，无上限 | 在途闸 + `pending`，至多排队一轮 | 选择排队而非重推理，理由见 3.6 |
| 生成 | Planner 决策 → Replyer 生成 | 决策器 → 主模型 | 不变 | 两段已存在 |
| 分条 | 回复分割器（`max_sentence_num`） | `smart_send_group`（`2239`）按行/标点/长度 | 不变 | 已等价 |
| 重复抑制 | 回复后处理（1.0.12 "减少重复回复"） | `is_similar_to_recent`（`2464`）+ `track_bot_reply` | 不变，戳并入后共用同一池 | |
| 会话状态 | 每聊天流独立状态 | 五张散表 | `GROUP_PIPELINE` 记相位 | 载荷仍在 `GROUP_MSG_BUFFER` |
| 静默/冷却 | `talk_value`、离线模式 | `STARTUP_COOLDOWN`（`10163`）、`quiet_hours`（`10179`）、`SESSION_TTL`（`10158`） | 不变 | 挂在生成前 |

出处（2026-10-25 读取）：

- 频率触发/动态触发、`planner_interrupt_max_consecutive_count`、回复分割器
  `max_sentence_num`：<https://docs.mai-mai.org/faq/chat-and-reply>
- `talk_value` / `inevitable_at_reply` / `mentioned_bot_reply` / `reply_trigger_mode`：
  <https://docs.mai-mai.org/manual/configuration/bot-config>
- Planner 快速反应模式、1.0.12 "优化 Planner 到 Replyer 的信息传递并减少重复回复"、
  1.3.6 消息路由 `scope` 归属：<https://docs.mai-mai.org/changelog>

本文只对照官方文档的当前术语（聊天流 / Planner / Replyer / 回复分割），
不引用其内部类名，也不假设其源码结构与本项目相同。

### 3.5 为什么拆成两个提交，顺序为什么对调

- 依赖方向：闸是管道属性，戳是入口。先有闸，入口才不可能绕过；
  如果反过来先接入口、后加闸，中间那一步仍然保留并发，只是窗口变小。
- 回滚粒度：C1 单独回退 → 戳回到缓冲区外、相位表消失，行为回到 1.5.8；
  C2 单独回退 → 戳回到旧通道，管道的闸仍在，只影响戳这一种事件。
- 不合并成一个大重构：C1 要动 230 行解析、四个直呼点、五张表；一次改完，
  出问题时无法判断是相位、入站还是决策的锅。

### 3.6 借用边界

- 不引入 `talk_value` 频率窗口与动态触发：本项目已有决策器与"点名必答"，
  再叠一层概率会让"为什么这次没回"更难解释。要调活跃度，改决策器 prompt 或它的规则。
- 不引入重推理式打断：单轮最坏约 376 秒（2.2），重推理会把同一批的模型调用翻倍，
  而用户看到的结果与"排到下一轮"相同。排队是成本更低的等价物。
- 不做"思考/表达"两段独立模型：决策器已经是一段轻量调用，主模型负责表达。
- 不引入长期记忆、关系、日程、插件生态：与本次缺口无关。

---

## 4. 提交分布

一次提交只做一件事，按下面顺序串行，每一步可单独回滚与验证。

| 编号 | 提交标题 | 主要文件 | 解决 |
|---|---|---|---|
| C1 | `refactor(bot): 10-25 群回复收口为每群流水线` | `bot_core.py`、新增 `tests/test_group_pipeline.py` | 缺口 2/3/4 |
| C2 | `fix(bot): 10-25 戳一戳接入群回复流水线` | `bot_core.py`、`tests/test_bot_core_events.py` | 缺口 1 |
| C3 | `fix(webui): 10-25 日志页补齐 notice 事件` | `webui/webui_server.py`、新增 `tests/test_rounds_parse.py` | 缺口 5（戳/撤回/成员/禁言） |
| C4 | `fix(webui): 10-25 原始日志并入 data/logs 并统一格式` | `webui/webui_server.py`、`webui/webui_artistic_v4.html`、新增 `tests/test_log_merge.py` | 控制台原始日志覆盖与可读性 |
| C5 | `docs(changelog): 10-25 补全 1.5.8 重新打包条目` | `CHANGELOG.md` | 发行说明 |

提交标题格式按 `CONTRIBUTING.md`（`124-132` 的类型表）与 `tests/test_commit_format.py`：
`<type>(<scope>): <MM-DD> <做了什么>`。

版本号不变，所以没有 `chore(release)` 提交。发布动作（打包、强移 tag、替换附件）
不属于提交，见第 7 节。

第一稿把 notice 拆成 C3（戳）与 C3b（撤回/成员）。两者改的是同一个
`collect_rounds`、同一批正则、同一个测试文件，拆开只是让同一个函数被改两次，
没有独立的回滚价值，本稿合成 C3。

---

## 5. 各提交的改法

### C1 `refactor(bot): 10-25 群回复收口为每群流水线`

现状
- 入站逻辑内联在 `main()` 的群消息分支（`11930-12284`），合并与建批两套分支各写一遍
  deadline 计算；戳一戳另起一条通道。
- 出口四个直呼点（`12156` / `12167` / `12248` / `9838`），互相不知道对方在跑。
- 决策埋在 `process_buffered_message` 中段（`10216-10295`），只有无触发分支会走到。

改法

1. 状态区（`594` 附近）新增：

   ```python
   GROUP_PIPELINE: Dict[str, Dict[str, Any]] = {}
   _pipeline_lock = Lock()        # 只保护 GROUP_PIPELINE，不复用 _buffer_lock
   REPLY_PHASE_MAX = 420          # 单个相位最长持有时间，依据见 2.2
   ```

   `_pipeline_lock` 独立于 `_buffer_lock`（`539`）：`_buffer_lock` 非重入（`12078`
   的注释记录过一次死锁），相位判断不该和缓冲区读写抢同一把锁。

2. 新增唯一入站函数 `_pipeline_upsert`，把 `11930-12284` 的两套分支收进一处：

   ```python
   def _pipeline_upsert(group_id, *, sender, text, images=None, flags=None,
                        forced=False, source="message", reply_mid=0):
       """把一个入站事件并入该群批次；必要时建批并启动 Timer。

       flags:  {"at_bot","xiao_a","bot_nick","quote","quote_me","text_at"}，缺省全 False
       所有 Timer 一律在 _buffer_lock 之外创建与 start()。
       """
   ```

   `forced` 的取值沿用现在的口径，由 `main()` 的群分支传入：
   `is_at_bot or has_xiao_a or has_bot_nick or quote_me or has_text_at or in_session`
   （即 `10216` 那个条件的反面）。决策器只在 `forced=False` 时被调用。

   批次（`GROUP_MSG_BUFFER[gid]`）新增两个键：`forced`（缺省 False，合并时取或）与
   `poke_uid`（缺省 None，`source="poke"` 时写入，合并时取最新）。
   `poke_uid` 只被 C2 用来在回复成功后发追问权，不参与触发判断。

   函数内部分四步：
   - 持 `_buffer_lock` 读缓冲区。
   - 有批次且未过 `deadline`：合并正文、或上各标记、取消旧 Timer、按
     `_batch_deadline`（`3171`）重算 deadline、锁外 `start()`。
   - 有批次且已过期：调 `process_buffered_message(group_id)` 结算。它返回 `False`
     表示在途，此时**重新持锁再读一次**：还是同一个批次对象就并进去；已经不在了
     （在途线程刚 pop 掉）就按下一步新建批次，不能往已摘下的字典里写，
     否则这条消息直接丢失。这是第一稿 `_buffer_upsert` 漏掉的点。
   - 无批次：按现有 `12238-12284` 的字段建批，写入持锁、Timer 锁外启动。

3. `process_buffered_message` 拆成两层，函数名保持不变（三个测试直接调它，
   两个测试按源码文本定位，见"验证"）。
   `_pipeline_run` 的"取相位"与"续跑"必须在同一把锁里完成：先置 `idle` 再回到
   循环顶部检查，会让第三个线程在两步之间插进来，等于闸漏了一次。

   ```python
   def process_buffered_message(group_id):
       """流水线唯一出口。返回 False 表示已排队，或该群没有待处理批次。"""
       return _pipeline_run(group_id)

   def _pipeline_run(group_id) -> bool:
       if not _pipeline_acquire(group_id):
           return False
       while True:
           try:
               _pipeline_step(group_id)
           except Exception:
               _pipeline_release(group_id)
               raise
           if not _pipeline_release(group_id, again=True):
               return True

   def _pipeline_acquire(group_id) -> bool:
       """取相位。已被占用则置 pending 并返回 False；超时则强制接管。"""
       with _pipeline_lock:
           st = GROUP_PIPELINE.setdefault(group_id, _new_pipeline_state())
           if st["phase"] != "idle":
               if time.time() - st["since"] < REPLY_PHASE_MAX:
                   st["pending"] = True
                   return False
               log(f"[流水线] 群[{group_id}] {st['phase']} 超时"
                   f"{int(time.time() - st['since'])}s，强制接管")
           _phase_set_locked(st, "deciding")
           st["pending"] = False
           return True

   def _pipeline_release(group_id, again: bool = False) -> bool:
       """结束本轮。有待批时在锁内直接续到 deciding，返回 True。"""
       with _pipeline_lock:
           st = GROUP_PIPELINE.get(group_id)
           if st is None:
               return False
           if again and st["pending"]:
               st["pending"] = False
               _phase_set_locked(st, "deciding")
               return True
           st["pending"] = False
           _phase_set_locked(st, "idle")
           return False

   def _pipeline_step(group_id):
       """原 process_buffered_message 的主体：pop、决策、生成、分条发送。"""
   ```

   `_pipeline_step` 在进入生成、发送前分别把相位置为 `generating` / `sending`，
   供日志与超时接管判断；`_phase_set` 内部持 `_pipeline_lock`。
   续跑时相位从 `deciding` 直接接上，不经过 `idle`，避免别的线程在缝里再进一次。

4. 抽 `_decide(group_id, batch)`：把 `10216-10295` 的判断整段搬进去，返回 3.3 的四个键。
   `_pipeline_step` 里只留一次调用与三个分支。`forced` 由入站事件写入 `batch["forced"]`，
   合并批次时取或（任一条被点名，整批都算触发）。

5. 四个直呼点收敛：
   - `12156` / `12248` 两个 Timer 仍然回调 `process_buffered_message`，自然经过闸，不改。
   - `12167` 的"过期立即处理"并入 `_pipeline_upsert` 的第 3 步，删除这处直呼。
   - `9838` 的观察回看改为先看相位：`idle` 才写合成批次并调用；非 `idle` 时
     记一行 `[观察] 群[x] 正在{phase}，本次回看作废` 后返回，不覆盖待批数据。

6. 相位日志统一前缀，供 C3 与排查使用：

   ```
   [流水线] 群[829854136] idle → deciding 批=3条
   [流水线] 群[829854136] deciding → generating
   [流水线] 群[829854136] generating → sending 2条
   [流水线] 群[829854136] sending → idle
   ```

   只在相位变化时打，不逐条打。

行为变化
- 同群永不同时跑两次生成；在途期间到达的消息排队，最多延迟一轮。
- 触发批次多一行 `[决策放行] 触发`，日志页能看到"这轮为什么回"。
- 缓冲区过期分支不再插队，改为"先结算旧批，再给新事件建批"。

不改
- `_batch_delay` / `_batch_deadline` 的档位公式与三个配置键（`3146-3157`）。
- `smart_send_group` 的分条规则与打字延迟（`2239`）。
- `_schedule_observe` / `_observe_second_look` 的"只回看一次"语义（`9791`、`9810`）。
- 开机冷却、静默时段、会话配额三道闸的位置。

验证
新增 `tests/test_group_pipeline.py`：
1. 两个线程同时 `process_buffered_message(gid)`，`_pipeline_step` 只被进入一次。
2. 在途时第二次调用返回并把 `pending` 置真；本轮结束后恰好再跑一次。
3. `_pipeline_step` 抛异常时相位回到 `idle`，该群还能继续回。
4. 相位持有超过 `REPLY_PHASE_MAX` 时被接管。
5. `_decide` 在 `forced=True` 时不调决策插件、返回 `reply`。
6. `_decide` 在 `act=observe` 时不调模型，写 `GROUP_OBSERVE`。
7. `_observe_second_look` 在相位非 `idle` 时不覆盖 `GROUP_MSG_BUFFER`。
8. `_pipeline_upsert` 的合并/新建/过期三条分支各一条，deadline 仍取 `_batch_delay`。
9. 过期批次在途（已 pop）时再来一条消息：新批次被建出来，消息没有写进已摘下的字典。

回归
- 直接调 `process_buffered_message` 的三个用例：`test_image_understanding.py:120`、
  `test_meme_back.py:183`、`test_observe_e2e.py:136`。它们现在会经过闸，
  用例之间要 `GROUP_PIPELINE.clear()`，避免 `pending` 跨用例残留。
- 按源码文本定位的两个用例需要同步改锚点：`test_quiet_hours.py:101` 用
  `def process_buffered_message(` 找静默闸的位置，`test_quote_forces_defined.py:57`
  的 `ALLOWED` 集合要求 `quote_forces` 只出现在白名单函数里。主体搬到
  `_pipeline_step` 后，两处都要改成 `_pipeline_step`，否则断言会落在包装函数上。
  这是 C1 对既有测试的唯一改动。
- 行为回归（不直接调出口）：`test_bot_core_events.py`、`test_batch_delay.py`、
  `test_decider_pacing.py`。

风险
- 单群串行化会拉长热闹群的响应间隔。当前观测到的并发正是问题本身，串行是目标。
- 若 `_pipeline_step` 里有合法路径阻塞超过 420 秒，会被误判卡死并接管。
  上线后观察 `[流水线]` 的超时行一周。
- `process_buffered_message` 从"立刻处理"变成"保证被处理"，语义变化要写进
  docstring，避免后来的人以为它会同步跑完。

回滚
单提交回退，回到四个直呼点与内联决策，不影响 C2/C3。

### C2 `fix(bot): 10-25 戳一戳接入群回复流水线`

现状
- `_handle_poke_event`（`7668`）的群分支自己拼 prompt（`7708-7732`）、调模型（`7734`）、
  发送（`7748`）、发表情包（`7752-7758`），全程不碰缓冲区。

改法
1. 群分支保留反戳（`7682`）、昵称解析（`7698`）与计数（`7699-7707`），
   删除 `7708-7749`（近况拼装、分级文案、poke prompt、模型调用与发送），改为一次入站：

   ```python
   _pipeline_upsert(gid_str, sender=poke_uid,
                    text=f"[{poke_nick}] （戳了戳你，这是第 {poke_n} 次）",
                    forced=False, source="poke")
   ```

2. `forced=False` 是关键：这样批次进入 3.3 的决策点，由群聊决策器报出
   `[决策放行]` / `[跳过]` / `[观察]`。若把它当会话内消息（`forced=True`），
   `10216` 这一关被整个跳过，戳就只剩 `10478` 的二次决策兜底，
   日志里也不会出现这套判断。延迟因此走普通档 3 秒（`3156-3157`），不是会话档 5 秒。

3. 表情包并入主链：删除 `7752-7758`，交给 `_pipeline_step` 现有的表情流程
   （真机日志 `[17:04:51] [表情] 解析结果` 即该路径），由表情引擎按回复内容
   决定发不发、发哪张。

4. 保留会话解锁，但把写额度挪到"戳这一批确实发出之后"（2026-10-25 定，选项 3）：
   - 删除 `7690-7697` 的提前写入，以及 `7697` 的"戳一戳解锁追问通道"日志。
   - `_pipeline_upsert` 在 `source="poke"` 时把发起人记进批次 `poke_uid`；
     同批多人戳时后一条覆盖前一条（最近戳的人拿额度）。
   - `_pipeline_step` 在分条发送之后才写
     `GROUP_SESSION[gid] = {"remaining": poke_session_replies,
     "last_msg_time": time.time(), "poke_owner": poke_uid}`，
     并打 `群[x] 戳回复完成，解锁追问通道(仅戳主 uid，额度N)`。
   - `skip` / `observe` / 生成为空 / 发送抛异常都不写。观察回看的批次没有
     `poke_uid`，所以"稍后回"也不补发额度。
   - "发送成功"按 `smart_send_group` 未抛异常判定。它本身不返回成功与否，
     本次不扩大这个接口。

5. 戳一戳不写入 `GROUP_RECENT_MSGS` / `GROUP_TIMELINE`：它是系统事件，不是人话。
   写进去会让后续回抓把"（戳了戳你，这是第 N 次）"当成群友发言重复注入。
   本批正文里会出现一次，够模型用。

6. 私聊分支（`7761-7824`）不动。私聊没有群缓冲区，本次问题不涉及。

行为变化
- 戳一戳不再即时回复，改为等 3 秒普通档窗口（`_batch_delay` 的 else 档）。
- 这 3 秒内到达的消息与戳合成一批，一起交给模型。
- 决策器三选一，动作直接落日志：
  - `reply`：与同批消息合成一轮，日志 `[决策放行]`（`10295`）。
  - `skip`：不回复，日志 `[跳过]`（`10290`）。
  - `observe`：稍后再看，日志 `[观察]`（`10287` → `_schedule_observe`）。
- 回复要过 `is_similar_to_recent`（`10715`），与主链共用去重池。
- 戳主的 2 轮免 @ 追问权保留，但只在戳那一批真的发出后才生效；
  `skip` / `observe` / 生成失败都不生效。
- **戳一戳不再保证有文字回复。** 表情包也由主链决定，可能没有图。
- 主循环不再被阻塞：真机 `17:05:22`→`17:05:31` 的 9 秒占用消失，事件收取不再滞后。
- 日志页：poke 轮由 C3 建，决策动作由 `_RND_DECISION`（`webui_server.py:1319`）
  归进该轮的 `decisions`。

不改
- `_send_poke_back`（`7616`）的 action 兜底顺序与返回码日志。
- `POKE_MEME_EMOTIONS`（`3265`）常量本身（私聊分支 `7817` 仍在用）与表情匹配规则。
- 私聊戳一戳的即时回复（`7798-7824`）。

验证
- `python tests/test_bot_core_events.py`：`TestPokeEvent`（`386-470`）里四个群戳用例
  断言的是旧行为，需逐条改写：
  - `test_group_poke_creates_session_and_replies`（`423`）→ 改断言"写入 `GROUP_MSG_BUFFER`、
    未调 `call_deepseek`、未调 `smart_send_group`、入站时不写 `GROUP_SESSION`"。
  - `test_group_poke_empty_reply_no_send`（`438`）→ 删除，空回复由主链自己的用例覆盖。
  - `test_group_poke_count_escalates_vibe`（`443`）→ 改为断言两批 buffer 文本里分别带
    "第 1 次" / "第 2 次"。
  - `test_group_poke_other_target_ignored`（`432`）保持不变。
- 新增：同一秒内两次戳 + 一条普通消息 → 只有一个 buffer，延迟取普通档 3 秒。
- 新增：戳之后走 `_decide`，`skip` → 不调模型、不发送；`reply` → 只调一次模型、只发一次。
- 新增：戳的批次 `forced=False`、`source="poke"`（防止以后被改回绕过决策器）。
- 新增：戳不写 `GROUP_RECENT_MSGS`。
- 新增：戳被 `skip` → 该群 `GROUP_SESSION` 没有新增额度；被 `reply` 且发送完成 →
  写入且 `poke_owner` 是戳的人、`remaining` 等于 `poke_session_replies`。
- 新增：戳与别人消息合并成一批时，额度只记给戳的人（`poke_uid` 取最近一条戳）。
- 全量：`for f in tests/test_*.py; do python "$f" || echo "FAIL: $f"; done`。

风险
- 用例里 `_pipeline_upsert` 会真起一个 `Timer`，测试结束前要么 `timer.cancel()`，
  要么像现有 `test_bot_core_events.py:281` 那样塞一个假 timer，避免 3 秒后回调打到 mock 上。
- 额度后置后，戳的回复还在生成时到达的追问不再算 `in_session`，会先过决策器。
  这是有意的：额度只给已经被回应的人。这个窗口通常是 10-40 秒。
- "发送成功"以 `smart_send_group` 未抛异常为准，它不返回成功与否；
  发送层部分失败时仍会写额度，本次不扩大这个接口。
- 戳文本 `[昵称] （戳了戳你，这是第 N 次）` 去前缀后超过 10 字，不命中
  `_is_short_meaningless`（`9931`，>8 字直接放行）与 `_is_nonsense`（`9964`，>10 字直接放行），
  不需要额外豁免；但仍要跑一遍 `10315-10346` 的逐行过滤。

回滚
单提交回退。回退后戳回到独立通道，C1 的闸仍在，最坏等于 1.5.8 的并发。

### C3 `fix(webui): 10-25 日志页补齐 notice 事件`

现状
- `collect_rounds` 只认消息与群回复，notice 类事件整类不可见；
  带名字的群号还会污染 `last_scope`。

改法
1. 新增 scope 归一化函数，两处使用：`_rnd_scope_from_event`（`1348`）返回前套一层；
   `_RND_ASK_GRP` 的匹配从 `群\[(\d+)\]` 放宽为 `群\[([^\]]+)\]` 再归一化。

   ```python
   def _scope_key(label):
       """'829854136' / '远望酒庄(991461217)' / '私聊' → '829854136' / '991461217' / 'pm'"""
   ```

2. 新增正则（`1305-1323` 区），按"更具体的先"排序：

   ```python
   _RND_POKE_EVENT = re.compile(r"^\[事件\]\s*戳一戳:\s*user_id=(.*?)\s+group_id=(\S+)$")
   _RND_POKE_REPLY = re.compile(r"^群\[([^\]]+)\]\s*戳一戳回复:\s*(.+)$", re.S)
   _RND_POKE_MEME  = re.compile(r"^群\[([^\]]+)\]\s*戳一戳表情包:\s*(.+)$")
   _RND_POKE_BACK  = re.compile(r"^\[事件\]\s*反戳\s+(\S+)\s+user=(.*?)\s+group=(\S+)")
   _RND_RECALL     = re.compile(r"^\[事件\]\s*撤回:\s*type=(\S+)\s+user_id=(\S+)\s+group_id=(\S+)\s+msg_id=(\S+)")
   _RND_MEMBER     = re.compile(r"^\[事件\]\s*群成员(增加|减少):\s*group=(\S+)\s+user=(\S+)")
   _RND_BAN        = re.compile(r"^\[事件\]\s*群(禁言|解除禁言)")
   ```

   私聊版（`^私聊戳一戳回复:`）同样加一条，scope 记为 `pm`。

3. 解析规则：
   - `_RND_POKE_EVENT` 新建一轮：`ask="（戳了戳你）"`、`who=昵称`、
     `route="戳一戳 → 反戳 → 决策"`、`scope=_scope_key(group_id)`，
     `time` 直接用 `_read_log_rows` 剥出的时间戳。
   - `_RND_POKE_REPLY` 写 `cur["reply"]`（多次则换行追加）。
   - `_RND_POKE_MEME` 写 `cur["tools"]`，`{"name": "表情包", "q": 情绪名, "note": ""}`，
     复用现有渲染，不改前端。
   - `_RND_POKE_BACK` 追加到 `cur["decisions"]`，形如 `[反戳] send_poke retcode=0`。
   - `_RND_RECALL` / `_RND_MEMBER` / `_RND_BAN` 各建一轮，`ask` 用事件描述，
     `reply` 为空，`route` 写事件名。撤回新增检测的条数见下表。
   - `[事件] 戳一戳忽略` 不进轮次，直接跳过。
   - 不为不匹配的行加通用兜底：那会把堆栈和调试行也画成轮次。

4. 事件行与产生位置：

   | 日志行 | 产生位置 | 8 小时样本里的条数 |
   |---|---|---|
   | `[事件] 撤回: type=… user_id=… group_id=… msg_id=…` | `bot_core.py:7832` | 4 |
   | `[事件] 群成员增加: group=… user=…` | `7501` | 0 |
   | `[事件] 群成员减少: group=… user=…` | `7541` | 0 |
   | `[事件] 群禁言 / 群解除禁言` | `7586` / `7589` | 0 |
   | `[事件] 好友增加: …` | `7597` | 0 |

   后四类在本机样本里没有出现，用例样本需要手工构造。

验证
- 现状没有任何测试直接覆盖 `collect_rounds`（`tests/test_webui_v4_pages.py` 只 mock 了
  `/api/rounds` 的返回，测的是前端）。新增 `tests/test_rounds_parse.py`：
  monkeypatch `_log_paths` 指向临时日志文件，喂一段含戳一戳事件、反戳、回复、表情包的文本，
  断言产出一轮且 `reply` 与 `tools` 齐备；再喂 `群[远望酒庄(991461217)]` 变体，
  断言 scope 归一成 `991461217`；再喂撤回/入群/退群/禁言的构造行，断言各自成轮。
- `python tests/test_api_routes.py`：该测试检查所有 POST 路径都注册在 `do_POST`，
  C3 不新增路由，应保持通过。
- 手工：日志页选中该群，确认戳一戳单独成轮，且群列表不出现 `群 远望酒庄(991461217)`
  这种带名字的重复项。
- 前端不改，`tests/test_webui_v4_pages.py` 应保持通过。

风险
- 正则放宽后，`群[...]` 行可能被多条规则同时匹配。顺序按"更具体的先"排：
  poke 回复、poke 表情、合并消息、引用消息。
- 新增轮次会让日志页同一时段的轮数变多，属预期。

回滚
单提交回退，只影响日志页展示。

### C4 `fix(webui): 10-25 原始日志并入 data/logs 并统一格式`

现状
- `_log_paths()`（`1560`）只含 `core_log.txt`、项目根 `qq_bot.log`、AppData 的
  `qq_bot.log` 与两个 Temp 文件，不含 `data/logs/qq_bot.log`。
- `utils.log` 写的就是 `data/logs/qq_bot.log`（`RotatingFileHandler`，5MB × 5，
  `Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")`），
  `ai_client.py` / `providers.py` / `sandglass_bridge.py` 的日志在那里。
- `_read_log_rows`（`1326`）按文件顺序追加、最后 `rows[-max_lines:]`：
  多文件时时间倒序，跨文件重复行各留一份，外部 logger 的日期前缀也没有归一。

改法
1. `_log_paths()` 追加 `PROJECT / "data" / "logs" / "qq_bot.log"`，并给每个来源一个
   标签：`core` / `app`（项目根）/ `data` / `temp`。标签只给前端显示，不写进正文。
2. `_read_log_rows` 改为"先解析、再排序、后截断"：
   - 时间戳统一：`[HH:MM:SS]`、`YYYY-MM-DD HH:MM:SS,mmm`、`HH:MM:SS` 三种写法都认。
     没有日期的行以该文件 mtime 的日期为基准。
   - 级别抽取：行首的 `[INFO]` / `[WARNING]` / `[ERROR]` / `[DEBUG]`（含 `INFO:` 变体）
     移进 `level` 字段，正文不再重复级别。
   - 折叠重复前缀：同一行连续出现相同的 `[标签]`（如 `[事件] [事件]`）只留一个。
   - 去重：跨文件完全相同的 `(时间, 正文)` 只保留一条。
   - 排序键 `(日期, 时间)`，稳定排序；同一秒内保持文件内的原始顺序。
   - 续行仍然并回上一条（现有行为，`1329-1330` 的注释说明了原因）。
3. 返回结构向后兼容：默认仍返回 `[时间, 正文]`，`/rounds` 与 `/events` 不改；
   原始流另取 `[时间, 正文, 级别, 来源]` 四元组，由调用方选择。
4. 前端原始日志页按 `level` 着色、显示来源徽标。只改渲染，不改筛选与滚动逻辑。

验证
- 新增 `tests/test_log_merge.py`：造两个临时文件，一个 `[12:00:01]`、一个
  `2026-10-25 12:00:00,900 [INFO]`，断言：排序正确、级别被抽走、
  来源标签正确、完全重复行只留一条、`[事件] [事件]` 只留一个。
- 手工：控制台原始日志流里能看到 `Sandglass` 初始化行，行首没有重复的 `[INFO]`。

风险
- 排序从"按文件尾"变成"按时间尾"，首屏时间范围可能变化。
- 日期基准取自文件 mtime：运行了多天的日志里，凌晨的行会归到 mtime 当天，
  跨天排序可能错位。现有样本是单日文件，这个限制本次不解决。
- 轮转文件 `qq_bot.log.1`…`.5` 仍不读；要不要读是另一个决定。
- 去重只针对 `(时间, 正文)` 完全相同：同一秒内两路真的发了同样一句话，会被并成一条。

回滚
单提交回退。

### C5 `docs(changelog): 10-25 补全 1.5.8 重新打包条目`

版本号不变，没有 `chore(release)` 提交。`CHANGELOG.md` 的 1.5.8 段追加一节，
位置在 `### 修复` 与 `### 打包` 之间，标题写 `### 重新打包（同日）`：

- 群回复收口为每群流水线：同群不再并发生成两次回复。
- 戳一戳接入流水线，由群聊决策器裁决合并成一轮或不回复；表情包并入主链。
- 日志页补齐 notice 轮次：戳一戳、撤回、成员、禁言。
- 原始日志并入 `data/logs`，多来源按时间排序、统一级别与来源、去重。

这一节写在 1.5.8 段内，不新开版本号；标题日期仍是 `2026-10-25`。

---

## 6. 验证矩阵

| 场景 | 判定 | 依赖 |
|---|---|---|
| 单次戳，无其他消息 | 3 秒后交给决策器；放行则回一句，skip 则不回；不阻塞事件收取 | C2 |
| 戳 + 同秒内普通消息 | 只产生一批、一次模型调用 | C2 |
| 戳到达时已有批次在生成 | 戳并入待批，不与在途批次并发 | C1 + C2 |
| 决策器对戳 skip | 无模型调用、无发送，日志页该轮显示 `[跳过]` 理由 | C2 + C3 |
| 决策器对戳 reply | 与同批消息合成一轮，日志页显示 `[决策放行]` | C2 + C3 |
| 决策器对戳 skip | 不写追问权，戳主下一条消息仍要过决策器 | C2 |
| 戳回复成功 | 写入 2 轮免 @ 追问权，`poke_owner` 是戳的人 | C2 |
| 连续三条消息分三次到 | 一个批次，一次回复 | C1 |
| 一轮生成中又来消息 | 本轮结束后立即处理待批，不并发 | C1 |
| 模型超时重试 | 相位最终回到 `idle`，该群仍能回复 | C1 |
| 相位卡死 | 420 秒后强制接管，日志有 `[流水线] … 超时` | C1 |
| 日志页 | 戳一戳单独成轮，含决策动作、回复；表情包由主链决定 | C3 |
| 日志页群列表 | 不出现 `群 远望酒庄(991461217)` | C3 |
| 日志页撤回与成员事件 | 各自成轮 | C3 |
| 原始日志多来源 | 按时间排序，级别与来源不写进正文，完全重复行只留一条 | C4 |
| 控制台原始日志 | 含 Sandglass 等 `data/logs` 内容 | C4 |

真机复验用同一条路径抓证据：复制 `%LOCALAPPDATA%\EggBloom_v3\qq_bot.log` 到
`%TEMP\opencode\`，统计"同一秒内出现两条回复"与"同群并发生成"两个指标，目标为 0。

---

## 7. 发布顺序

1. C1 → C2 → C3 → C4 依次提交，每步跑完对应测试。
2. 全量测试：`for f in tests/test_*.py; do python "$f" || echo "FAIL: $f"; done`。
3. 补 CHANGELOG，提交 `docs(changelog): 10-25 补全 1.5.8 重新打包条目`。
4. 打包（Lite / Standard 两个附件 + `SHA256SUMS.txt`）。版本号不改，
   `scripts/activate.py --check-only` 应仍通过。
5. `git tag -f v1.5.8`，再强制推送该 tag（`git push -f origin v1.5.8`），
   让 tag 指向新的 HEAD。
6. 发行页沿用 1.5.8：替换三个附件，重新核对 GitHub digest 与本地一致，
   发行说明取 `CHANGELOG.md` 的 1.5.8 段，并在开头注明"同日重新打包"。

回滚
- 代码：任一提交单独 `git revert`。
- 已发包：把 `v1.5.8` 移回上一提交，重新打包并替换附件；不删除发行页。
  旧附件如果没有留底，digest 无法复原。

---

## 8. 本次不做

- 私聊缓冲区（`tests/test_private_merge.py` 记录私聊是另一套合并逻辑），不在本次范围。
- `_RND_SEND` 只覆盖私聊的问题（群聊发送行没有 `句=`），日志页群回复本来就靠
  `群回复:` 行，本次不扩大。
- `proactive_talk` 心跳线程累计 2,228 次启动对 227 次退出、`scheduler` 751 次加载对
  222 次停止：属于插件生命周期问题，与本次三条结论无因果关系，单独处理。
- 删除或复活 `/api/events`：本次选择补齐 `/rounds`，不改数据源。
- `data/logs/qq_bot.log` 的轮转文件（`.1`…`.5`）是否纳入读取，留给后续决定。
- 引入 MaiBot 式的发言频率窗口、动态触发、重推理打断（理由见 3.6）。

---

## 9. 已定口径

2026-10-25 已定，不再列为待确认：

1. 戳走缓冲区，由决策器决定合并成一轮或不回复；表情包并入主链；戳与同类动作进日志页。
2. 戳主的 2 轮免 @ 追问权保留，写额度后置到回复成功之后（选项 3）。
   `poke_session_replies` 配置键因此保留，配置页与 `tests/test_config_docs_export.py` 都不用改。
3. 版本号保持 1.5.8，原地覆盖发行；没有 `chore(release)` 提交。
4. C4 本轮做，且并入前统一格式（时间戳、级别、来源、去重）。

---

## 10. 与第一稿的差异

1. 编排依据从"三个现象"改为"一条链路的五个缺口"，C1/C2 的内容对调：
   先建管道（相位、唯一入站、单一决策点、串行出口），再接戳一戳。
2. 新增第 3 节：五个阶段的链路、四个执行相位、两个数据结构、决策点契约、
   与 MaiBot 的对照及出处。
3. 合并第一稿的 C3 与 C3b 为一个 C3：同一解析器、同一测试文件、同一判据。
4. 补充 `_pipeline_upsert` 的第三条分支（旧批次过期且相位忙时并入，不覆盖），
   第一稿的 `_buffer_upsert` 没有处理这个数据丢失点。
5. 补充相位超时接管、相位日志前缀、用例间的 `GROUP_PIPELINE.clear()`。
6. 明确写出不借用的四项（频率窗口、重推理打断、双模型分工、记忆与插件生态），
   避免"参考 MaiBot"被理解成整套照搬。
7. 版本号按 1.5.8 原地覆盖编排：删除 `chore(release)` 提交，
   发布动作改为打包后强移 `v1.5.8` 并整体替换附件。
8. 戳主追问权从"入站即写"改为"回复成功后才写"：`skip` 不再白送 2 轮免 @。
9. C4 从"可延后"改为本轮做，并加一层格式统一（时间戳、级别、来源、去重）；
   标题相应改为"并入 data/logs 并统一格式"。
