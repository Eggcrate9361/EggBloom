# EggBloom 核心流程（实测版）

> 整理：2026-10-03
> 可信度声明：**本文只写我逐行核对过、能给出行号的内容。** 未经核对的不写。
> 复核方法见文末「附录：可复现的验证命令」。
> 配套详解（由子代理产出，覆盖更全）：`_ref/deepdive/bot_core-flow.md`、`_ref/deepdive/webui_server-flow.md`

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

## 六、`webui_server.py` 路由表（我机械提取的基准，共 52 条）

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
| 单个 | 9 | `status` `tools` `command` `llm/test` `token-stats` `qq-path` `chatlogs` `gallery` `/api/` |

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

---

## 七、跨切面发现汇总（全部有行号证据）

| # | 发现 | 证据 | 状态 |
|---|---|---|---|
| 1 | **`ctx.emit` 曾是空壳**，插件之间无法通信 | `abi.py:154`（改前） | ✅ 已修（ABI v2） |
| 2 | **插件无法调 OneBot 动作** | ABI 无 `api_call` | ✅ 已修（ABI v2） |
| 3 | **typed `on_before_reply` / `on_after_reply` 从未派发** | 三个文件里 bot_core 出现 0 次 | ❌ **未修，1.5 处理** |
| 4 | `data_store.py` 两棵树同名不同物（一棵是 JSON 工具、一棵是目录管理器） | 见整理报告 P0 | ✅ 已合并 |
| 5 | 三套各自为政的定时器 | `bot_core` Timer / `proactive_talk` 心跳 / `slang_learner` 间隔 | 🔄 调度器已就绪，待收编 |
| 6 | `private_memory.py` / `message_router.py` / `onebot_client.py` / `log_utils.py` 无人 import | 全项目检索 0 命中 | ⬜ 待删（M5） |

---

## 八、附录：可复现的验证命令

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

# 4) 路由数 —— 注意：按行计数是 59，按「唯一路径」才是 52
(Select-String -Path "$w\webui\webui_server.py" -Pattern '"/api/' -AllMatches).Count   # → 59（行）
(Select-String -Path "$w\webui\webui_server.py" -Pattern '["''](/api/[A-Za-z0-9_\-/]*)["'']' -AllMatches |
  ForEach-Object { $_.Matches } | ForEach-Object { $_.Groups[1].Value } |
  Sort-Object -Unique).Count                                                            # → 52（唯一路径）
```
