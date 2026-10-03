# EggBloom 核心流程（实测版）

> 整理：2026-10-03
> 可信度声明：**本文只写我逐行核对过、能给出行号的内容。** 未经核对的不写。
> 复核方法见文末「附录：可复现的验证命令」。
> 配套详解（覆盖更全，**但未经逐条复核**）：
> [`deepdive/bot_core-flow.md`](deepdive/bot_core-flow.md)（202 行 / 46KB，12 项全覆盖）、
> [`deepdive/webui_server-flow.md`](deepdive/webui_server-flow.md)（589 行 / 79KB，15 节，含 43 条风险与 8 项未能确认）。
> 本文与它们的分工：**本文是我逐行核实过的结论；它们是我抽样核对过的详细展开。**

---

## 一、进程与端口拓扑

```
EggBloom Desktop.exe（Electron 外壳，无 asar，改代码重启即生效）
   │  main.js：挑 Python → spawn webui/webui_server.py（cwd = resources\app）
   │            并守护它：崩溃后 3s 起自动重启，退避到最多 30s
   ▼
webui_server.py :8080   ← Electron 窗口就是加载 http://localhost:8080
   │  自己再拉起 bot_core.py 子进程
   ▼
bot_core.py
   ├── :3005 事件服务（ThreadingHTTPServer）← 协议层推送 OneBot 事件
   └── 出站调用 OneBot HTTP API（onebot_url，默认 127.0.0.1:3000）
   ▼
协议驱动 SnowLuma（node.exe + index.mjs）→ QQ 客户端
```

**端口一览**：8080 WebUI｜3005 bot_core 事件服务｜3000/3001 SnowLuma HTTP/WS｜3100/3101 NapCat｜5099 SnowLuma WebUI｜6099 NapCat WebUI。

---

## 二、bot_core 启动与初始化顺序

模块级（import 阶段）按顺序做这些事，**每一段都包在 try/except 里，失败只降级不致命**：

| 顺序 | 做什么 | 失败后果 | 行号 |
|---|---|---|---|
| 1 | import 标准库 + 第三方（requests 可选） | — | 顶部 |
| 2 | 配置加载（`config.load_config`） | — | — |
| 3 | 表情包引擎 `meme_engine` | 无表情包 | ~110 |
| 4 | 自主学习 `self_learning` | 无自主学习 | ~50 |
| 5 | **插件系统** `init_plugin_manager()` → `set_host_api()` → `load_all()` | `PLUGIN_OK=False`，全部插件不可用 | 120-129 |
| 6 | 安装全局异常钩子 `sys.excepthook` / `threading.excepthook` | 未捕获异常不被记录 | 1075-1076 |

> ⚠️ 第 5 步的 `set_host_api()` 是 **2026-10-03 新增**（ABI v2）：在 `load_all()` **之前**注入 `api_call`，
> 这样插件在 `on_load` 时就能用。注入用 lambda 延迟解析 —— 因为 `api_call` 定义在 1085 行，此处尚未定义。

入口 `def main()` 在 **6506 行**。

---

## 三、消息主链路（端到端，全部实测）

```
协议层推送
   │  POST http://127.0.0.1:3005
   ▼
EventHandler.do_POST                     bot_core.py:733
   │  （服务起在 928：ThreadingHTTPServer(("127.0.0.1", 3005), EventHandler)）
   ▼
MSG_QUEUE.put(body)                      bot_core.py:916
   │  （MSG_QUEUE = queue.Queue() 定义在 935）
   ▼
主循环 while True                        bot_core.py:6678
   │
   ├─ 排空队列 while not MSG_QUEUE.empty()     6684
   │     │
   │     ├─ 只处理 post_type == "message"      6690
   │     │
   │     ├─ 【启动基线】丢弃 time < startup_time 的历史消息
   │     │   防止协议层登录时把历史消息当新消息灌进来   6694-6697
   │     │
   │     ├─ 【去重】优先 message_id（同秒多条不丢）    6699-6702
   │     │         无 id 时回退时间戳水位线 LAST_MSG_TIME  6703-6704
   │     │         水位线推进时 save_state()            6705-6707
   │     │
   │     └─ 【解析消息段】text 段拼接；完整段保留
   │         （forward / reply / at / face / image 等）  6708-6711
   ▼
按 msg_type 分流 → group 分支 / private 分支
   ▼
AI 回复
```

