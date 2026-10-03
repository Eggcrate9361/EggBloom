> ⚠️ **可信度声明**：本文由子代理通读产出（2026-10-03），所有结论均标注行号，但**未经逐条复核**。
> 已由我逐行核实的关键结论见 [../CORE_FLOW.md](../CORE_FLOW.md)。
> 引用本文的具体数字前，请先按行号回查源码。

---

# bot_core.py 流程说明（可核查版）

- 目标文件：`E:\EggBloom水星\EggBloom Desktop-new\resources\app\bot_core.py`（7546 行 / 约 366 KB）
- 行号基准：本文所有行号均取自对该文件的逐行读取（`read` 工具输出），格式 `bot_core.py:1234`。
- 只读核查，未运行 bot_core。凡无法从代码直接确认的，均标注「未能确认」。
- 与调用方核对：父代理给的 `load_personality:1782` 与本文件第 1792 行不一致；本文以实测 `def load_personality()` 位于 **1792** 行为准（`bot_core.py:1792`）。其余核对项（do_POST:733 / MSG_QUEUE.put:916 / 主循环:6678 / 排空:6684 / 启动基线:6694 / 去重:6699-6704 / 段解析:6708 / before_reply:159,6141,6261 / after_reply:6476,7431 / on_message:6834）全部一致。

---

## 1. 启动与初始化顺序

### 1.1 模块级 import 阶段（按执行顺序）

| 顺序 | 行号 | 内容 | 失败后果 |
|---|---|---|---|
| 1 | `8-13` | 标准库 import；自定义 `ThreadingHTTPServer(ThreadingMixIn, HTTPServer)`，`daemon_threads=True` | 无 |
| 2 | `19-28` | `import requests` → `REQUESTS_OK` | False 时 `api_call` 走 urllib 降级（`1104-1109`） |
| 3 | `31-34` | stdout/stderr 强制 UTF-8（windowed 模式为 None 时跳过） | 无 |
| 4 | `37-41` | 把脚本目录与上级目录插入 `sys.path`（为 self_learning/meme_engine 等） | 无 |
| 5 | `44-54` | `from self_learning import get_engine` → `self_learn`，`SELF_LEARN_OK` | False → 所有自学调用被跳过：整群启动学习 `6637-6643`、消息收集 `6976-6977`、回复后学习 `6495-6497`、群上下文注入 `5726-5729` |
| 6 | `56-79` | `import data_store` | ImportError → 内联兜底类 `read_json/write_json`（原子写 tmp+replace） |
| 7 | `81-98` | `from app_state import AppState` → `_ST = AppState()` 单例 | ImportError → 内联单例兜底 |
| 8 | `100-107` | `from slang_injector import get_context_for_llm as get_slang_context`，`SLANG_OK` | False → `get_slang_context = lambda x: ""`，黑话注入静默失效（调用点 `5732-5735`） |
| 9 | `109-118` | `from meme_engine import match_meme, parse_meme_intent, auto_detect_emotion`，`MEME_OK` | False → 三个函数变桩（`match_meme` 恒 None、`parse_meme_intent` 恒 `(x, None)`），表情包功能整体停摆 |
| 10 | `120-139` | 插件系统：`init_plugin_manager()` → `set_host_api(api_call=lambda ...)`（`128-130`）→ `load_all()`（`134`）→ `PLUGIN_OK` | False → 全部钩子派发点短路（`151`、`240`、`6127`、`6827`、`7399`） |
| 11 | `142-193` | 定义 `_run_ego_before_reply()`、`_inject_ego_context()`（此时尚未定义 `api_call`） | 无 |
| 12 | `194-204` | PyInstaller frozen 检测 → `SCRIPT_DIR`；再次修正 `sys.path` | 无 |
| 13 | `206-216` | `from config import load_config` → `_config = load_config()` | 未捕获异常会直接终止进程（无 try/except） |
| 14 | `218-501` | 由 `_config` 派生模块常量：冷却/超时/窗口（`219-239`）、`ONE_BOT_URL`（`252-289`，协议层端口文件 + 3000-3003 扫描）、`BOT_QQ/TARGET_QQS/GROUP_IDS/API KEY/URL`（`290-298`）、各类文件路径（`300-315`）、`EXIT_PHRASES_SHORT/IDLE`（`471-501`） | 无 |
| 15 | `303,314-315` | `os.makedirs` 创建 `_USER_DATA_DIR`、`TEMP_DIR`、`IMAGE_CACHE` | 无 |
| 16 | `382-406` | `CTX`（验证证书）/`LOCAL_CTX`（不验证）；`_SESSION = None` 惰性 requests.Session | 无 |
| 17 | `408-468` | 线程锁（7 把）与全局状态字典（`GROUP_MSG_BUFFER` 等） | 无 |
| 18 | `507-930` | 定义 `start_event_server()`（含内部 `EventHandler` 类），**只定义不启动**；`928` 绑定 `127.0.0.1:3005`，`930` `serve_forever()` | 无（线程在 `6657` 才启动） |
| 19 | `932-941` | `startup_time = 0`、`MSG_QUEUE = queue.Queue()`、昵称缓存与队列 | 无 |
| 20 | `1002` | `GROUP_MEMBERS = {}` | 无 |
| 21 | `1075-1076` | 安装 `sys.excepthook` / `threading.excepthook` → 写入日志（`1066-1072`） | 无 |
| 22 | `5166-5242` | **启动 3 个 AI 工作线程**（`_AI_WORKERS = 3`，`5240-5242`）——这是 import 期的线程副作用 | 无 |
| 23 | `7555-7556` | `if __name__ == "__main__": main()` | — |

### 1.2 `__main__` 入口
`__main__` 只调用 `main()`（`bot_core.py:7555-7556`）。`main()` 内部按顺序做（`6506-6677`）：

1. 打印启动横幅（`6509-6512`）。
2. NexSandglass 记忆引擎后台懒加载 `sandglass_bridge._ensure_init()`，异常仅记日志（`6514-6520`）。
3. 协议层感知日志（`6524-6529`）。
4. 自动拉起 QQ：仅当配置 `qq_path` 存在时用 `tasklist` 探测并 `Popen`，随后 `sleep(5)`（`6531-6554`）。
5. 轮询等待 OneBot 上线：`api_call("get_login_info")`，每 2s 一次，超过 300 次（10 分钟）放弃但**继续运行**（`6556-6570`）。
6. 插件 AI 客户端传播：`from ai_client import AIClient` 构造客户端 → `pm.set_ai_client`（`6572-6590`）。
7. 启动昵称解析后台线程（`6592-6596`）；同步预加载 `TARGET_QQS` 昵称（`6598-6603`）。
8. 预加载每个 `GROUP_IDS` 的群成员列表，填充 `QQ_NICK_CACHE` 与 `GROUP_MEMBERS`（`6605-6634`）。
9. 启动时为每个群触发一次 `self_learn.maybe_learn`（`6636-6643`）。
10. `load_state()`（`6645`）→ `startup_time = time.time()`（`6646-6647`）→ 启动冷却倒计时线程（`6648-6654`）。
11. **启动 3005 事件服务线程**（`6656-6657`）。
12. 黑话学习器可选定时任务（`6659-6671`）。
13. 进入主循环（`6678`，循环体末尾 `time.sleep(3)`，`7552`）。

---

## 2. 事件入口（3005 端口）

- 服务类型：`ThreadingHTTPServer`（多线程）+ `BaseHTTPRequestHandler`，绑定 `("127.0.0.1", 3005)`（`bot_core.py:928`），`serve_forever()`（`930`）。端口**硬编码**，不读配置（未见 3005 之外的端口来源）。启动点 `6657`。
- CORS：`_local_origin()` 只回显 localhost/127.0.0.1/::1/`null` 的 Origin，其余不返回 ACAO 头（`510-518`）；`end_headers` 统一加 ACAO/方法/头（`520-526`）；`do_OPTIONS` 返回 204（`528-530`）；`log_message` 被屏蔽（`925-926`）。
- `do_GET` 路由：`/api/status`（`534`）、`/api/groups`（`571`）、`/api/groups/members`（`626`，`startswith` 前缀匹配）、`/api/friends`（`671`）、`/api/plugins`（`710`），其余 404（`730-731`）。
- `do_POST` 路由：`/reload_config`|`/reload`（`735`）、`/api/plugins/test`（`753`）、`/api/admin/notify_test`（`781`）、`/api/groups/settings`（`798`）、`/api/groups/action`（`829`）、`/api/friends/settings`（`851`）、`/api/friends/action`（`880`）。
- **事件进入队列**（兜底分支）：以上路由都不匹配时，读 `Content-Length`，`0 < length < 65536` 则 `json.loads` 并 `MSG_QUEUE.put(body)`（`912-919`），随后统一返回 200 空体（`922-924`）。即**任意未匹配的 POST 路径 + JSON body 都会被当成 OneBot 事件**（见风险节）。
- 事件的生产者不是 bot_core：`protocols/*_adapter.py` 的 WS 事件桥把框架推送原样转发到 3005（`protocols/onebot_std_adapter.py:390-401`，`DEFAULT_REPORT_URL` 见 `onebot_std_adapter.py:39`、`napcat_adapter.py:35`、`base.py:24`；SnowLuma 见 `snowluma_adapter.py:14`）。适配器**不做群号/好友过滤**（`onebot_std_adapter.py:390-401` 直接 `post_event`）。
- WebUI 也会直接用 3005（`webui/webui_server.py:584`，`webui_artistic_v3.html:2836`），`main_window.py:1322-1323` 用 3005 的 `/reload_config` 做热重载。

---

## 3. 主循环（取事件 → 分发）

链路（`bot_core.py:6678` 起）：

1. **排空队列**：`while not MSG_QUEUE.empty()` 逐条 `get_nowait()`（`6684-6686`），非 dict 直接丢弃（`6687-6688`）。整段包在 `try/except` 内，单条异常只记日志（`6768-6769`）。
2. **分支判定 `post_type`**（`6689`）：
   - `"message"`（`6690-6733`）：见下。
   - `"notice" + notice_type=notify + sub_type=poke` → `_handle_poke_event(ev)`（`6734-6740`）。
   - `"notice" + notice_type ∈ {group_recall, friend_recall}` → `_handle_recall_event(ev)`（`6741-6746`）。
   - `"request"` → `_handle_request_event(ev)`（`6747-6752`）。
   - `"notice" + group_increase` → `_handle_group_increase`（`6753-6757`）。
   - `"notice" + group_decrease` → `_handle_group_decrease`（`6758-6762`）。
   - `"notice" + friend_add` → `_handle_friend_add`（`6763-6767`）。
   - **`meta_event` 无任何分支**：落入 elif 链末尾后不做任何处理（`6689-6768` 全链无 meta_event 判定），等价于静默忽略。其他 notice_type（如 group_admin、group_upload）同样被忽略。
3. **message 内部处理**（`6690-6733`）：
   - `msg_type` 默认 `private`（`6691`），`is_group = (msg_type == "group")`（`6692`）。
   - 启动基线：`time` 早于 `startup_time` 的历史消息丢弃（`6693-6697`）。
   - 去重：有 `message_id` 走 `_mark_seen_msg_id`（`6699-6702`，实现 `426-435`，容量 `_SEEN_MSG_IDS_MAX = 3000`）；无 id 才回退时间戳水位线 `LAST_MSG_TIME`（`6703-6704`），并 `save_state()`（`6705-6707`）。
   - 段解析：list → 拼接 text 段 + 保留完整 segments；str → 包成单 text 段（`6708-6718`）。
   - 组装内部消息结构（`_type/_group_id/message/sender/user_id/time/message_id`，`6719-6731`），并记录段类型日志（`6732-6733`）。
4. **轮询兜底**：本轮若拿到任何推送事件，`new_msgs = event_msgs`；否则 `new_msgs = get_new_messages()`（`6771-6775`）。`get_new_messages`（`2104-2167`）对 `TARGET_QQS` 调 `get_friend_msg_history`、对 `GROUP_IDS` 调 `get_group_msg_history`（各 count=10），合并且按 time 排序、按 message_id 去重、过滤启动前消息、推进 `LAST_MSG_TIME`（`2142-2167`）。
5. **冷却完成日志**（`6777-6781`，用 `main._cooldown_done_logged` 属性做一次性标记）。
6. **全局空闲退场扫描**（`6783-6823`）：见 §4.4。
7. **逐条消息处理**（`6825` 起）。
8. 循环尾部：异常计数与退避 `sleep(min(consecutive_errors, 10))`（`7487-7494`）；每 200 轮（约 10 分钟）做一次清理 + `save_state(force=True)`（`7497-7550`）；`time.sleep(3)`（`7552`）。

---

## 4. 群聊分支

入口条件：`msg_type == "group"`（`bot_core.py:6862`）。

### 4.1 早退与前置过滤
- 自己发的消息跳过：`sender == BOT_QQ` 或 `msg_uid == BOT_QQ` 或 `post_type == "message_sent"`（`6863-6867`）。
- 群开关：`_group_restriction(group_id, "reply_enabled", True) is False` → 整群静默（`6868-6870`；读取函数 `4417-4423`，读 `_config["group_restrictions"][gid]`）。
- **无 `GROUP_IDS` 白名单校验**：群分支不检查 `group_id` 是否在 `GROUP_IDS` 内；`GROUP_IDS` 只用于轮询（`2123`）、启动预加载（`6606/6638`）、空闲退场遍历（`6806`）与状态展示（`543`）。
- 纯转发（有 forward 段且无文本段）只记上下文不回复（`7075-7077`、`7092-7095`）。
- 含文本 `@他人`（QQ at 段指向非 Bot 且非触发词）→ 跳过（`7100-7103`）。