**三个设计要点值得记下来**（都是踩过坑才有的）：

1. **启动基线过滤**（6694）—— 否则每次登录 QQ 都会把历史消息当新消息处理一遍。
2. **去重双策略**（6699-6704）—— 只用时间戳水位线会丢掉"同一秒内的多条消息"，所以优先用 `message_id`。
3. **队列排空而非阻塞取**（6684）—— 一次循环把队列取空，与另一路轮询消息合并处理。

---

## 四、AI 回复链路与插件注入点

群聊侧的关键注入点（**旧式 hooks，实测**）：

| 位置 | 调用 | 作用 |
|---|---|---|
| 159 | `pm.execute_hooks("before_reply", …)` | EGO 上下文收集（`_run_ego_before_reply`，定义在 142） |
| 6141 | `pm.execute_hooks("before_reply", …)` | 群聊决策链路里的注入 |
| 6261 | `pm.execute_hooks("before_reply", …)` | **主注入点**：构造 `_plugin_ctx` 后派发 |
| 6476 | `pm.execute_hook("after_reply", …)` | 发送后改写 |
| 7431 | `pm.execute_hook("after_reply", …)` | 私聊侧发送后改写 |

**注入契约**（读 6261-6271 得出）：插件返回 `{"action": "skip" | "inject", ...}`，
按插件 `priority` 聚合；`skip` → 不回复，`inject` → 内容并入 system prompt。

`_plugin_ctx` 传给插件的上下文字段（6246-6260）：`recent_msgs` / `bot_replies` / `sender` / `user_id` /
`is_at_bot` / `has_xiao_a` / `has_bot_nick` / `has_quote` / `has_text_at` / `in_session` /
`history` / `bot_qq` / `bot_name`。

### 4.1 AI 客户端是怎么来的（有个时序陷阱）

| 项 | 行号 |
|---|---|
| `call_deepseek(...)` 定义 | **5879** |
| `AIClient(...)` 实例化 | **6586** |
| 注入插件系统：`pm.set_ai_client(_ac)` | **6587** |
| system prompt 静态缓存 `_STATIC_PROMPT_CACHE` | 1725 / 1734 / 1787-1789 |

> ⚠️ **时序陷阱**：`AIClient` 是在 `main()` 内部（6586）才创建并注入的。
> 也就是说 **插件在模块 import 阶段拿不到 AI 客户端** —— `ctx.ai_chat()` 会返回 `None` 并打日志
> 「AI 客户端未注入」（见 `abi.py`）。插件若在 `on_load` 里直接调 `ctx.ai_chat()` 就会踩到。
> 正确做法：在 `on_message` / `on_event` 这类**运行时钩子**里调，或自己判断返回 `None` 时降级。

### 4.2 人设注入链（`load_personality`）

入口 **1782**。优先级从高到低：

| 顺序 | 来源 | 行号 |
|---|---|---|
| 1 | **蒸馏人格卡** —— 由「这样脆弱的东西」插件发布 | `ego/personality_engine._distill_persona()`（89-108） |
| 2 | EGO 人格引擎 `get_personality()["raw"]` | `bot_core.py:1793-1796` |
| 3 | 直接读 `personality.txt`（4 个候选路径） | 1816-1821 |
| 4 | 代码内置的兜底人格字符串 | 1836 起 |

**缓存机制**：纯 mtime 缓存。第 2 步拿到的 EGO 内容也**用 `personality.txt` 的 mtime 当 key**
（1804-1808）—— 所以「发布新人格卡后必须 touch 一下 `personality.txt`」，
否则 bot 会一直用旧缓存。这正是 `plugins/这样脆弱的东西/publisher.py:100` 那个 `_touch_personality()` 存在的原因。

> 🔴 另一处已记录的行为：`_distill_persona()` **只判断发布文件在不在，完全不看插件开关**。
> 所以关掉「这样脆弱的东西」插件 ≠ 撤销人格注入。要真正下线必须调它自己的 `disable`。

system prompt 是拼接出来的，带静态缓存（1725 / 1734 / 1787），上限 8 个键防内存增长（1788-1789）。

---

## 五、🔴 关键发现：真插件 ABI 的钩子是「半残」的

我把每个 typed 钩子在 `bot_core.py` / `plugin_manager.py` / `abi.py` 里的出现次数都数了一遍：

| typed 钩子 | bot_core 派发 | 结论 |
|---|---|---|
| `on_load` / `on_enable` / `on_disable` / `on_unload` | 0 处（**由 manager 自己调用，合理**） | ✅ 正常 |
| `on_message` | **2 处（6830 / 6834）** | ✅ 唯一被派发的业务钩子 |
| **`on_before_reply`** | **0 处** | ❌ **ABI 承诺了，从未派发** |
| **`on_after_reply`** | **0 处** | ❌ **同上** |
| `on_event` | 0 处（**由 `ctx.emit` → `manager.emit_event` 驱动，不走 bot_core**） | ✅ 2026-10-03 已接通 |

### 这意味着什么

**用真插件 ABI 写的插件，只能被动收消息，无法干预 AI 回复。**
而"干预 AI 回复"恰恰是插件往 prompt 注入人格/记忆/上下文、或改写/拦截回复的唯一途径。

结果是：**真插件 ABI 无法承载一个有主动性的插件** —— 这对 1.5 要做的
「agent harness」是硬阻塞（harness 必须能往 prompt 里注入计划、工具结果、观察）。

### 建议的修法（未实施）

在 bot_core 已派发旧式钩子的 5 个位置（159 / 6141 / 6261 / 6476 / 7431），**并列补一次 typed 派发**：

```python
# 与旧式钩子并列，互不影响
if pm and pm.has_typed_hook("on_before_reply"):
    typed = pm.execute_typed_hooks("on_before_reply", event={...})
```

然后把 typed 返回值（`Optional[dict]`）合并进既有的 `intervention` 聚合逻辑（6263 起）。
**必须在同一处聚合，否则两套钩子的 skip/inject 会打架。**

> 这属于 1.5 的功能改动，不在"整理"范围内，**本次只记录不实施** —— 它在 AI 回复热路径上。

---

## 六、`webui_server.py` 路由表（基准：**51 条唯一路径**）

> 计数口径（经复核修正）：**唯一路径 51 条**；按「路径 + 方法」注册计 **55 条**
> （`/api/config`、`/api/plugins/action`、`/api/protocol/config`、`/api/protocol/logs` 各有 GET + POST）。
> 我最初报的 52 条里含一个**非路由**项 —— 1327 行的 `path.startswith("/api/")` 前缀判断。

按功能分组（完整列表见 `_ref/deepdive/webui_server-flow.md`）：

| 分组 | 条数 | 端点 |
|---|---|---|
| **protocol** | 13 | `status` `start` `stop` `switch` `config` `detect` `auto-detect` `classify` `check` `fs` `logs` `logs/clear` `open-site` |
| **memes** | 6 | `memes` `upload` `import` `delete` `tags` `emotions` |
| **llama** | 5 | `start` `stop` `scan` `test` `open` |
| **imagegen** | 4 | `generate` `status` `test` `workflow` |
| **plugins** | 4 | `plugins` `action` `page` `toggle` |
| **bot** | 3 | `start` `stop` `status` |
| **config** | 3 | `config` `config/model` `config/reload` |
| **memory** | 3 | `memory` `memory/list` `memory/detail` |
| **providers** | 2 | `providers/catalog` `providers/models` |
| 单个 | 8 | `status` `tools` `command` `llm/test` `token-stats` `qq-path` `chatlogs` `gallery` |

合计：13 + 6 + 5 + 4 + 4 + 3 + 3 + 3 + 2 + 8 = **51** ✓

**协议面板占了 13 条，是最大的一块** —— 与"协议页是前端最重的页面（25,562 字符）"一致。

### 已核实的路由细节

| 端点 | 行号 | 作用 |
|---|---|---|
| `/api/status` | 910 | 主程序状态 |
| `/api/plugins/page` | 1468 | 服务插件 UI 页（`PROJECT/plugins/<id>/ui.html`，1479） |
| `/api/plugins` | 1988 | 插件列表 |
| `/api/plugins/action` | 1512 | **通用插件通道** → `_invoke_generic_plugin`（526） |
| `/api/protocol/*` | 2160-3397 | 协议面板全套 |
| `/api/bot/start` / `stop` | 3050 / 3055 | 拉起 / 停止 bot_core 子进程 |