### 4.2 触发判定（@ / 触发词 / 引用 / 文本@）
- QQ at 段：`at_qq == BOT_QQ` → `is_at_bot`；也支持昵称匹配（先 `QQ_NICK_CACHE[BOT_QQ]`，再 `bot_group_nick`，再 displayName 模糊匹配，含硬编码 `"占位名片A"`）（`7027-7051`）。
- 触发词：`_is_group_trigger(text)`（`7054`）→ 默认 `["小a", "@占位名片A", "@10000001"]`（`5936-5938`），匹配逻辑 `5941-5953`。
- Bot 昵称出现在文本中（配置 `group_trigger_include_bot_nick`，默认 True）（`7055-7058`）；文本内 `@BOT_QQ` / `@BOT_GROUP_NICK`（`7059-7064`）。
- 文本 @ 任意人（配置 `group_trigger_include_text_at`，默认 True）：仅用于让消息进缓冲，后续由 `process_buffered_message` 过滤（`7065-7069`）。
- 引用：配置 `group_trigger_include_quote` 时按是否存在 `reply` 段判定（`7070-7073`）——注意此赋值**覆盖**了前面 `6956-6957` 基于引用内容得到的 `has_quote`。
- 被 @/小a 时先尝试群管理指令 `_handle_group_admin_command`（`7084-7090`，实现 `5026-5118`，鉴权 `5049-5056`）。

### 4.3 引用消息回抓（4 级回退）
`6878-6959`：
1. 段内联 `quoted_text/quoted_nick`（`6889-6894`）。
2. `api_call("get_msg")` 取原文，失败则用 `raw_message`（`6895-6913`）。
3. `GROUP_RECENT_MSGS` 按 `message_id` 命中（`6914-6928`）。
4. 反向搜含 `[转发消息]` 的缓存记录（`6929-6943`）。
5. `GROUP_TIMELINE` 按 `sender_uid` 找最近一条（`6946-6955`）。
命中后拼成 `[引用:昵称] 原文\n[当前] 文本`（`6956-6959`）。

### 4.4 会话状态（session）与退场（idle exit）
- `GROUP_SESSION[gid] = {"remaining": group_session_max_replies(默认20), "last_msg_time": now}`：建立点 `7213-7217`（被 @/小a/含昵称/引用时）与 `6205-6206`（`process_buffered_message` 内再次确保）。
- 会话内消息处理：无 @/无触发词/无文本@/无引用时，若 `GROUP_SESSION.remaining > 0` 则标记 `in_session = True`（`7105-7115`），配额在真正处理时才递减（`6152-6162`）；过 `SESSION_TTL`（默认 300s）则删除会话（`6103-6107`）。
- 空闲退场（主循环，每轮扫描）两段：
  - 有会话的群：`bot_last >= last_any` 且静默超过 `_idle_exit_min_silence()`（下限 30s，函数 `244-250`，配置 `group_idle_exit_min_silence`）→ 判断 `now - last_exit >= group_last_exit_cooldown`（默认 3000s）且 `random() < group_idle_exit_probability`（默认 0.3）→ 发 `EXIT_PHRASES_IDLE` 随机一条 + `send_idle_exit_meme` + 记录 `GROUP_LAST_EXIT`；随后**无论是否发送都 pop 掉会话**（`6784-6804`）。
  - 无会话但 Bot 最后发言的群：同条件再判一次（`6805-6823`）。
- 回复侧：AI 正常回复会被 `strip_exit_phrases()` 剥离退场语（`6412`，重试路径 `6429`，实现 `1393-1413`）。
- `EXIT_PHRASES_SHORT`（`471-477`）**在全文件中无任何引用**（见死代码）。
- 被踢/退群时清理会话与缓冲（`4669-4680`）；200 轮清理里按 `SESSION_TTL*2` 回收（`7521-7525`）。

### 4.5 消息缓冲合并（`process_buffered_message` 的窗口与延时策略）
写入侧（主循环 `7025-7218`）：

- 合并窗口（仅在已有缓冲区时生效，`7118-7130`）：
  - 会话中 → `group_msg_buffer_window_session`（默认 5s）（`7122-7123`）。
  - 触发但还没有上下文 → `group_msg_buffer_window_trigger`（默认 15s）（`7124-7125`）。
  - 其他 → `GROUP_MSG_BUFFER_WINDOW`（默认 3s）（`7126-7127`）。
  - 无缓冲时算出的 `merge_window`（`7129`，5/15/3）**在新建分支未被使用**，只用于上面的过期判断。
- 合并动作：同 sender 直接追加 `\n文本`，不同 sender 追加 `\n[uid] 文本`；刷新 `time`，OR 合并 `is_at_bot/has_xiao_a/has_bot_nick/has_quote/has_text_at/in_session`，若有意义消息则置 `has_context`；取消旧 Timer 并重建（`7130-7162`）。
- 合并后的 Timer 延时（`7151-7160`）：会话 5s；触发且无上下文 15s；否则 3s。
- 缓冲区已过期 → 立即 `process_buffered_message(group_id)` 再新建（`7163-7165`）。
- 新建缓冲区：`buffer_text = 回抓历史 + 当前行`（`7169-7194`）；回抓条件为被 @/叫小a，最多 15 条且不超过 `GROUP_REPLY_MAX_LENGTH`（`7171-7189`）。
- 新建缓冲区的 Timer 延时（`7207`，注意是 dict 字面量里的海象表达式 `delay :=`）：会话 5s；`(is_at_bot or has_xiao_a or has_bot_nick or has_quote) and not has_context` → **10s**；否则 3s。**与合并路径的 15s 不一致**。

消费侧 `process_buffered_message(group_id)`（`6091-6499`）：
1. `pop` 缓冲区并取 `merged_text`（`6094-6098`），空则返回。
2. 会话过期检查（`6103-6107`）；启动冷却期内直接返回（`6109-6113`）。
3. 无任何触发标记时，交给 `group_chat_decider` 插件判定是否插话（`6124-6151`；只看该插件的 `inject` 结果 `6143-6145`）。
4. 会话配额递减（`6152-6162`）。
5. 逐行过滤：`_is_at_others`（定向他人 → 整条 return）、`_is_group_trigger`、`_is_short_meaningless`、`_is_nonsense`、`_is_bystander_commentary`（`6164-6202`）。`meaningful_count == 0` 且被 @ 时，用「有人@我但没说话/发的是无意义内容」prompt 触发一次 AI（`6208-6227`）。
6. 插件 `before_reply` 钩子聚合（`6231-6313`）：`skip` → 直接返回（`6315-6316`）；`override` → 用插件回复（`6317-6318`）；`inject` → 按 priority 升序拼接，EGO 的部分单独抽出做 system 级注入（`6290-6305`），temperature 存入 `_ST`（`6306-6309`）。
7. 会话内「是否明确指向 Bot」再判：需要含「你」且（疑问词/请求词/祈使句式），否则 return（`6328-6359`）。
8. 画图意图正则命中 → `handle_generate_image` 并直接返回（`6371-6385`）。
9. `call_deepseek(..., is_group=True, personality_temperature=_ST.get(...), ego_inject_context=_ego_ctx)`（`6387`）。
10. 联网搜索循环：匹配 `[搜索:xxx]`，最多 2 轮，把 `search_web` 结果作为系统消息重问（`6389-6404`）。
11. `[不说话]`/`[跳过]` → 返回（`6406-6408`）；`strip_exit_phrases`（`6410-6415`）。
12. 相似度去重：60s 内历史回复，`is_similar_to_recent` 阈值 0.6，最多重试 2 次换话（`6417-6442`）。
13. 写 `group_chat_history`（各截 300 字，保留 16 条）（`6444-6450`）；NexSandglass 回写（`6452-6458`）。
14. `parse_meme_intent` 剥离 `[表情]` → `_trim_incomplete` → `de_ai_post_process`（`6460-6468`）；`after_reply` 钩子可覆盖文本（`6471-6483`）；`smart_send_group` + `track_bot_reply`（`6484-6486`）。
15. 表情包附着 `try_send_meme`（`6490-6491`，实现 `1589-1648`）；`save_instruction`（`6492`）；可选触发自学 `maybe_learn`（`6494-6497`）。

---

## 5. 私聊分支

群分支之后的下落路径（`bot_core.py:7220` 起）：

1. `sender = msg["sender"]["user_id"]`；NT 协议随机 UID `u_xxx` 会被**替换为 `TARGET_QQS[0]`**（`7221-7225`）。
2. **`TARGET_QQS` 过滤**：`if sender not in TARGET_QQS: continue`（`7226-7227`）。
3. **好友「自动回复」门控**：`_friend_restriction(sender, "reply_enabled", True) is False` → 完全静默（`7228-7230`；读取函数 `4466-4472`，读 `_config["friend_restrictions"][qq]`）。
4. `extract_text`（`7232`）；纯转发跳过（`7235-7239`）。
5. 引用提取：内联 → `get_msg` → `raw_message` → 私聊 `chat_history` 最后一条 user 消息（`7242-7281`）；拼 `[引用] ...\n[当前] ...`（`7283-7287`）。
6. 图片：仅有图无文字 → 存 `PENDING_IMAGES[sender]` 等待关联（`7291-7295`）；后续文字消息 5s 内取回（`7297-7304`）。
7. `save_chat_log`（`7306-7307`）+ NexSandglass 落沙（`7308-7316`）。
8. 空消息跳过；纯图默认文本「看看这张图片」（`7318-7322`）。
9. 登记 `PRIVATE_SESSION`（`7324-7329`，撤回反馈的会话门槛）。
10. ChatStyle 自学（`7333-7339`）；撤回反馈 5s 冷却跳过（`7341-7347`）；`chat_log("in", ...)`（`7349-7351`）。
11. 控制指令 `try_execute_command`（`7354-7360`，实现 `5121-5138`，权限 `4398-4407`）。
12. 开机冷却检查（`7362-7365`）。
13. 私聊限速 `PRIVATE_REPLY_INTERVAL`（默认 3s，`7367-7373`）。
14. 画图意图（`7379-7394`）。
15. EGO `before_reply`（私聊，`7396-7411`，`ctx` 带 `scope=private/sender/history/bot_qq`）。
16. `call_deepseek(...)`（`7413`）→ 回复处理：`parse_meme_intent` → `_trim_incomplete` → `de_ai_post_process` → `after_reply` 钩子（带 `scope=private`，`7427-7439`）→ `smart_send`（`7440`）；表情包（`7444-7463`）；写 `chat_history`（user+assistant，保留 24 条）（`7465-7469`）；`save_chat_log`/`save_instruction`/NexSandglass（`7470-7479`）。
17. AI 无回复 → 发「收到消息了，但暂时无法处理，请稍后再试。」（`7480-7481`）；`save_state()` + `sleep(1)`（`7483-7484`）。

群「自动回复」门控在 §4.1（`6868-6870`），插件回复路径也单独复检一次（`6847-6851`）。

---

## 6. AI 调用链

```
收到消息
 ├─ 私聊: call_deepseek(7413)
 └─ 群聊: process_buffered_message → call_deepseek(6387)
           ├─ 联网搜索重问(6404) / 去重换话重试(6425)
           └─ 戳一戳: call_deepseek(4785/4841)
call_deepseek(5879-5898)
 └─ _submit_ai_task(5210-5220)  →  _ai_task_queue
     └─ _ai_worker(5169-5207)  ×3 线程（import 期启动 5240-5242）
         └─ _call_deepseek_sync(5656-5876)
             ├─ _build_static_prompt(is_group)(5669 → 1728-1790)
             ├─ dynamic_parts 组装(5670-5754)
             ├─ _build_cached_messages(5764 → 5325-5417)
             └─ 循环 while True(5792)
                 ├─ _call_chat_sync(5794 → 5573-5610)
                 │   ├─ _llm_config(5436-5524)：本地 ollama/lmstudio/llama-server 或远程
                 │   ├─ anthropic/gemini → providers.make_provider(5584-5589) → prov.chat
                 │   └─ openai 兼容 → _post_chat(5603 → 5549-5561, urllib+CTX, timeout 60)
                 ├─ 有 tool_calls → 执行 TOOL_FUNCTIONS(5820) → 回灌 tool 消息(5824)
                 │      ├─ 网页任务: 继续多轮(5826-5830)，上限 MAX_WEB_ROUNDS=20(5790)
                 │      └─ 否则: _stream_chat_lines(5832 → 5613-5653)
                 └─ 无 tool_calls → 返回 content(5834-5862)
```

要点：