`_invoke_generic_plugin` 的三级约定（读 526-579 得出）：
1. `PLUGIN_ACTIONS = {"name": fn(params)}` ← 优先
2. `get_config()` / `set_config(params)` / `toggle()`
3. `execute_action(action, params)`

### 6.1 ⚠️ 项目里其实有**两套** `/api/*`，容易混淆

| 服务 | 端口 | 面向 | 端点 |
|---|---|---|---|
| `webui_server.py` | **8080** | 浏览器 / Electron 窗口 | 52 条（上表） |
| `bot_core.py` | **3005** | 协议层推送事件；**同时也提供联系人管理 API** | 11 条（下表） |

**两套都有 `/api/status`** —— 读到这个名字时必须先确认是哪个端口。

`bot_core.py` 自己的 11 个端点（3005）：

| 端点 | 行号 | 作用 |
|---|---|---|
| `/api/status` | 534 | bot 自身状态 |
| `/api/groups` | 571 | 群列表（含 Bot 角色探测） |
| `/api/groups/members` | 626 | 群成员 |
| `/api/groups/settings` | 798 | 每群限制（`group_restrictions`） |
| `/api/groups/action` | 829 | 群操作（改名/禁言/踢人/设管理员…） |
| `/api/friends` | 671 | 好友列表 |
| `/api/friends/settings` | 851 | 每好友限制（`friend_restrictions`） |
| `/api/friends/action` | 880 | 好友操作（改备注等） |
| `/api/plugins` | 710 | 插件列表（bot 视角） |
| `/api/plugins/test` | 753 | 插件测试 |
| `/api/admin/notify_test` | 781 | 管理员通知测试 |

> 前端「联系人管理 / 群管理」页实际打的是 **3005 这一套**，不是 8080 那套 —— 排障时要分清。

---

## 七、群聊决策、缓冲合并与退场（实测）

### 7.1 缓冲合并

| 项 | 值 | 行号 |
|---|---|---|
| 合并窗口（条） | `GROUP_MSG_BUFFER_WINDOW`，默认 3 | 236 |
| 缓冲容器 | `GROUP_MSG_BUFFER: {group_id: {...}}` | 455 |
| 并发保护 | `_buffer_lock = Lock()` | 412 |
| 处理入口 | `process_buffered_message(group_id)` | 6091 |
| 取缓冲 | 先 `pop` 再处理（保证只处理一次） | 6094 |

**定时器延时策略**（同一个群多次触发会 `cancel` 旧定时器再重设）：

| 情形 | 延时 |
|---|---|
| 会话中（`in_session`） | 5 秒 |
| 被直接触发但还没有上下文 | 15 秒 |
| 其余 | 3 秒 |

→ 设计意图：被 @ 但对方还没说完，多等一会儿把上下文凑齐；已在会话里就快速接话。

### 7.2 触发条件（六个标志）

`is_at_bot` / `has_xiao_a` / `has_bot_nick` / `has_quote` / `has_text_at` / `in_session`

**关键闸门在 6125**：六个都为假 → 不回复。

| 项 | 行号 |
|---|---|
| `_group_trigger_words()` | 5936 |
| 默认触发词 `["小a", "@占位名片A", "@10000001"]` | 5938 |
| 会话中但非直接触发 → 走另一分支 | 6153 |
| 标志在缓冲里累积 | 6136 |

> ⚠️ **一个容易误判的点**：`_config.get("group_trigger_words", [默认])` 只在 key **不存在**时才用默认值。
> 当前 `bot_config.json` 里该键存在但为空 → 返回 `[]`，**默认触发词不生效**。
> 所以实际是"含昵称 / 含@文本"两个开关在兜底 —— 这与 8 月那份《配置中心全览》的提示一致。

### 7.3 会话与退场

| 项 | 值 | 行号 |
|---|---|---|
| 会话存活 | `SESSION_TTL`，默认 300 秒 | 229 |
| 退场总开关 | `GROUP_IDLE_EXIT_ENABLE`，默认 True | 238 |
| **静默下限硬保护** | `GROUP_IDLE_EXIT_MIN_SILENCE_FLOOR = 30` | 242 |
| 生效值 | `_idle_exit_min_silence()` 取 `max(配置, 30)` | 244-250 |
| 退场语（短 / 空闲） | `EXIT_PHRASES_SHORT` / `EXIT_PHRASES_IDLE` | 471 / 480 |
| 退场语清洗 | `strip_exit_phrases()` | 1393 |
| 退场表情默认 | `["崩溃","委屈","烦躁","落寞"]` | 1651 |