- **参数与组装**：`_call_deepseek_sync(user_msg, images, target_qq, group_id, is_group, private_memory_context, personality_temperature, ego_inject_context)`（`5656-5660`）。`target_qq` 缺省取 `TARGET_QQS[0]`（`5664-5665`）。system prompt = `_build_static_prompt(is_group)`（`5669`），其后动态内容全部走 `dynamic_parts`（`5670`、`5680-5773`），最终由 `_build_cached_messages` 变成 `messages`（`5764`）。
- **messages 结构**（`5325-5417`）：`[system static]` → 历史摘要（`_HISTORY_CACHE`，内容指纹 md5 判定，`5342-5393`） → 最近 4 轮原文（`RECENT_TURNS = 4`，`5333`、`5395-5396`） → 动态 system 片段（`5401-5404`） → 当前 user（有图时最多 3 张 base64，`5406-5415`）。目的是命中 DeepSeek 前缀缓存。
- **温度**：`_call_chat_sync` 使用全局 `AI_TEMPERATURE`（`5578`、`5594`）。**`personality_temperature` 参数全程未被使用**（声明 `5659`、透传 `5184`、`5882/5892`，无任何读取点）——即 EGO 情绪温度实际不生效（见风险节）。
- **工具**：私聊 `TOOLS`（11 个：search_web/open_app/take_screenshot/get_system_info/control_volume/control_power/manage_process/search_files/send_user_file/web_operate/generate_image，`2738-2938`，映射 `2978-3002`）；群聊 `_build_group_tools()`（`3004-3029`，默认 `GROUP_TOOLS` = get_group_members + generate_image，`2941-2976`；配 `group_tool_allowlist` 可放开）。工具循环会把 `group_id/target_qq` 自动补进参数（`5814-5817`）。
- **网页任务**：消息含 URL 或问卷/表单关键词时追加 `[系统提示]`（`5766-5773`），并抬高 `max_tokens`（`5784-5786`）；模型一次工具都没调会追加一次强制指令（`5851-5859`）。
- **重试与降级**：`except` 捕获后最多再试 4 次（共 5 次尝试），每次 `sleep(3)`（`5863-5874`），全部失败返回 `None`（`5875`）。`call_deepseek` 等待上限：普通 30s、网页任务 600s（`5895-5898`；`_wait_ai_result` `5223-5236`）。私聊 `None` → 兜底话术（`7481`）；群聊空回复 → 直接 return（`6406-6408`）。
- **`AIClient` / `providers` 关系**：
  - bot_core 自身的请求**不经过 `ai_client.AIClient`**，而是直接 `urllib.request`（`_post_chat` `5549-5561`）或 `providers.make_provider`（`5584`、`5618`）。
  - `AIClient` 仅在 `main()` 里为插件构造并注入：`from ai_client import AIClient` → `pm.set_ai_client(_ac)`（`6572-6590`），插件通过 `ctx.ai_chat()` 使用（`plugins/abi.py:134-143`）。
  - `_llm_config()`（`5436-5524`）决定走本地（ollama/lmstudio/llama-server）还是远程，并优先用凭据管理器里的**分提供商 Key**（`5448-5454`；`config.get_provider_api_key`）。
  - 协议判定 `_resolve_provider_type()`（`5420-5433`）：anthropic/gemini 走 `providers`，其余走 OpenAI 兼容 JSON。
  - llama-server 本地启动会 `Popen` 子进程并轮询 `/health` 最多 120s（`5488-5506`），空闲看门狗见 §12。
- 观测：token 用量写 `token_stats.record`（`5650`、`5803`、`5841`）。

---

## 7. 人设注入链

### 7.1 `load_personality()`（`1792-1853`）优先级
1. **EGO `personality_engine.get_personality()` 优先**（`1800-1823`）：取 `raw` 非空即返回。
   - EGO 内部再分优先级（`plugins/ego/personality_engine/__init__.py`）：**蒸馏人格卡** `plugins/这样脆弱的东西/data/published/active.persona.txt`（`89-108`）> `personality.txt`（`111-137`）。所以实际优先级是：**蒸馏人格卡 > EGO 的 personality.txt > bot_core 的文件兜底 > 硬编码兜底**。
2. **文件兜底**（`1825-1844`）：依次尝试 `PERSONALITY_FILE`、上一级、上两级、脚本目录下的 `personality.txt`（`1826-1831`），读取成功即返回。
3. **硬编码兜底人格**（`1845-1853`）。

### 7.2 mtime 缓存机制
- 缓存结构 `_personality_cache = {"text","mtime","path"}`（`1722`）。
- 命中条件：`text` 非空 且 `path` 相同 且 `mtime` 相同（EGO 分支 `1816-1818`；文件分支 `1836-1841`）。
- 关键隐式约定：EGO 分支里取 mtime 的路径是 `plugins/ego/personality_engine/../../personality.txt` = `app/personality.txt`（`1809-1815`）。**蒸馏人格卡换了文件，但缓存键看的是 `personality.txt` 的 mtime**，所以发布方必须 touch 该文件才会刷新——`plugins/这样脆弱的东西/publisher.py:100-107` 与文件头注释 `publisher.py:14-15` 明确写了这一点。若 EGO 的 `personality.txt` 不存在，则 `mtime = 0`、`path = None`（`1820`），此时**每次调用都会重新读蒸馏卡**（缓存等于失效）。
- `_build_static_prompt` 的缓存键包含 `_personality_cache["mtime"]`（`1733`），因此人设变化会同时让 static prompt 缓存失效。

### 7.3 `_build_static_prompt()`（`1728-1790`）注入项
| 行号 | 内容 |
|---|---|
| `1738` | `load_personality()` 主体 |
| `1740-1747` | 【你是谁】身份块（网名小a / 群昵称 / QQ 号）拼在最前 |
| `1749-1754` | 私聊时正则剔除【群聊行为】【自主学习成果】【群N表达风格】 |
| `1756-1757` | 【回答多样性】 |
| `1759-1766` | `de_ai.de_ai_rule_block(_config)`（去 AI 味铁律） |
| `1768-1769` | 【表情包】`[表情:情绪词]` 说明与情绪词表 |
| `1771-1772` | 【诚实铁律·最重要】 |
| `1774-1785` | 【群聊规则】（仅群聊） |
| `1733-1736,1787-1789` | 按 `(is_group, _CONFIG_REV, personality_mtime)` 缓存，>8 键整体清空 |

### 7.4 其他 system 级注入（`_call_deepseek_sync`，`5670-5773`）
| 行号 | 注入内容 |
|---|---|
| `5680-5681` | 硬编码特例：`10000002` 是「老大」，态度暧昧亲近 |
| `5685-5693` | 【当前时间】（节流：距上次 ≥600s 才注入，`_GROUP_TIME_INJECT_AT`） |
| `5694-5713` | 【群聊动态】最近时间线（去重、最多 6 条，每条 50 字） |
| `5714-5718` | 【群成员列表】（仅当消息含 选/谁/@/在线/成员/随机/人 时，取前 15 人） |
| `5719-5725` | 【之前的群聊背景】摘要（10 分钟最多注入一次，截 200 字） |
| `5726-5729` | 自主学习 `self_learn.get_context_for_llm(group_id)` |
| `5732-5735` | 黑话 `get_slang_context(user_msg)`（开关 `slang_injector_enable`） |
| `5737-5738` | **EGO 上下文**（人格/记忆/风格/节奏）经 `_inject_ego_context`（`189-193`）包成【EGO上下文】system 片段 |
| `5739-5747` | 私聊 NexSandglass `get_memory_prompt_block(user_msg, light=True)` |
| `5748-5749` | `private_memory_context` →【用户画像】（实际恒为空，见 §8） |
| `5750-5754` | 15 分钟内说过退场语 → 【重要】不要重复退场语 |
| `5766-5773` | 网页/问卷任务 → `[系统提示]` |
| `6369` | 插件 `inject` 过 → 追加「请务必回复」 |
| `6424` | 重复回复重试时的换话提示 |

EGO 注入来源：`_run_ego_before_reply()`（`142-186`）只收集 `plugin_name == "ego"` 的 `inject`（`163-176`），兼容 `content` 与 `prompt_blocks`（`171-174`）；EGO 插件自身按 `mood/personality/memory/style/rhythm` 子模块顺序聚合（`plugins/ego/__init__.py:112-160`，priority=20 见 `66-79`）。

---

## 8. 记忆相关（bot_core 内的读写点）

**NexSandglass（沙漏）**——全部经 `sandglass_bridge`，且都是函数内 import：

| 行号 | 调用 | 方向 |
|---|---|---|
| `6514-6520` | `_ensure_init()` | 启动初始化 |
| `6986-6993` | `log_user_message` + `extract_decision_particles`（群） | 写 |
| `7309-7316` | `log_user_message` + `extract_decision_particles`（私聊） | 写 |
| `6452-6458` | `sync_conversation` + `log_bot_reply(reply, group_id)`（群） | 写 |
| `7473-7479` | `sync_conversation` + `log_bot_reply`（私聊） | 写 |
| `5739-5747` | `get_memory_prompt_block(user_msg, light=True)`（**仅私聊**，群聊不注入） | 读 |
| `1617-1636` | `search_memory(kw, limit=2)` 推断表情包情绪 | 读 |

**自主学习 `self_learning`**：import `45-46`；`get_status`（`4152`、`4357`）；`get_group_profile`（`4193`）；`get_context_for_llm`（`5727`）；`collect_message`（`6977`，群消息无触发也收集）；`maybe_learn`（`6497` 群回复后、`6641` 启动时）。开关 `self_learning_enable`（默认 True）。

**`super_memory`**：bot_core **不直接调用**，只通过 EGO 的 `before_reply`/`after_reply` 钩子间接触发（派发点 `159`、`6141`、`6261`、`6476`、`7433`）；文档字符串里提到其返回 `prompt_blocks`（`148`）。私聊 `after_reply` 特意传 `scope/sender` 以便其写入正确作用域（`7432-7433` 注释）。

**`private_memory.py`**：bot_core **从未 import**（全文件无 `import private_memory`）。相关形参 `private_memory_context` 一路透传（`5658`、`5881`、`5891`、`5183`、`5748-5749`），但唯一调用处传的是 `pm_ctx`，而 `pm_ctx = ""`（`7377`）。**该参数目前恒为空**。

---

## 9. 插件钩子派发点

旧式（`PLUGIN_META` + `has_hook/execute_hook(s)`；实现 `plugins/plugin_manager.py:421-488`）：

| 行号 | 调用 | 钩子 / 参数 / 语义 |
|---|---|---|
| `128-130` | `set_host_api(api_call=...)` | 注入宿主唯一 OneBot 出口（ABI v2），在 `load_all()` 之前 |
| `156-159` | `has_hook` + `execute_hooks("before_reply", message, group_id, context)` | EGO 专用收集（`_run_ego_before_reply`），只取 `plugin_name=="ego"` |
| `712-715` | `pm.list_plugins()` | `/api/plugins`，过滤 `adapter` 壳 |
| `763-764` | `pm.check_plugin_api(name)` | `/api/plugins/test` |
| `6130-6145` | `pm.is_loaded/is_enabled("group_chat_decider")` + `execute_hooks("before_reply", ...)` | 无触发消息的插话预判，只认该插件的 `inject` |
| `6243-6261` | `has_hook` + `execute_hooks("before_reply", message=merged_text, group_id, context=_plugin_ctx)` | 群聊主决策；处理 `skip/override/inject/temperature`（`6263-6309`） |
| `6475-6476` | `execute_hook("after_reply", message=merged_text, group_id, reply, context=_plugin_ctx)` | 群聊发送前改写（`action=="override"`） |
| `7431-7433` | `execute_hook("after_reply", message=text, group_id="", reply=sent_text, context={"scope":"private","sender":sender})` | 私聊发送前改写 |
| `6587` | `pm.set_ai_client(_ac)` | 传播 `AIClient` 给插件 |

类型化 ABI v2（`PLUGIN_SPEC=1` + `PLUGIN_HOOKS`；实现 `plugin_manager.py:215-255`）：

| 行号 | 调用 | 说明 |
|---|---|---|
| `6830-6844` | `has_typed_hook("on_message")` + `execute_typed_hooks_first("on_message", event={type,group_id,sender,text,message_id,time})` | 每条消息最前置；返回非空文本即作为回复并 `continue`（跳过 AI），群聊仍尊重 `reply_enabled`（`6845-6855`） |

**注意**：bot_core **没有**直接调用 `execute_typed_hooks()`（只有 `execute_typed_hooks_first`）；`execute_typed_hooks` 仅被 `plugin_manager.emit_event`（`510-528`）使用，而 bot_core 全文无 `emit_event`/`ctx.emit` 调用（`grep "emit"` 无命中）。即事件总线在 bot_core 侧是空的。

---

## 10. 对外 API（bot_core 自起的 3005 服务）

| 方法 | 路径 | 行号 | 作用 | 改动的配置键 |
|---|---|---|---|---|
| GET | `/api/status` | `534-570` | 运行状态（依赖 psutil）；大量字段写死 0（`542-553`） | 无 |
| GET | `/api/groups` | `571-625` | 群列表 + Bot 角色 + `reply_enabled`/`new_member_card` | 无（只读 `group_restrictions`） |
| GET | `/api/groups/members?group_id=` | `626-670` | 成员列表（owner/admin 优先排序） | 无 |
| GET | `/api/friends` | `671-709` | 好友列表 + `reply_enabled` | 无（只读 `friend_restrictions`） |
| GET | `/api/plugins` | `710-729` | 插件列表 | 无 |
| POST | `/reload_config` 或 `/reload` | `735-752` | `reload_config()`（`329-379`） | 不改文件；刷新 `_RELOADABLE_GLOBALS`（`318-326`）+ 失效权限缓存 + `_CONFIG_REV += 1` |
| POST | `/api/plugins/test` | `753-780` | 插件 API 自检 | 无 |
| POST | `/api/admin/notify_test` | `781-797` | 给管理员发测试通知 | 无 |
| POST | `/api/groups/settings` | `798-828` | 群开关/新人名片 | **`group_restrictions[gid].reply_enabled`、`.new_member_card`**（`_set_group_restrictions` `4444-4463`） |
| POST | `/api/groups/action` | `829-850` | `_group_action`（`1115-1156`）：改名片/设撤管理/禁言/解禁/踢/改群名/全员禁言 | 无（纯 OneBot 调用） |
| POST | `/api/friends/settings` | `851-879` | 好友自动回复开关 | **`friend_restrictions[uid].reply_enabled`**（`_set_friend_restrictions` `4475-4494`） |
| POST | `/api/friends/action` | `880-911` | `set_friend_remark` 改备注 | 无 |
| POST | 其他任意路径 | `912-919` | 当作 OneBot 事件入 `MSG_QUEUE` | 无 |
| — | 事件副作用 | `4574-4575`、`4592-4593`、`4602-4603`、`4690-4691` | 好友申请/入群/好友增加时 | **`target_qqs`** / **`group_ids`**（`_append_target_and_reload` `4509-4524`） |