> **30 秒下限是踩坑后的补丁**：8 月那份配置文档记着"设 12000 秒还是 47 秒就退场"的 bug。
> 现在无论配置填多小，都不会低于 30 秒。

---

## 八、bot 生命周期与安全防护（实测）

### 8.1 webui 如何拉起 bot_core

```
/api/bot/start   webui_server.py:3050
   ▼
start_bot()      3896
   ├─ 已在运行？直接返回（_bot_lock 保护）
   ├─ 必须有 bot_qq，否则返回「请先到配置中心填入 Bot QQ 号」
   ├─ _get_bot_cmd()  3881  → 决定用哪个解释器、哪个工作目录
   └─ Popen(cmd, cwd=cwd, env=…, stdout=PIPE, stderr=STDOUT, CREATE_NO_WINDOW)
```

`_get_bot_cmd()`（3881）的两种形态：

| 形态 | 命令 | cwd |
|---|---|---|
| 冻结（打包） | 同级目录或 `_internal/` 下的 `EggBloom_v3_Core.exe` | 该 exe 所在目录 |
| 源码 | `[sys.executable, PROJECT/bot_core.py]` | `PROJECT`（即 `resources\app`） |

> **关键点：用的是 `sys.executable`** —— 谁在跑 webui_server，bot_core 就用同一个解释器。
> `main.js` 用**包内 Python** 起 webui_server，所以 bot_core 也跑在包内 Python 上。
> 这正是"包内 Python 缺依赖会连带影响 bot_core"的原因（已于 2026-10-03 装齐）。

环境变量固定设置：`PYTHONUNBUFFERED=1`、`PYTHONIOENCODING=utf-8`；Windows 下加 `CREATE_NO_WINDOW`。

停止：`/api/bot/stop`（3055）→ `stop_bot()`（3932）→ `terminate()` → 等 5 秒 → `kill()`。

### 8.2 端口与 `last_port.txt`

| 项 | 行号 |
|---|---|
| 绑定 `127.0.0.1` | 3995 |
| `pick_port(preferred=8080, tries=20)`（顺延最多 20 个端口） | 4001 |
| `run(host, port)` → 实际端口写入 `PROJECT/last_port.txt` | 4007 / 4015-4017 |

> 🔴 **隐患**：`main.js` 把 URL **写死**成 `http://localhost:8080`（169 / 173 / 306）。
> 一旦 8080 被占用、服务顺延到 8081，Electron 窗口会一直加载失败并按 `did-fail-load` 每 2 秒重试 8080 ——
> **窗口永远出不来**，而服务其实正常运行在 8081。
> 后端已经准备了 `last_port.txt`，但前端从未读它。

### 8.3 安全防护

| 防护 | 实现 | 行号 |
|---|---|---|
| **防 DNS rebinding** | `_host_allowed()`：Host 头必须是本机，否则 403 | 1239 / 1297-1301 |
| **CORS 仅本机** | `_allowed_origin()`：只对 localhost / 127.0.0.1 / ::1 / file:// 返回 ACAO | 1224 |
| **防目录穿越** | `unquote` → `resolve()` → 必须仍在 `HERE` 内 → 且扩展名在 `_STATIC_EXTS` 白名单 | 1328-1335 / 67 |
| **API Key 掩码** | `_mask_secret()` 输出 `«redacted:xxxxx…»`；`_is_redacted_key()` 防止把掩码写回凭据管理器 | 84 / 91 / 72 / 806 |

> 目录穿越防护的注释（1329）写明了它修的是什么：
> 此前 `HERE / path.lstrip("/")` 不过滤 `..`，**本机任意进程都能读到 `../bot_config.json` 与 `model-debug.log`**。

---

## 九、跨切面发现汇总（全部有行号证据）