落盘实现：`_persist_config_file()`（`4426-4441`）原子写**程序目录** `bot_config.json`，并在写入前剔除 `api_key`（`4433`）；写完立即 `reload_config()` 免重启生效（`4458`、`4489`、`4521`）。无鉴权层——仅靠绑定 127.0.0.1 + CORS 限制（`510-526`）。

---

## 11. 状态持久化

| 文件 | 定义 | 写入函数 | 写入时机 | 内容 |
|---|---|---|---|---|
| `STATE_FILE` = `%LOCALAPPDATA%\EggBloom_v3\qq_bot_state.json` | `309` | `save_state()` `1875-1893` | `2166`（轮询推进水位）、`6707`（推送推进水位）、`7358`（私聊指令后）、`7483`（私聊每轮结束）、`7548`（每 200 轮 force） | `last_msg_time`、`chat_history`（截 24 条）、`group_chat_history` |
| 同上（读） | — | `load_state()` `1856-1873` | `main()` `6645` | 缺省 `LAST_MSG_TIME = now-600` 以回溯 10 分钟（`1871-1872`） |
| `INSTRUCTION_FILE` = `...\qq_instructions.json` | `307` | `save_instruction()` `5901-5909` | `6492`（群）、`7357`（指令）、`7472`（私聊） | list，追加 `{time,text,reply,status:"done"}`，**无上限、每次全量读写** |
| `bot_config.json`（程序目录） | `208` | `_persist_config_file()` `4426-4441` | `4457`、`4488`、`4520` | 群/好友限制、`target_qqs`、`group_ids`（**剔除 api_key**） |
| `chat_logs\QQ_<id>\chat_YYYY-MM-DD.md` | `312`、`1699-1719` | `save_chat_log()` | `7307`、`7471` | Markdown 对话转存 |
| `LOG_FILE` = `...\qq_bot.log` | `308` | `log()` `1031-1045` + `_rotate_log_file()` `1048-1063` | 每次日志调用 | 5MB 轮转，保留 5 份 |

`save_state` 节流：非 force 时累计 20 次或距上次 ≥60s 才落盘（`1879-1885`）。**注意**：群聊回复路径（`process_buffered_message`）**不调用** `save_state`，群历史落盘只靠每 200 轮 `force=True`（`7548`）与水位线推进时的非 force 调用。

---

## 12. 定时器

`threading.Timer` 仅 3 处：

| 行号 | 延时 | 用途 |
|---|---|---|
| `5207` | 300s | AI 任务结果 entry 延迟清理（防 `_ai_results` 泄漏） |
| `7160` | 5 / 15 / 3s | 合并缓冲刷新 → `process_buffered_message(gid)`（会话/触发无上下文/其他） |
| `7207` | 5 / 10 / 3s | 新建缓冲区刷新 → `process_buffered_message(gid)` |

后台线程（非 Timer，但同属「定时/常驻」策略）：AI 工作线程池 3 个（import 期 `5240-5242`）、昵称解析线程（`6594`，`980-1001`，队列 `get(timeout=30)`）、事件服务线程（`6657`）、启动冷却倒计时线程（`6649-6654`）、llama-server 空闲看门狗（`3909-3932`，`sleep(60)` 轮询，按需启动 `3943-3944`）。主循环自身为 `sleep(3)` 的轮询（`7552`）。

**无可优雅退出机制**：全文无 `atexit`/`signal`/`KeyboardInterrupt` 处理；`7496` 的注释「优雅关闭检查」下方实际只有定期清理，进程退出时不做最终落盘（只能依赖每 200 轮的 `force=True`）。

---

## 值得注意的设计

1. **基于 `message_id` 的去重**（`421-435`、`6699-6706`）：把原来的秒级时间戳水位线改为 message_id 集合（3000 上限，FIFO 淘汰），修掉了「同一秒第二条消息被吞」的问题；无 id 时才回退水位线。
2. **启动基线丢弃历史消息**（`6694-6697`、`2156-2158`）：以 `startup_time` 为界，避免协议层登录同步推送触发对旧消息的回复。
3. **DeepSeek 前缀缓存友好**：`_build_static_prompt` 按 `(is_group, config_rev, personality_mtime)` 缓存（`1733-1736`），动态内容一律后置为独立 system 片段（`5670-5773`、`5401-5404`）；历史摘要用 md5 内容指纹 + 滚动追加（`5342-5393`），避免「条数相同内容变了仍用旧摘要」。
4. **插件钩子优先级聚合 + 单插件异常隔离**：`plugin_manager.py:460-488` 按 priority 升序、单个插件抛异常只打印不影响其他；bot_core 侧对 `skip/override/inject` 三类动作分别处理并把 EGO 注入提升为 system 级（`6263-6309`、`6294-6299`）。
5. **空闲退场加下限保护**（`241-250`）：`GROUP_IDLE_EXIT_MIN_SILENCE_FLOOR = 30`，注释明确是为了防止「刚回复完（表情包还在上传）就退场」。
6. **撤回即取消缓冲**（`4876-4883`）：撤回事件会取消未处理的缓冲区 Timer，从而「截断即将发出的 AI 回复」，再据 `buffer_cancelled` 选择截断/撤回两套话术（`4907-4936`）。
7. **配置落盘绕开 MSIX 重定向**（`4426-4441` 及注释）：显式写程序目录真实 `bot_config.json`，规避 Store 版 `%LOCALAPPDATA%` 沙箱；同时剔除 `api_key` 不落盘明文。
8. **可选模块全面降级**：6 个可选依赖各自 try/except 并提供桩实现（`44-139`），任一模块缺失不会让 bot 起不来。
9. **网络热路径与主循环解耦**：昵称解析走后台队列（`944-977`、`980-1001`），AI 调用走 3 线程任务队列（`5166-5242`），避免单条慢消息阻塞整个管道（`5239` 注释）。
10. **主循环绝不因连续异常退出**（`7486-7494`）：退避重试 + 连续 20 次时告警，注释说明了「否则任何每轮必抛的错误会让 Bot 在 ~60 秒内静默暴毙」。
11. **协议层请求体上限预判**（`1453-1465`）：按 2MB 上限反推原图 1.4MB 上限并做压缩/跳过，避免硬发被断连。
12. **私聊撤回也加会话门槛**（`4986-5010`）：只有 `PRIVATE_SESSION` 存在且在 `PRIVATE_SESSION_IDLE_TIMEOUT` 内才反馈，避免对任意撤回自动回话。

## 风险与坏味道

1. **`personality_temperature` 是死参数**：`_call_deepseek_sync` 声明（`5659`）并逐层透传（`5184`、`5882/5892`），但 `_call_chat_sync` 只用全局 `AI_TEMPERATURE`（`5578`、`5594`）。EGO 通过 `_ST` 传的温度（`6308`、`6387`、`7401-7413`）**完全无效**。
2. **`private_memory_context` 同样是死参数**：调用点传 `pm_ctx = ""`（`7377`、`7413`），`private_memory.py` 从未被 import（§8）。
3. **裸 `except:` / 静默吞异常**：`1893`（`save_state` 的落盘失败被完全吞掉，`except: pass`）、`2273`、`2285`、`2410`、`2459`、`2525`、`4009`；全文件 `except Exception` 174 处、`except ...: pass` 55 处（正则计数）。状态持久化失败无任何可观测信号。
4. **巨型函数**：`main()`（`6506-7552`，约 1050 行）与 `process_buffered_message()`（`6091-6499`，约 410 行）耦合了协议、业务、持久化与日志。
5. **缓冲区的锁形同虚设**：声明了 `_buffer_lock`（`412`），但仅被退群清理使用（`4673`）；主循环改 `GROUP_MSG_BUFFER`（`7026-7218`）与 Timer 线程里 `process_buffered_message` 的 `pop`（`6094`）都未加锁。`_replies_lock`（`413`）、`_meme_lock`（`415`）、`_group_meta_lock`（`410`）**从未被使用**（仅定义）。
6. **两套缓冲延时不一致**：新建缓冲触发时 10s（`7207`），合并后触发时 15s（`7157`）；合并窗口 5/15/3（`7118-7130`）与延时 5/10(15)/3 是两套独立魔法数，且 `7129` 算出的 `merge_window` 在新缓冲分支未被使用。
7. **`has_quote` 被覆盖**：`6956-6957` 依据引用内容置 True，随后 `7071-7073` 又按「是否存在 reply 段 + 开关」重算，逻辑上互相掩盖。
8. **群消息没有 `GROUP_IDS` 白名单**：群分支只查 `reply_enabled`（`6862-6870`），而协议适配器转发全部事件（`protocols/onebot_std_adapter.py:390-401`），因此 Bot 所在的**任何群**只要有事件推送就会参与回复，`GROUP_IDS` 实际只影响轮询与启动预加载（`2123`、`6606`、`6638`、`6806`）。
9. **任意 POST 路径都被当事件**（`912-919`）：未匹配路由的 POST + JSON body 直接入队，配合 `/api/*` 无鉴权与 127.0.0.1 绑定，本机任意进程可伪造消息事件或调用群管理动作（`829`）。
10. **NT UID 归并风险**：`u_xxx` 私聊发送者被直接改写成 `TARGET_QQS[0]`（`7224-7225`），多用户场景下会把不同人当成同一人。
11. **硬编码业务特例**：`10000002` 的「老大」人设（`5680-5681`）、群昵称兜底 `"占位名片A"`（`7049`）、`_KNOWN_NAMES = ["王宇航","王雨涵"]`（`5933`）、默认 `BOT_QQ = "10000001"`（`291`）、默认触发词列表（`5938`）。
12. **`save_instruction` 无上限全量读写**（`5901-5909`）：每轮回复把整个 JSON 列表读入、append、整体重写，长期运行会线性变慢；同时**群/私聊历史落盘不均衡**（群聊不调 `save_state`，`6094-6497` 无该调用，只靠 `7548` 的 200 轮 force）。
13. **`log()` 每次都做轮转检查 + 打开文件追加**（`1039-1045`、`1048-1063`）：每条日志一次 `getsize` + `open/write/close`，热路径开销固定。
14. **函数内重复 import**：`from sandglass_bridge import ...`（`6987`、`7310`、`6454`、`7475`、`5741`、`1619`）、`from de_ai import ...`（`6465`、`7422`）、`import re`（`1017`、`1196`、`1285`、`1311`）等，每轮消息都会执行。
15. **群/私聊过滤逻辑三份并存**：`_is_short_meaningless`/`_is_nonsense`/`_is_bystander_commentary`（`5990-6044`）、`is_meaningless_message`（`6066-6084`）、`_filter_message_line`（`6047-6063`）语义重叠。
16. **死代码**（经全文件标识符计数验证，仅出现一次）：
   - 函数：`_chunk_text`（`1183-1187`）、`_filter_message_line`（`6047-6063`）。
   - 常量：`EXIT_PHRASES_SHORT`（`471-477`）、`MEME_DIR`（`305`）、`ENV_FILE`（`209`）、`_APPDATA_ENV`（`212`）、`COMFYUI_IMG2IMG_PATH`（`3186`）、`_llama_process`（`3898`）、`_llama_last_used`（`3899`）。
   - 未使用锁：`_group_meta_lock`（`410`）、`_replies_lock`（`413`）、`_meme_lock`（`415`）。
   - 整文件：`message_router.py`、`onebot_client.py`——bot_core 全文无引用（`grep` 无命中），仓库内也仅被文档/自身引用（`docs/napcat-adapter-spec.md`、`webui/HANDOFF.md`）。**结论：对 bot_core 是死代码**（是否被其它入口加载未能确认）。
   - 注释过期：`934` 提到 `eggbeot-plugin.js` 推送，但该文件在仓库中不存在（`grep` 无命中）。
17. **`meta_event` 无处理分支**（`6689-6768`）：生命周期事件被静默忽略，无日志。
18. **`/api/status` 字段造假**（`542-553`）：`images_generated`、`memes_sent`、`plugin_count`、`tokens_used`、`api_calls_today` 恒为 0；`messages_total` 用 `'chat_history' in dir()` 这种无效判断（模块级 `dir()`）。
19. **`_mark_seen_msg_id` 用 list `pop(0)`**（`433`）：3000 元素下 O(n) 淘汰。

---

## 未能确认的事项

1. `message_router.py` / `onebot_client.py` 是否被 Desktop 前端（`main.js`/`main_window.py`）之外的入口动态 import——只在 app 目录内做了静态引用检索，未追踪打包配置（`package.json`）与动态 import 字符串。
2. WebUI（`webui/webui_server.py`）是否对 3005 的 `/api/*` 做了额外代理鉴权——未通读该文件，仅确认它持有 `BOT_CORE_URL = "http://127.0.0.1:3005"`（`webui_server.py:584`）。
3. `GROUP_IDS` 之外的群在真实部署中是否会收到事件，取决于协议适配器的订阅范围；bot_core 侧确认无白名单校验（`6862-6870`），但适配器订阅端未逐一核查。
4. `_config` 中 `qq_instructions.json` 的读取方（是否有消费者）未核查——bot_core 只写不读。