| # | 发现 | 证据 | 状态 |
|---|---|---|---|
| 1 | **`ctx.emit` 曾是空壳**，插件之间无法通信 | `abi.py:154`（改前） | ✅ 已修（ABI v2） |
| 2 | **插件无法调 OneBot 动作** | ABI 无 `api_call` | ✅ 已修（ABI v2） |
| 3 | **typed `on_before_reply` / `on_after_reply` 从未派发** | 三个文件里 bot_core 出现 0 次 | ❌ **未修，1.5 处理** |
| 4 | `data_store.py` 两棵树同名不同物（一棵是 JSON 工具、一棵是目录管理器） | 见整理报告 P0 | ✅ 已合并 |
| 5 | 三套各自为政的定时器 | `bot_core` Timer / `proactive_talk` 心跳 / `slang_learner` 间隔 | 🔄 调度器已就绪，待收编 |
| 6 | `private_memory.py` / `message_router.py` / `onebot_client.py` / `log_utils.py` 无人 import | 全项目检索 0 命中 | ⬜ 待删（M5） |
| 7 | **端口顺延后前端仍写死 8080** → 8080 被占用时 Electron 窗口永远出不来（服务其实正常） | `pick_port`:4001 / `last_port.txt`:4015-4017 **vs** `main.js`:169,173,306 | ❌ 未修 |
| 8 | `group_trigger_words` 存在但为空 → 默认触发词不生效（易误判为"配了却不好使"） | `bot_core.py:5938` 的 `.get(key, default)` 语义 | ⬜ 待确认是否预期 |
| 9 | **`personality_temperature` 是死参数** —— EGO 情绪温度一路透传但从未被使用 | `5659→5184→5882/5892`，而 `_call_chat_sync:5578` 只用全局 `AI_TEMPERATURE` | ❌ 未修 |
| 10 | **群聊没有 GROUP_IDS 白名单** —— Bot 所在**任何群**都会参与回复 | 群分支只查 `reply_enabled`（6862-6870）；适配器转发全部事件（`onebot_std_adapter.py:390-401`） | ⚠️ 可能是设计如此，需确认 |
| 11 | `private_memory_context` **恒为空** —— 私聊画像功能实际未生效 | 唯一调用点传 `pm_ctx = ""`（7377 / 7413）；`private_memory.py` 从未被 import | ❌ 未修 |
| 12 | **锁形同虚设** —— `_replies_lock`/`_meme_lock`/`_group_meta_lock` 从未使用；`_buffer_lock` 只在一处用，而缓冲写入（7026-7218）与 Timer 线程 `pop`（6094）都不加锁 | 全项目 grep | ❌ 未修 |
| 13 | `save_state` 用裸 `except: pass`（1893）吞掉落盘失败 —— 状态静默丢失 | 1893 | ❌ 未修 |
| 14 | 死代码：`_chunk_text`(1183)、`_filter_message_line`(6047)、`EXIT_PHRASES_SHORT`(471)；`/api/chatlogs`(1386)、`/api/plugins/page`(1468) 是前端从不调用的死端点 | grep 核实 | ⬜ 待删 |

### 9.1 两条安全问题（我已亲自验证，含**准确的影响面**）

#### A. `/memes/*` 缺路径校验 → 可读 app 目录下任意文件

**证据**：静态分支有防护（1328-1335：`unquote` → `resolve()` → 必须仍在 `HERE` 内 → 扩展名白名单），
但**该分支判定失败后不 `return`**，控制流落到 2311 的 memes 分支；
而 `_serve_meme`（1148-1174）是 `file_path = meme_dir / rel` —— **没有 `resolve()`、没有前缀校验、没有扩展名白名单**。

`_meme_dirs()`（1146）= `PROJECT/memes/memes` 与 `PROJECT.parent/memes/memes`，
所以 `../../` 可上溯到 `resources/app/` 与 `resources/`。

**能读到什么**（实测）：`bot_config.json` **不含 `api_key`**（该字段本就不落盘），
但**含 `onebot_token`**；此外 `.neurobase/sandglass.db`（记忆库）、`chat_logs/`、`plugins/state/*.json` 等都在范围内。

**影响面要说准**：回复走 `_serve_meme` 手工写响应头（1165-1169），**不带 ACAO**，
因此跨站 fetch 读不到内容；而 Host 校验（1297）挡住了 DNS rebinding。
→ **这是加固缺陷（defense-in-depth 缺失），不是远程可利用漏洞。**
但项目自己的威胁模型注释（2340）把"本机进程"排除在外，而浏览器地址栏/本机任意程序可直接读 —— 建议仍修。

**修法**：`_serve_meme` 里对 `file_path` 做与静态分支同样的 `resolve()` + 前缀校验 + 扩展名白名单；
并让静态分支失败时 `return`（发 404），不要继续往下走。

#### B. `GET /api/plugins/action` 可改状态，却没有 Origin 校验（CSRF）

**证据**：`/api/plugins/action` 在 `do_GET` 也被处理（1512），参数从 **query string** 取（1515-1516）；
但 `do_GET` **只有 Host 校验（1297），没有 Origin 校验** ——
`_allowed_origin` 只出现在 1250 / 1270 / 1288，那是**响应头**里决定要不要给 ACAO，**不是请求闸门**。
Origin 闸门只在 `do_POST`（2339-2340 有明确注释），GET 路径漏了。

**影响面**：恶意网页可用 `<img src="http://127.0.0.1:8080/api/plugins/action?...">` 触发**无参数或只吃简单参数**的动作
（`params` 走 query 会变成字符串，复杂的会失败）。响应读不到（无 ACAO），但**副作用已经在服务端发生**。
→ 属真实 CSRF 面，但可利用的动作范围受限。

**修法**：把 `do_GET` 里的 `/api/plugins/action` 归入需要 Origin 校验的路径（或干脆只允许 POST）。

---

## 十、附录：可复现的验证命令

```powershell
$w = "E:\EggBloom水星\EggBloom Desktop-new\resources\app"

# 1) 事件服务与队列 —— 实测命中行号：16, 916, 928, 935, 6684, 6686
#    （733 是 do_POST，需另加 pattern 才命中）
Select-String -Path "$w\bot_core.py" -Pattern 'MSG_QUEUE|ThreadingHTTPServer'
Select-String -Path "$w\bot_core.py" -Pattern 'def do_POST'

# 2) typed 钩子接线矩阵 —— 实测：on_message=2，其余三个业务钩子=0
foreach ($h in @('on_message','on_before_reply','on_after_reply','on_event')) {
  "$h -> " + (Select-String -Path "$w\bot_core.py" -Pattern "`"$h`"" | Measure-Object).Count
}

# 3) 旧式钩子相关行 —— 实测命中：157, 159, 6141, 6243, 6261, 6475, 6476, 7431, 7433
#    其中真正的「派发调用」是 159, 6141, 6261, 6476, 7431；其余是 has_hook 判断
Select-String -Path "$w\bot_core.py" -Pattern 'execute_hooks?\(|has_hook\('

# 4) 路由数 —— 三个口径要分清
(Select-String -Path "$w\webui\webui_server.py" -Pattern '"/api/' -AllMatches).Count   # → 59（行）
(Select-String -Path "$w\webui\webui_server.py" -Pattern '["''](/api/[A-Za-z0-9_\-/]*)["'']' -AllMatches |
  ForEach-Object { $_.Matches } | ForEach-Object { $_.Groups[1].Value } |
  Sort-Object -Unique).Count                                                            # → 52（字符串）
# ↑ 52 里含 1327 行那个非路由的前缀判断 '/api/'，扣掉才是真实路由数 51
# 按「路径 + 方法」注册计则是 55 条（config / plugins/action / protocol.config / protocol.logs 各双方法）

# 5) 群聊决策与退场 —— 实测命中：242, 250, 6091
#    （6125 的六标志闸门与 6153 的会话分支需另用 pattern 才命中）
Select-String -Path "$w\bot_core.py" -Pattern 'def process_buffered_message|GROUP_IDLE_EXIT_MIN_SILENCE_FLOOR'
Select-String -Path "$w\bot_core.py" -Pattern 'not is_at_bot and not has_xiao_a'

# 6) 端口顺延 vs 前端写死 —— 实测：前者 4001 / 4017，后者 main.js 169 / 173 / 306
Select-String -Path "$w\webui\webui_server.py" -Pattern 'def pick_port|last_port\.txt'
Select-String -Path "$w\main.js" -Pattern 'localhost:8080'

# 7) bot 生命周期（应命中 3050 / 3055 / 3881 / 3896 / 3932）
Select-String -Path "$w\webui\webui_server.py" -Pattern 'def _get_bot_cmd|def start_bot|def stop_bot'
```
