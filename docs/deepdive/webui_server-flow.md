> ⚠️ **可信度声明**：本文由子代理通读产出（2026-10-03），所有结论均标注行号，但**未经逐条复核**。
> 已由我逐行核实的关键结论见 [../CORE_FLOW.md](../CORE_FLOW.md)。
> 引用本文的具体数字前，请先按行号回查源码。

---

# EggBloom `webui_server.py` 流程说明（可核查版）

| 项 | 值 |
|---|---|
| 目标文件 | `E:\EggBloom水星\EggBloom Desktop-new\resources\app\webui\webui_server.py` |
| 规模 | 4035 行 / 195,203 字节（LastWrite 2026-09-21 14:25） |
| 核查方式 | 全文逐段 `read`（1–4035 行）+ `grep` 交叉验证；关联文件 `config.py`、`protocol_manager.py`、`main_window.py`、`main.js`、`webui_artistic_v3.html` |
| 运行状态 | **未启动服务、未发任何请求**。所有结论均为静态代码阅读结论；凡属"运行时才能确认"的推断已单独标注（见第 15 节） |
| 引用约定 | 未标文件名者一律指 `webui_server.py`；例：`1320` = `webui_server.py:1320` |

---

## 1. 服务启动

### 1.1 HTTP Server 类型

- 使用 `class ThreadedHTTPServer(ThreadingMixIn, HTTPServer)`，并设 `daemon_threads = True`：`3989`–`3990`。
- 依赖导入：`from http.server import HTTPServer, BaseHTTPRequestHandler`（`15`）、`from socketserver import ThreadingMixIn`（`16`）。
- 即：**不是** `http.server.ThreadingHTTPServer`，而是自己用 `ThreadingMixIn + HTTPServer` 拼的多线程服务器（`3989`）；每个请求一个线程，且线程为 daemon（`3990`）。
- 模块 docstring 自述技术栈为 "ThreadedHTTP + Single-file SPA"：`6`。

### 1.2 监听地址与端口选择

- 入口函数签名 `def run(host="127.0.0.1", port=8080):`：`4007`；`if __name__ == "__main__": run()`：`4034`–`4035`。
- **没有任何命令行参数解析**（全文无 `sys.argv` 使用，`4034`–`4035` 直接调用默认参数）→ 监听地址恒为 `127.0.0.1`，首选端口恒为 `8080`。
- 实际绑定：`server = ThreadedHTTPServer((host, actual_port), Handler)`：`4024`。
- 启动日志：`print(f"EggBloom WebUI running on http://{host}:{actual_port}")`（`4025`）并另写统一日志 `_uni_log(...)`（`4026`，实现见 `32`–`38`）。

### 1.3 端口占用顺延

- `_port_free(p)`：对 `127.0.0.1` 做 `bind` 试探，成功即视为空闲，失败 `OSError` 视为占用：`3992`–`3999`。
- `pick_port(preferred=8080, tries=20)`：从 `preferred` 起逐个试探 `range(preferred, preferred + tries)`，即 **8080–8099**，返回首个空闲端口；20 个都被占用时**返回 preferred 本身**（`4005`），随后 `4024` 的绑定会抛 `OSError` 并导致进程退出（运行时可确认）。
- `run()` 中调用与提示：`actual_port = pick_port(preferred=port)`（`4010`），`if actual_port != port:` 时打印"端口被占用，改用"（`4011`–`4012`）。
- 注意：`_port_free` 只探测 `127.0.0.1`（`3995`），而绑定用的是调用方传入的 `host`（`4024`）；默认两者一致，若以 `run(host="0.0.0.0")` 调用则探测与绑定不同源（`3995` vs `4024`）。

### 1.4 `last_port.txt` 的写入

- 写入位置为 **`PROJECT / "last_port.txt"`**：`4017`（`(PROJECT / "last_port.txt").write_text(str(actual_port), encoding="utf-8")`），整段包在 `try/except Exception: pass` 中（`4014`–`4019`）。
- 紧邻的 `4015`–`4016` 先执行 `last_port_dir = PROJECT / "webui"; last_port_dir.mkdir(exist_ok=True)`，但该变量**之后从未被使用**，写文件用的是 `PROJECT` 根（`4017`）——属遗留/死代码。
- 本机实测：`resources\app\last_port.txt` 存在，内容为 `8080`（目录列举验证）。
- 消费方：
  - `main_window.py:56`–`66` `_get_webui_port()` 读取 `os.path.join(_PROJECT_ROOT, "last_port.txt")`（`main_window.py:59`），非数字则回落 `8080`（`main_window.py:66`）；`_PROJECT_ROOT` 非 frozen 时 = `main_window.py` 所在目录（`main_window.py:37`），即 `resources/app`，与 `PROJECT` 一致。
  - Electron 侧 `main.js` **不读** `last_port.txt`，而是硬编码 `8080`：`main.js:143`（退出时调 `/api/protocol/stop`）、`main.js:169`（`win.loadURL('http://localhost:8080?v=...')`）、`main.js:173`（失败重试仍指向 8080）、`main.js:204`（IPC `eggbloom-api` 代理同样是 `port: 8080`）。

### 1.5 启动时的其他副作用（在同一进程启动阶段执行）

- 每次启动清空内存协议日志并置"已清空"标记：`4021`–`4023`（`PROTO_LOG.clear(); _LOG_CLEARED = True`）。
- import 阶段即尝试拉起 8083 embedding 服务：`514`–`517`（`try: _ensure_embed_server()`），并起 30s 守护线程：`519`–`521`。
- import 阶段即启动状态刷新线程：`940`–`941`。
- 启动/停止进程的入口由外部负责：桌面端 `main_window.py:1423`–`1444` 以 `[sys.executable, server_py]`、`cwd=webui_dir` 启动（`main_window.py:1433`–`1438`）；Electron 端 `main.js:238`–`246` 解析脚本路径、`main.js:251`–`275` 选择 Python、`main.js:281`–`303` 启动并做崩溃守护（退避重启，`main.js:286`–`295`）。

---

## 2. PROJECT 解析

### 2.1 推导过程与候选顺序

```
41: HERE = Path(__file__).parent
42: if getattr(sys, 'frozen', False):
43:     _APP_DIR = Path(sys.executable).resolve().parent
44: else:
45:     _APP_DIR = HERE.resolve()
46: _PROJECT_CANDIDATES = [
47:     _APP_DIR,
48:     _APP_DIR.parent,
49:     _APP_DIR.parent.parent,
50:     _APP_DIR.parent.parent.parent,
51:     _APP_DIR.parent.parent.parent.parent,
52:     Path.home() / "AppData/Local/EggBloom_v3",
53: ]
54: PROJECT = None
55: for _c in _PROJECT_CANDIDATES:
56:     if (_c / "bot_config.json").exists() or (_c / "bot_core.py").exists():
57:         PROJECT = _c
58:         break
59: if PROJECT is None:
60:     PROJECT = _APP_DIR
61: sys.path.insert(0, str(PROJECT))
```

- `HERE` = 脚本自身所在目录（`41`），本机即 `resources\app\webui`。
- `_APP_DIR` 分两支：
  - **frozen 分支**（`42`–`43`）：取 `sys.executable` 的目录（打包成 exe 时的安装目录），**不再是脚本目录**。
  - **非 frozen 分支**（`44`–`45`）：`HERE.resolve()`，即 `resources\app\webui`。
- 候选顺序：`_APP_DIR` → 上溯 4 层父目录 → `%USERPROFILE%\AppData\Local\EggBloom_v3`（`46`–`53`），共 6 个候选。
- 判定条件：候选目录下**存在 `bot_config.json` 或 `bot_core.py`** 即命中（`56`），命中即 `break`（`58`）；一个都没命中则 `PROJECT = _APP_DIR`（`59`–`60`）。
- 命中结果被插入 `sys.path` 首位（`61`），因此后续 `from config import ...`、`from protocols import registry`、`import plugins.*` 都以 `PROJECT` 为根解析。

**本机实际推导**（依据目录列举）：非 frozen → `_APP_DIR = ...\resources\app\webui`；候选 1（`webui\`）无 `bot_config.json`/`bot_core.py` → 落空；候选 2 = `...\resources\app`，该目录同时存在 `bot_config.json`（28,760 B）与 `bot_core.py`（375,535 B）→ **`PROJECT = resources\app`**。

### 2.2 对 config 加载的影响

- `PROJECT` 直接决定 `sys.path[0]`（`61`），因此决定 `config.py` 从哪一份副本被导入：`from config import load_config as load_config`（`63`，带 `# noqa: E402`）。
- `config.py` **自己另有一套目录推导**，与 `PROJECT` 无耦合：
  - `SCRIPT_DIR`：frozen 取 `dirname(sys.executable)`，否则取 `dirname(abspath(__file__))`（`config.py:18`–`21`）。
  - `APP_CONFIG_FILE = SCRIPT_DIR/bot_config.json`（程序目录，优先；`config.py:30`）。
  - `CONFIG_FILE = ~/AppData/Local/EggBloom_v3/bot_config.json`（`config.py:23`、`config.py:26`）。
  - `load_config()` 优先级：`DEFAULT_CONFIG` → 程序目录 `APP_CONFIG_FILE` 整份 `update`（`config.py:393`–`399`）→ AppData 仅**补缺**（`config.py:401`–`410`）→ `.env` 仅补缺（`config.py:412`–`422`）→ **Windows 凭据管理器最高优先**（`config.py:424`–`426`）。
- 因本机 `PROJECT == config.py:SCRIPT_DIR == resources\app`，两条路径指向同一个 `bot_config.json`；但在 frozen 打包布局下若 `bot_config.json` 不在 exe 同级目录，`webui_server` 会继续上溯寻找（`55`–`58`），而 `config.py` 固定用 exe 同级（`config.py:30`），**二者可能分叉**——这一点未在运行时验证。
- `load_config` 带 mtime/size 弱缓存：`config.py:388`–`390`（签名未变则复用上次结果）。
- 模块级缓存：`CONFIG_CACHE = load_config()`（`954`）。

---

## 3. 前端加载

### 3.1 `_frontend_path` 与初始装载

- `_frontend_path = str(HERE / "webui_artistic_v3.html")`：`1133` —— 指向**脚本同目录**（`resources\app\webui\webui_artistic_v3.html`，实测 399,825 B），与 `PROJECT` 无关。
- 初始装载在 import 期完成：`1134`–`1138`，成功则 `HTML = _f.read().rstrip()`；失败则把异常文本拼进一个极简 HTML 作为兜底（`1138`）。
- `_html_mtime = 0`：`1139`（**初值为 0，不是真实 mtime**，见 3.2）。

### 3.2 mtime 热重载机制

- `_get_html_mtime()`：`os.path.getmtime(_frontend_path)`，异常返回 `0`：`1201`–`1205`。
- `_load_html()`：重读文件到全局 `HTML`，并把 `_html_mtime` 同步为当前 mtime；异常静默忽略：`1207`–`1214`。
- 请求期判定（`/` 路由内）：`mtime = _get_html_mtime(); if mtime != _html_mtime: _load_html()`，然后 `_send_html(HTML)`：`1306`–`1310`。
- 由此：**修改 HTML 文件后无需重启服务，下一次请求 `/` 即自动重载**。但 `_html_mtime` 初值 0（`1139`）而初始装载（`1134`–`1138`）**没有**同步 mtime，所以进程启动后**第一次**访问 `/` 必然触发一次多余的重读（`1307`–`1308`）。
- 该分支外再包一层 `try/except`，`except` 里**重复了完全相同的 4 行逻辑**（`1311`–`1315` vs `1305`–`1310`）。
- HTML 响应头强制不缓存：`Cache-Control: no-cache, no-store, must-revalidate` + `Pragma` + `Expires: 0`（`1267`–`1269`）。

### 3.3 `/` 与 `/desktop` 的区别

| 路由 | 判定行 | 数据来源 | 行为 |
|---|---|---|---|
| `/` 或 `/index.html` | `1304` | `HERE / "webui_artistic_v3.html"`（`1133`）经 `HTML` 全局变量 | 支持 mtime 热重载（`1306`–`1308`）；`_send_html` 输出（`1310`） |
| `/desktop` | `1318` | `PROJECT / "desktop-ui" / "index.html"`（`1320`） | **每次请求都同步读盘**（`1320`），失败输出 `<h1>Desktop UI missing</h1>`（`1323`） |

- **本机不存在 `PROJECT/desktop-ui` 目录**（文件系统检查：`resources\app` 下无 `desktop-ui`）→ `/desktop` 当前**必然**返回 `1323` 的占位页。
- `/desktop` 无 mtime 缓存、无重载逻辑，纯读文件（`1319`–`1321`）。

---

## 4. 完整路由表

### 4.1 统计口径

- **独立 `/api/...` 路径：51 个**。
- **按 (路径, 方法) 计：55 条**（其中 `/api/config`、`/api/plugins/action`、`/api/protocol/config`、`/api/protocol/logs` 同时注册了 GET 与 POST，共 4 个双方法路径）。
- **非 API 路由：5 条**（`/`、`/index.html`、`/desktop`、`/page`、`/memes/*`）+ 1 条静态通配（`/assets/*` 及一切非 `/api/` 路径）+ `OPTIONS` 预检。
- 分发方式：`do_GET` 为一条 `if path == ...` 长链（`1296`–`2331`，约 1035 行）；`do_POST` 为另一条长链（`2333`–`3866`，约 1533 行）。**没有路由表数据结构、没有正则分发**。

### 4.2 页面与静态资源（非 `/api`）

| 路径 | 方法 | 作用 | 处理位置 |
|---|---|---|---|
| `/` | GET | 返回 SPA 主页面（支持 mtime 热重载） | `1304`–`1316` |
| `/index.html` | GET | 同上（与 `/` 同一分支） | `1304`–`1316` |
| `/desktop` | GET | 读 `PROJECT/desktop-ui/index.html` 返回；缺失时返回占位页 | `1318`–`1324` |
| `/page?plugin_id=` | GET | 返回 `PROJECT/plugins/<id>/ui.html`（插件 UI iframe 入口） | `1490`–`1510` |
| `/memes/<rel>` | GET | 返回表情包图片（带内存缓存） | `2311`–`2328` |
| `/assets/*` 及其它非 `/api/` 路径 | GET | 从 `HERE` 目录做白名单静态文件服务 | `1327`–`1353` |
| `*` | OPTIONS | CORS 预检，返回 204 | `1281`–`1294` |

### 4.3 状态与总览

| 路径 | 方法 | 作用 | 处理位置 |
|---|---|---|---|
| `/api/status` | GET | 仪表盘总数据：先读后台线程维护的 `_STATUS_CACHE`（`1356`–`1358`），缓存为空才现算并从 bot_core 拉取（`1360`–`1383`） | `1355`–`1384` |
| `/api/bot/status` | GET | Bot 进程状态（pid/uptime/qq_path/protocol_enabled） | `2156`–`2158` → `get_bot_status()` `3953`–`3967` |
| `/api/tools` | GET | 群聊工具白名单目录（12 项，含 `group_default`） | `2152`–`2154` → `_tools_catalog()` `1086`–`1088`，常量 `1071`–`1084` |
| `/api/gallery` | GET | 图片库列表（`image_cache`/`memes`/AppData 三处，最多 50 条） | `1391`–`1394` → `get_image_gallery()` `1091`–`1112` |
| `/api/token-stats` | GET | Token 用量快照 + 最近 50 条历史 | `2079`–`2086`（调 `token_stats.snapshot/history`） |
| `/api/command` | POST | 底层控制台指令：`status`/`restart`/`config_reload`/`help`，其余返回"未实现"（`3201`–`3202`） | `3178`–`3206` |

### 4.4 配置与模型供应商

| 路径 | 方法 | 作用 | 处理位置 |
|---|---|---|---|
| `/api/config` | GET | 返回配置全文（**所有敏感值掩码**），`providers` 优先取 `bot_config.json`、否则回落 `webui/providers.json` | `2110`–`2150` |
| `/api/config` | POST | 保存配置：Key 清除（`2489`–`2499`）、空工具名单删除（`2500`–`2502`）、静默阈值钳制（`2504`–`2509`）、provider Key 迁移到凭据管理器（`2512`–`2550`）、全局 Key 写凭据管理器（`2552`–`2561`）、落盘 + 热重载（`2562`–`2568`） | `2485`–`2569` |
| `/api/config/reload` | POST | 仅通知 bot_core 热重载配置，不改动配置 | `2480`–`2483` → `push_bot_reload()` `631`–`645` |
| `/api/config/model` | POST | 快速切模型：一次性写 `api_provider`/`api_url`/`model_name` 与 `last_model`，然后热重载 | `3102`–`3130` |
| `/api/providers/catalog` | GET | 快切模型下拉数据源（不含任何 Key） | `2088`–`2108` |
| `/api/providers/models` | POST | 拉取远端模型列表（按 provider 类型选鉴权与 URL），成功后自动持久化到该 provider 的 `models` | `2850`–`2966` |
| `/api/llm/test` | POST | 文本模型真实连通性测试（表单值优先，掩码 Key 视为无效） | `3132`–`3176` |
| `/api/qq-path` | POST | 单独把 `qq_path` 写进 `bot_config.json`（直接读写文件，不走 `save_config`） | `3208`–`3224` |

### 4.5 Bot 生命周期

| 路径 | 方法 | 作用 | 处理位置 |
|---|---|---|---|
| `/api/bot/start` | POST | 拉起 `bot_core.py` 子进程 | `3050`–`3053` → `start_bot()` `3896`–`3930` |
| `/api/bot/stop` | POST | 终止 Bot 子进程（terminate → 5s → kill） | `3055`–`3058` → `stop_bot()` `3932`–`3951` |

### 4.6 协议面板（`/api/protocol/*` 共 14 条）

| 路径 | 方法 | 作用 | 处理位置 |
|---|---|---|---|
| `/api/protocol/status` | GET | 协议层运行态：`protocol_running`/`driver`/`drivers`/`api_port`/`event_port`/`qq_path`/`status`/三驱动配置/风险文案 | `2160`–`2192` |
| `/api/protocol/config` | GET | 返回 `protocol_driver` + `protocol_drivers` 全量配置 | `2194`–`2201` |
| `/api/protocol/config` | POST | 按 driver 合并写入 `protocol_drivers.<driver>`，`qq_path` 同时写顶层 | `3367`–`3390` |
| `/api/protocol/start` | POST | 新建 `ProtocolManager()`、注册状态回调、`start()`，存到 `S._protocol_manager` | `3226`–`3237` |
| `/api/protocol/stop` | POST | 停止并保留引用（**未置空 `S._protocol_manager`**） | `3239`–`3249` |
| `/api/protocol/switch` | POST | 风险二次确认（`3270`–`3280`）→ 停旧驱动（`3281`–`3289`）→ 持久化 `protocol_driver`/`protocol_enabled`（`3290`–`3294`），**不自动启动新驱动** | `3258`–`3299` |
| `/api/protocol/check` | POST | 启动前自检（只读，不启动进程） | `3251`–`3256` → `_protocol_check_items()` `290`–`393` |
| `/api/protocol/detect` | POST | 全盘扫描常见安装区，返回 4 类驱动路径候选 | `3301`–`3303` → `_detect_protocol_paths()` `94`–`185` |
| `/api/protocol/auto-detect` | POST | 结合"安装/运行中/端口探测"给出候选与推荐驱动 | `3334`–`3339` → `_auto_detect_protocol_drivers()` `229`–`287` |
| `/api/protocol/classify` | POST | 按文件名/目录名启发式判断一个路径属于哪个驱动 | `3305`–`3309` → `_classify_driver_path()` `188`–`207` |
| `/api/protocol/fs` | POST | 目录浏览（默认 `C:\`），返回子项与父目录 | `3341`–`3365` |
| `/api/protocol/open-site` | POST | 在系统浏览器打开域名白名单内的站点 | `3311`–`3332`（白名单 `3314`–`3315`） |
| `/api/protocol/logs` | GET / POST | 返回日志（`limit` 默认 200，上限受环形缓冲 600 限制） | GET `2203`–`2213`；POST `3392`–`3395` → `_collect_logs()` `1026`–`1037` |
| `/api/protocol/logs/clear` | POST | 清空内存日志并置 `_LOG_CLEARED = True` | `3397`–`3403` |

### 4.7 插件

| 路径 | 方法 | 作用 | 处理位置 |
|---|---|---|---|
| `/api/plugins` | GET | 插件列表：优先代理 bot_core（并过滤 `adapter` 壳），否则扫盘解析 `PLUGIN_META`/`PLUGIN_MANIFEST` | `1988`–`2077` |
| `/api/plugins/action` | GET | 通用动作通道（query string 传参）。流程：`_invoke_generic_plugin` → `slang_learner` 专用分支 → `personality_engine` → `ego` → 通用 `get_config/set_config/toggle` → `web_operator` | `1512`–`1986` |
| `/api/plugins/action` | POST | 同上（body 传参），多出 `slang_inject_all`/`slang_reject_all` 与 `web_operator` 的 `chain_run`/`web.autonomous` | `3405`–`3863` |
| `/api/plugins/page` | GET | 返回 `plugins/<id>/ui.html` | `1468`–`1488` |
| `/api/plugins/toggle` | POST | 写 `plugins/plugin_state.json`（同时写 `已启用`/`enabled` 两套键及小写别名）并调 plugin_manager 使能/禁用 | `3060`–`3100` |

### 4.8 记忆

| 路径 | 方法 | 作用 | 处理位置 |
|---|---|---|---|
| `/api/memory` | GET | 记忆总览：自学习消息数/活跃群数/存储 KB/表达列表（查库失败时返回 3 条**硬编码示例**，`1405`–`1409`） | `1396`–`1412` |
| `/api/memory/list` | GET | 私聊记忆清单（`super_memory.db` 按 `scope_id` 聚合） | `1414`–`1435` |
| `/api/memory/detail` | GET | 按 `?qq=` 拉取该私聊的逐轮对话 | `1437`–`1466` |

数据源：`PROJECT/plugins/ego/super_memory/data/super_memory.db`（`1418`、`1451`）；自学习统计与表达来自 `PROJECT/self_learning_db/self_learning.db`（`965`、`1010`）。

### 4.9 表情包（memes）

| 路径 | 方法 | 作用 | 处理位置 |
|---|---|---|---|
| `/api/memes` | GET | 列出全部表情（含 sidecar `.json` 与 `tags.json` 标签合并） | `2243`–`2278` |
| `/api/memes/emotions` | GET | 汇总全部标签作为情绪集合（排序去重） | `2280`–`2308` |
| `/api/memes/upload` | POST | base64 上传图片 + 写同名 `.json` 标签；文件名取 basename 并限扩展名 | `2970`–`2993` |
| `/api/memes/delete` | POST | 删除图片及同名标签文件（basename + 扩展名校验） | `2995`–`3014` |
| `/api/memes/tags` | POST | 覆写某图的标签 sidecar（**未做名称校验**） | `3016`–`3028` |
| `/api/memes/import` | POST | 从指定目录递归导入图片到 `memes/memes` | `3030`–`3048` |
| `/memes/<rel>` | GET | 静态返回表情图片（内存缓存 + basename 兜底查找） | `2311`–`2328` → `_serve_meme()` `1148`–`1199` |

### 4.10 生图（ComfyUI）

| 路径 | 方法 | 作用 | 处理位置 |
|---|---|---|---|
| `/api/imagegen/status` | GET | ComfyUI 可达性 + 全部生图参数 | `2215`–`2240` |
| `/api/imagegen/generate` | POST | 建客户端 → 载入/内联 workflow → 入队 → 等图（超时 `comfyui_timeout`） | `2369`–`2443` |
| `/api/imagegen/test` | POST | 连通性 + 版本号 | `2445`–`2459` |
| `/api/imagegen/workflow` | POST | 保存上传的 workflow 到 `PROJECT/webui/uploaded_workflows/`（文件名正则清洗） | `2461`–`2478` |

### 4.11 本地 LLM（llama-server / Ollama / LM Studio）

| 路径 | 方法 | 作用 | 处理位置 |
|---|---|---|---|
| `/api/llama/test` | POST | 按 driver 测试：ollama/lmstudio 直连 chat；llama-server 先查 `/health`，未就绪则拉起并**最多轮询 90 秒** | `2571`–`2665` |
| `/api/llama/start` | POST | 仅支持 llama-server：拉起并**最多轮询 120 秒** | `2667`–`2744` |
| `/api/llama/stop` | POST | POST `/shutdown`，失败也返回 `ok: True`（`2832`） | `2822`–`2833` |
| `/api/llama/open` | POST | 用系统默认方式打开 `http://127.0.0.1:<port>` | `2835`–`2848` |
| `/api/llama/scan` | POST | 扫描 `llama-server.exe`/`ollama.exe`/`.gguf`（指定目录或全盘盘符 + 常见目录，上限 10/60 条） | `2746`–`2820` |

### 4.12 日志

| 路径 | 方法 | 作用 | 处理位置 |
|---|---|---|---|
| `/api/protocol/logs` | GET / POST | 内存环形日志，空时回落磁盘日志 | `2203`–`2213` / `3392`–`3395` |
| `/api/protocol/logs/clear` | POST | 清空内存日志 | `3397`–`3403` |
| `/api/chatlogs` | GET | **直读磁盘日志**（不走 `_collect_logs`，因此忽略"已清空"标记） | `1386`–`1389` → `get_chat_logs(200)` `1039`–`1068` |

### 4.13 前端实际调用情况（交叉验证，用于识别死端点）

对 `webui_artistic_v3.html` 全文检索（`API('/...')` 与 `fetch('/api/...')`）：

- 被调用（存在对应 `API('...')` 调用点）：`/protocol/auto-detect`（`html:614`、`html:828`）、`/protocol/detect`（`html:635`、`html:3608`）、`/protocol/config`（`html:640`、`html:3479`+、`html:3488`、`html:3502`、`html:3572`、`html:3590`、`html:3602`）、`/protocol/check`（`html:713`、`html:734`、`html:3469`）、`/protocol/switch`（`html:744`）、`/protocol/status`（`html:790`、`html:4016`）、`/bot/status`（`html:791`）、`/protocol/logs`（`html:792`、`html:829`、`html:835`）、`/protocol/logs/clear`（`html:837` 区块内）、`/protocol/start`（`html:3457`）、`/protocol/stop`（`html:3479` 区块）、`/protocol/classify`（`html:3558`）、`/protocol/fs`（`html:3658`）、`/protocol/open-site`（`html:3353`）、`/qq-path`（`html:3544`、`html:3576`）、`/config`（GET `html:836`、`html:1598`、`html:2139` 区、`html:2704`、`html:4075`；POST 多处）、`/config/model`（`html:1966`）、`/config/reload`（`html:2101`）、`/tools`（`html:1712`）、`/llm/test`（`html:1872`、`html:2042`）、`/llama/test|start|scan|stop|open`（`html:2276`–`2530`）、`/providers/catalog`（`html:1935`）、`/providers/models`（`html:2019`、`html:2628`）、`/token-stats`（`html:2780`）、`/memes`（`html:3118`）、`/memes/upload|delete|tags`（`html:3244`–`3325`）、`/memory`（`html:121568` 偏移处 `loadMemory`）、`/memory/list`、`/memory/detail?qq=`（模板串）、`/status`（`html:115758`、`html:116000`，监控页每轮轮询）、`/gallery`（`html:120914` 偏移处 `loadGallery`）、`/plugins`（`html:3696`、`html:3779` 邻域、`html:344447` 偏移处第二份 `loadPlugins`）、`/plugins/toggle`（`html:3779`）、`/plugins/action`（`html:4037`）、`/imagegen/status`（`html:164757` 偏移处）、`/command`（`html:839` 区块 `mountConsole`）、`/bot/start|stop`（`html:3350` 区块）。
- **前端从不调用**（死端点）：`/api/chatlogs`（全文 `chatlogs` 出现 6 次，均在路由/菜单/文案，`/api/chatlogs` 匹配数 0）、`/api/plugins/page`（`plugins/page` 匹配数 0；前端用的是 `/page?plugin_id=`，`html:841`、`html:3804`）。
- **前端调用但不属于本服务**（指向 bot_core `127.0.0.1:3005`）：`gmApi` 定义 `fetch('http://127.0.0.1:3005'+path)`（`html:2836`），因此 `/api/groups`（`html:2839`）、`/api/groups/action`、`/api/groups/settings`、`/api/groups/members`、`/api/friends`（`html:3010`）、`/api/friends/settings`、`/api/friends/action` 全部由 **bot_core** 提供，**不在 `webui_server.py` 中**。
- 通知测试同样是 bot_core：`fetch('http://127.0.0.1:3005/api/admin/notify_test', {method:'POST'})`（`html:837` 区块）；而**通知配置**是通过 `POST /api/config` 带 `notify_admin` 负载写入（`html:836`/`html:837` 区块）。

---

## 5. 安全防护

### 5.1 `_host_allowed()` —— DNS rebinding 防护

```python
1239: def _host_allowed(self):
1240:     """DNS rebinding 防护：Host 头必须是本机。"""
1241:     host = (self.headers.get("Host") or "").strip().lower()
1242:     bare = host.split(":")[0]
1243:     return bare in ("localhost", "127.0.0.1", "::1", "")
```

- 只取 `Host` 头冒号前部分做白名单比较（`1241`–`1243`），命中 `localhost`/`127.0.0.1`/`::1` **或空串**即放行。
- 空 `Host` 被放行（`1243` 的 `""`）——即无 Host 头的原始 HTTP 请求不受此防护约束。
- 调用点：`OPTIONS`（`1282`）、`GET`（`1297`）、`POST`（`2334`）；不通过则 403 + `Content-Length: 0`（`1283`–`1285`、`1298`–`1301`、`2335`–`2338`）。
- 与 `run()` 默认绑定 `127.0.0.1`（`4007`）自洽；若改绑其它地址，该白名单会一律 403（未运行时验证）。

### 5.2 `_allowed_origin()` —— CORS

```python
1224: def _allowed_origin(self):
1227:     origin = self.headers.get("Origin", "") or ""
1228:     if not origin:
1229:         return "*"          # 无 Origin（同源 / curl 等）放行
1231:     o = urlparse(origin)
1232:     host = (o.hostname or "").lower()
1233:     if host in ("localhost", "127.0.0.1", "::1") or origin == "null":
1234:         return origin
1237:     return ""
```

- 无 `Origin` 头 → 返回 `"*"`（`1228`–`1229`）；本机来源回显原 Origin（`1233`–`1234`，含字面量 `"null"`，兼容 `file://`）；其它来源返回空串（`1237`）。
- 应用点：`_send_json`（`1250`–`1252`）、`_send_html`（`1270`–`1272`）、`do_OPTIONS`（`1288`–`1290`）。返回空串时**不发送** `Access-Control-Allow-Origin`，浏览器即阻止跨源读取。
- 注意：`"*"` 与 `Access-Control-Allow-Headers: Content-Type, Authorization`、`Allow-Methods: GET, POST, OPTIONS` 同时下发（`1253`–`1254`），不带 `Allow-Credentials`。

### 5.3 写操作的 CSRF 防护（仅 POST）

```python
2341: _origin = self.headers.get("Origin") or ""
2342: if _origin:
2345:     _oh = urlsplit(_origin).hostname or ""
2348:     if _oh and _oh not in ("127.0.0.1", "localhost", "::1"):
2349:         403
```

- **只有 `do_POST` 做**该检查（`2339`–`2352`），且是独立于 `_allowed_origin` 的第二份实现（`2341`–`2348`），两份逻辑对 `Origin: null` 的处理不一致（`1233` 允许，`2348` 拒绝）。
- `do_GET` **没有**任何 Origin/CSRF 检查（`1296`–`1301` 仅做 Host 校验）。而 `GET /api/plugins/action`（`1512`）会**改变状态**（如 `ego` 的 `set_config` `1714`–`1718`、`memory_clear` `1744`–`1764`、`reload` `1811`–`1826`）——详见第 14 节。

### 5.4 静态文件目录穿越防护

```python
1327: if path.startswith("/assets/") or (path.startswith("/") and not path.startswith("/api/")):
1330:     rel = unquote(path.lstrip("/"))
1331:     file_path = (HERE / rel).resolve()
1332:     _root = str(HERE.resolve())
1333:     _real = str(file_path)
1334:     in_dir = (_real == _root) or _real.startswith(_root + os.sep)
1335:     if in_dir and file_path.is_file() and file_path.suffix.lower() in _STATIC_EXTS:
```

- 三重校验：`unquote` 解码（`1330`）→ `resolve()` 规范化（`1331`）→ 前缀必须落在 `HERE` 目录内（`1334`）→ 必须是文件（`1335`）→ 扩展名必须在白名单（`1335`）。
- 扩展名白名单 `_STATIC_EXTS`：`.html .htm .css .js .mjs .json .map .png .jpg .jpeg .gif .webp .svg .ico .woff .woff2 .ttf .txt .md`（`67`–`69`），注释明确目标是"杜绝 `.log`/`.py`/`.db`/`.env` 等敏感文件被静态读出"（`66`），并说明此前 `HERE / path.lstrip("/")` 不筛 `..` 的历史漏洞（`1328`–`1329`）。
- 越界或不匹配则不 `return`，继续走后续判定，最终 404（`2330`–`2331`）。
- Content-Type 仅按后缀手工映射 css/png/svg，其余**默认 `application/javascript`**（`1338`–`1344`）。

### 5.5 API Key 掩码

| 函数/逻辑 | 行号 | 说明 |
|---|---|---|
| `_is_redacted_key(k)` | `72`–`81` | 判空、`«redacted` 前缀、`****` 后缀 → 视为占位符（不应写回凭据管理器） |
| `_mask_secret(k, keep=6)` | `84`–`91` | 空值返回 `""`；`len<=6` 返回 `«redacted»`；否则 `«redacted:<前6位>…»` |
| provider 级掩码 | `2135`–`2136` | `/api/config` GET 时对每个 provider 的 `api_key` 掩码 |
| `has_api_key` 标记 | `2138`–`2143` | 查 `cred_store.exists_cred(provider_cred_name(id))`，供前端显示"清除 Key" |
| 顶层 `api_key` 掩码 | `2145`–`2146` | 掩码后才回传 |
| `onebot_token` 掩码 | `2147`–`2148` | 同上 |
| 写回时过滤 | `2487` | POST `/api/config` 的 `update` 直接剔除 `api_key`/`api_key_clear` |
| provider Key 落库 | `2527`–`2539` | 明文写凭据管理器；掩码值只在旧配置存有明文时做一次性迁移（`2533`–`2538`）；随后 `_p.pop("api_key", None)` |
| 全局 Key 落库 | `2552`–`2561` | 非掩码且不含 `"YOUR"` 才写 `MODEL_API_KEY_CRED` |
| 落盘前剔除 | `2562` | `cfg.pop("api_key", None)` |
| `save_config` 二次防御 | `946` | `to_save = {k: v for k, v in cfg.items() if k != "api_key"}` |
| 模型拉取取 Key | `803`–`817` | 请求 Key > provider 内 Key > `config.get_provider_api_key` |

### 5.6 其它已实现的白名单/校验

- `/api/protocol/open-site` 域名白名单：`napneko.github.io`、`llonebot.github.io`、`snowluma.github.io`、`github.com`、`github.io`、`localhost`、`127.0.0.1`、`::1`（`3314`–`3315`），比较方式为等值或子域后缀（`3320`），不通过返回"域名不在允许列表"（`3324`）。
- `/api/memes/upload`：`Path(raw_name).name` 取 basename + 拒绝 `.`/`..` + 扩展名白名单 `{.jpg,.jpeg,.png,.gif,.webp}`（`2975`–`2979`），注释明确防 `..\..\evil.png` 任意写（`2974`）。
- `/api/memes/delete`：basename + 扩展名白名单（`2999`–`3003`）。
- `/api/imagegen/workflow`：文件名正则清洗 `re.sub(r'[^A-Za-z0-9_.-]+', '_', filename)`（`2468`），输出固定在 `PROJECT/webui/uploaded_workflows`（`2465`–`2469`）。
- `/api/protocol/config`：拒绝非 dict 的 `config`（`3373`–`3375`），并剔除 `qq_path` 后合并（`3380`）。

---

## 6. 配置读写

### 6.1 读路径

- 模块级 `CONFIG_CACHE = load_config()`（`954`）——注意该对象**只有写入（`.update`）没有任何读取**：`2566`、`2956`、`3127`、`3220` 四处写入，全文无读取点 → 事实上的死缓存。
- 所有 GET 分支都是现调 `load_config()`：`/api/config`（`2111`）、`/api/providers/catalog`（`2092`）、`/api/protocol/status`（`2168`）、`/api/protocol/config`（`2195`）、`/api/imagegen/status`（`2216`）、`/api/config/model`（`3110`）、`/api/llm/test`（`3135`）、`get_bot_status`（`3966`）、`/api/llm/...`（`2601` 附近无，实际在 `_make_autonomous_chat` `825`、`_embed_config` 无）。
- `load_config` 的优先级与缓存语义见第 2.2 节（`config.py:381`–`441`）。

### 6.2 写路径：`save_config` 与 `config.py` 的关系

```python
944: def save_config(cfg):
945:     p = PROJECT / "bot_config.json"
946:     to_save = {k: v for k, v in cfg.items() if k != "api_key"}
947:     try:
948:         with open(str(p), 'w', encoding='utf-8') as f:
949:             json.dump(to_save, f, ensure_ascii=False, indent=2)
950:         return True
951:     except Exception:
952:         return False
```

- **`webui_server.py` 自己定义了一个同名 `save_config`（`944`–`952`），并未 import `config.save_config`**：模块顶部只导入 `load_config`（`63`），全文无 `save_config` 的 import。
- 两者差异：
  | 维度 | `webui_server.save_config`（`944`–`952`） | `config.save_config`（`config.py:444`–`470`） |
  |---|---|---|
  | 目标文件 | 只写 `PROJECT/bot_config.json`（`945`） | 先写 `APP_CONFIG_FILE`（`config.py:457`），失败回退 AppData（`config.py:465`） |
  | api_key | 硬剔除（`946`） | 硬剔除，除非 `save_api_key=True`（`config.py:451`–`453`） |
  | 缓存失效 | 不调用 `invalidate_config_cache()` | 每次成功后调用（`config.py:459`、`config.py:467`） |
  | 原子性 | 直接覆写，非原子 | 直接覆写，非原子 |
  | 返回值 | bool | bool |
- 由于 `config.load_config` 的缓存签名基于文件 `mtime/size`（`config.py:388`–`390`），`webui_server.save_config` 未显式失效缓存**通常**也能被下一次读感知；但同一秒内写入且文件大小恰好不变时存在读到旧值的理论窗口（未运行时验证）。
- 写入调用点：`/api/config` POST（`2563`）、`/api/config/model`（`3124`）、`/api/protocol/switch`（`3294`）、`/api/protocol/config`（`3385`）、`/api/providers/models` 的 models 自动持久化（`2955`）。
- 例外：`/api/qq-path` **绕过 `save_config`**，自己 `json.loads` 整个 `bot_config.json`、改一个键再整份写回（`3210`–`3219`），因此会**丢弃内存中未落盘的其它修改**，也只写程序目录、无 AppData 回退。

### 6.3 写入前的钳制与清洗

| 处理 | 行号 | 规则 |
|---|---|---|
| 剔除敏感键 | `2487` | `update = {k: v for k, v in data.items() if k not in ("api_key", "api_key_clear")}` |
| Key 清除 | `2489`–`2499` | `api_key_clear == "_global_"` → 删 `MODEL_API_KEY_CRED`；否则按 provider id 清专属凭据 |
| 空工具名单 | `2500`–`2502` | `group_tool_allowlist` 为空 → `cfg.pop("group_tool_allowlist")`，回到默认 `GROUP_TOOLS` |
| **静默阈值钳制** | `2504`–`2509` | `group_idle_exit_min_silence` 转 int（失败按 0），然后 `max(sil, 30)` → **写入值恒 ≥ 30**；注释说明防止前端填 0 导致刚回复完就"退场" |
| providers Key 迁移 | `2512`–`2550` | 逐个 provider：`_clear_key` → 清凭据（`2528`–`2529`）；明文 Key → 存凭据管理器（`2530`–`2531`）；掩码值 → 从旧配置迁移明文（`2532`–`2538`）；`api_key`/`has_api_key` 不落盘（`2526`、`2539`）；已删除 provider 的凭据一并清理（`2541`–`2549`） |
| 合并 | `2551` | `cfg.update(update)` —— **对 `update` 的键没有白名单**，请求体里任意键都会进入 `bot_config.json` |
| 全局 Key | `2552`–`2561` | 掩码/含 `YOUR` 的不写 |
| 落盘前 | `2562` | `cfg.pop("api_key", None)` |
| 保存后 | `2565`–`2567` | 成功则 `CONFIG_CACHE.update(cfg)`（`2566`）+ `push_bot_reload()`（`2567`） |

- 钳制只有 `group_idle_exit_min_silence` 一项（`2504`–`2509`），其余数值/枚举字段无校验（例如 `comfyui_port`、各端口、概率值均可为任意 JSON 值）。
- `save_config` 的返回值直接决定响应 `{"success": success, "reload": reload_result}`（`2568`）。

---

## 7. 插件通道

### 7.1 `_invoke_generic_plugin()` 的三级约定

```
526: def _invoke_generic_plugin(plugin_id, action, params):
537:     mod = __import__(f"plugins.{plugin_id}", fromlist=["PLUGIN_ACTIONS", "execute_action"])
541:     acts = getattr(mod, "PLUGIN_ACTIONS", None)
542:     exec_fn = getattr(mod, "execute_action", None)
543:     supports_standard = isinstance(acts, dict) or callable(exec_fn)
544:     if not supports_standard:
545:         return None
546:     # 1. PLUGIN_ACTIONS 映射
548:         if isinstance(acts, dict):
549:             fn = acts.get(action)
550:             if callable(fn):
552:                     return fn(params or {})
557:     # 2. 标准动作兜底：get_config / set_config / toggle
559:         if action == "get_config":  ... return fn()
563:         elif action == "set_config": ... return fn(params or {})
567:         elif action == "toggle":     ... return fn()
573:     # 3. execute_action 兜底
574:     if callable(exec_fn):
576:             return exec_fn(action, params or {})
579:     return None
```

| 级别 | 约定 | 行号 | 行为 |
|---|---|---|---|
| 导入闸门 | `__import__(f"plugins.{plugin_id}", ...)` | `537`–`539` | 导入失败 → 返回 `None`（继续交硬编码分支） |
| 闸门 2 | 必须声明 `PLUGIN_ACTIONS`(dict) 或 `execute_action`(callable) | `541`–`545` | 未声明 → 返回 `None`（保持对老插件的完全兼容，注释见 `533`–`534`） |
| 第 1 级 | `PLUGIN_ACTIONS[action](params)` | `546`–`556` | 命中即调用；异常包装为 `{"error": "[<id>] <action> failed: ..."}`（`554`） |
| 第 2 级 | `get_config()` / `set_config(params)` / `toggle()` | `557`–`572` | 仅在 action 名恰好是这三者时触发；异常同上包装（`572`） |
| 第 3 级 | `execute_action(action, params)` | `573`–`578` | 最终兜底 |
| 返回 | 都未命中 → `None` | `579` | 调用方据此回落到硬编码分支 |

- 调用点：`GET /api/plugins/action`（`1524`–`1527`）、`POST /api/plugins/action`（`3419`–`3422`）。两处都是"非 None 即直接用 `_send_json(200, _generic)` 返回"。
- 硬编码分支（本条通道返回 `None` 时才执行）：
  - `slang_learner`：GET 版 `1528`–`1663`；POST 版 `3423`–`3528`（POST 版多 `get_config`/`set_config`/`clear_injected`，`3511`–`3523`，且 `clear_injected` 会直接改写 `_learner.INJECTED_FILE`，`3517`–`3522`）。
  - `personality_engine`：GET `1664`–`1693`；POST `3529`–`3558`。
  - `ego`：GET `1694`–`1896`；POST `3559`–`3774`（含 `memory_stats`/`memory_vector_stats`/`memory_persona`/`memory_clear`/`preview_prompt`/`reload`/`health`/`wizard_*`/`mood_*` 等）。
  - `web_operator`：GET `1936`–`1979`；POST `3775`–`3829`（POST 版多 `chain_run` `3814`–`3816`、`web.autonomous` `3817`–`3824`，后者通过 `_make_autonomous_chat()` `820`–`848` 注入宿主模型回调）。
  - 通用兜底 `get_config/set_config/toggle`：GET `1897`–`1985`；POST `3830`–`3862`（未命中返回 404 `{"error": "unknown plugin_id or action"}`，`3862`）。
- `_make_autonomous_chat()`（`820`–`848`）：复用主模型配置（`api_provider`/`api_url`/`model_name`）与凭据管理器里的 Key 构造 `chat(messages, tools, max_tokens)`；无 Key 时抛 `RuntimeError('未检测到可用的模型 API Key...')`（`841`–`842`）。

### 7.2 插件 UI 页如何被服务

- **`/api/plugins/page?plugin_id=<id>`**（`1468`–`1488`）：从 query 取 `plugin_id`（`1471`–`1473`），拼 `PROJECT / "plugins" / plugin_id / "ui.html"`（`1479`），不存在返回 `{"error": "plugin ui not found"}`（`1481`），存在则 `read_text(utf-8)` 后 `_send_html`（`1484`–`1485`）。
- **`/page?plugin_id=<id>`**（`1490`–`1510`）：**与上一段逐行等价**（`1491`–`1507` vs `1470`–`1486`）。
- 前端实际使用 `/page?plugin_id=`：`html:841`（`mountPluginPage` 拼 iframe src）与 `html:3804`（router 中 `plugin_*` 页面拼 iframe）。`/api/plugins/page` **无任何前端调用点**（`plugins/page` 匹配数 0）。
- 本机 `plugins/` 下确有 20 个 `ui.html`（含 `ego`、`chat_style`、`mood_engine`、`super_memory`、`web_operator`、`这样脆弱的东西` 等，文件系统检查）。
- **任务描述中的 `/api/plugins/ui` 在该文件中不存在**——全文检索 `plugins/ui` 无匹配；等价功能对应 `1468` 的 `/api/plugins/page` 与 `1490` 的 `/page`。

### 7.3 插件开关与状态文件

- `load_plugin_state()`：读 `PROJECT/plugins/plugin_state.json`，异常/不存在返回 `{}`：`3969`–`3976`。
- `save_plugin_state(state)`：整份 `json.dumps(..., ensure_ascii=False, indent=2)` 覆写，异常静默：`3978`–`3983`。
- `/api/plugins/toggle`（`3060`–`3100`）：同时写 `已启用[name]`、`enabled[name]`、以及两者的小写别名（`3074`–`3078`，注释解释为防 `EGO`/`ego` 重复键），随后 `init_plugin_manager()` + `pm.enable/disable(name)`（`3082`–`3089`），失败仅写协议日志（`3091`）；成功则调整 `S.plugin_active` 与活动流（`3092`–`3098`）。
- 插件列表 `/api/plugins`（`1988`–`2077`）：先代理 bot_core（`1989`–`1993`，过滤带 `adapter` 的壳）；否则扫 `PROJECT/plugins`，跳过 `_`/`.` 前缀与无 `__init__.py` 的目录（`2006`–`2012`），用**文本查找 + exec 解析** `PLUGIN_META =` / `PLUGIN_MANIFEST =`（`2017`–`2039`），docstring 首行作 description（`2040`–`2045`），文件名含 `adapter` 字样即标记为适配壳并跳过（`2048`–`2056`）。

---

## 8. 协议面板

### 8.1 与 `protocol_manager` 的协作

- `ProtocolManager` 是门面（`protocol_manager.py:36`–`106`）：`__init__` 按 `config.protocol_driver` 取驱动（`protocol_manager.py:45`，读取逻辑 `protocol_manager.py:25`–`33`），未注册则回退 `napcat`（`protocol_manager.py:46`–`51`），再 `registry.build(driver)`（`protocol_manager.py:52`）。
- `_running` 是 property，转发到 `adapter.is_running()`（`protocol_manager.py:59`–`65`）——这正是 `webui_server` 用 `getattr(mgr, '_running', False)` 判断的依据（`2162`）。
- `start()` 启动后若驱动声明 `SYNC_ONEBOT_URL` 还会回写 `onebot_url`（`protocol_manager.py:77`–`86`）。
- `webui_server` 的持有方式：`ProtocolManager()` → `set_status_callback(_protocol_status_callback)` → `start()` → 挂到 `S._protocol_manager`（`3228`–`3233`）。`S._protocol_manager` **未在 `State.__init__` 中初始化**（`680`–`697` 无该字段），因此全文一律用 `getattr(S, '_protocol_manager', None)` 访问（`2161`、`3240`、`3281`）。
- 状态回调 `_protocol_status_callback`（`664`–`677`）：`started`/`stopped` 时置 `S.protocol_running`（`668`–`671`），并把 detail 写进协议日志（`672`–`675`）。
- 驱动注册表：`from protocols import registry`（`299`、`2164`、`3264`），`registry.is_registered`（`305`、`3265`）、`registry.build`（`309`）、`registry.get_driver_ids()`（`2165`，异常回落 `["napcat"]` `2167`）。

### 8.2 各端点行为（详见第 4.6 节表格）

- **status**（`2160`–`2192`）：`protocol_running` 取 `mgr._running`（`2162`），`driver` 取配置（`2169`），`api_port`/`event_port` 默认 `3000`/`3005`（`2183`–`2184`），`status` 取 `mgr.get_status()`（`2173`），并回传三驱动配置与 `PROTO_RISK_INFO`（`2186`–`2190`）。
- **start/stop**：见 4.6；`stop` 后 `S._protocol_manager` **不被置空**（`3240`–`3249`），而 `switch` 里会置空（`3287`）——两处不一致。
- **switch**（`3258`–`3299`）：先做 `registry.is_registered` 校验（`3264`–`3267`）；对 napcat/llonebot/snowluma 要求 `confirm` 字段，否则返回 `need_confirm: True` 与风控文案（`3270`–`3280`）；确认后停旧驱动并在异常时写日志（`3281`–`3289`）；把 `protocol_driver` 与 `protocol_enabled=True` 落盘（`3291`–`3294`）；**不启动新驱动**，提示用户去配置后再手动启动（`3295`–`3296`）。
- **config**（GET `2194`–`2201` / POST `3367`–`3390`）：POST 走 `cfg.setdefault("protocol_drivers", {})` → `drivers.setdefault(driver, {})` → `cur.update(...)`（`3378`–`3380`），`qq_path` 额外写顶层（`3382`–`3384`）。
- **detect / auto-detect / classify / fs / open-site**：见 4.6。`open-site` 是唯一为"打开浏览器"单起线程的写端点（`3328`）。

### 8.3 QQ / NapCat 路径自动探测的实现

1. **`_detect_protocol_paths()`（`94`–`185`）** —— 无固定路径依赖的泛化扫描：
   - 目标可执行文件名常量：NapCat `NapCatWinBootMain.exe`/`napcat.exe`（`111`）、LLOneBot `llbot.exe`/`LLBot.exe`/`LLOneBot.exe`/`LLOneBot_WebUI.exe`（`112`）、SnowLuma `SnowLuma.exe`/`Lagrange.OneBot.exe`（`113`）、QQ `QQ.exe`/`QQNT.exe`（`159`）。
   - 扫描根 = 所有存在的盘符根（`139`）+ `~`（`140`，去重 `140`）+ `~/Downloads`/`Desktop`/`下载`/`桌面`（`142`–`145`）。
   - 深度限制 `depth >= 3` 即剪枝（`152`–`155`）；跳过名单 `_skip`（`106`–`109`，含 `Windows`、`Program Files*`、`AppData`、`node_modules`、`.git` 等）与点开头目录（`156`）。
   - 双通道识别：**目录名启发式** `_dir_kind`（`121`–`131`，napcat/snowluma|lagrange/llbot|llonebot/qq|tencent|imqq）与**文件名反查**（`172`–`180`）；SnowLuma 额外支持运行时目录识别 `node.exe + index.mjs`（`133`–`135`、`170`–`171`）。
   - 返回 `{"ok": True, "qq_paths", "napcat_paths", "llonebot_paths", "snowluma_paths"}`（`184`–`185`）。
2. **`_classify_driver_path(path)`（`188`–`207`）** —— 用户手选路径归属判断：先 `abspath`+`expanduser`（`194`），不存在返回 `unknown`（`195`–`196`）；`qq.exe`/`qqnt.exe` → `qq`（`199`–`200`）；文件名或目录名含 `napcat` → `napcat`（`201`–`202`）；含 `llbot`/`llonebot` → `llonebot`（`203`–`204`）；含 `snowluma`/`lagrange` → `snowluma`（`205`–`206`）；否则 `unknown`（`207`）。
3. **`_auto_detect_protocol_drivers()`（`229`–`287`）** —— "已安装 vs 运行中"综合推荐：
   - 端口探测 `_port_open`（`241`–`249`，0.4s 超时）：SnowLuma `5099`（`258`）、NapCat `3100`（`266`）、LLOneBot `7000`（`272`）。
   - 安装探测复用 `_detect_protocol_paths()`（`251`）；SnowLuma 要求真实存在（`254`–`255`）。
   - 排序：运行中优先，其次 `order = {snowluma:0, napcat:1, llonebot:2}`（`278`、`285`）；`recommended = active[0]["driver"]`（`286`）。
   - 附 `PROTO_RISK_INFO` 的风险级别/标签/文案（`280`–`283`，常量定义 `213`–`226`）。
4. **`_protocol_check_items(driver)`（`290`–`393`）** —— 启动前只读自检：
   - 依赖 `protocols.registry` 与 `protocol_manager._current_driver`（`299`–`300`），不可用直接返回错误（`302`）。
   - 按驱动区分必填项：napcat 校验 `exe_path`/`NapCatWinBootHook.dll`/`qq_path`/`qqnt.json`/`napcat.mjs` 均须存在（`325`–`334`，`must_exist=True`）；llonebot 校验 `exe_path`（`335`–`339`）；snowluma 校验 `node.exe`/`index.mjs`（`340`–`351`）；其它驱动只查 `exe_path`（`352`–`353`）。
   - 额外做**事件桥体检**：驱动在跑但 `bridge_state != "up"` 且 WS 端口可连 → 明确提示"戳一戳/通知类事件不会上报"（`355`–`390`，判定核心 `382`–`388`）。
   - `ok = not missing`（`392`–`393`）。

---

## 9. Bot 生命周期

### 9.1 启动入口选择（解释器与工作目录）

```python
3881: def _get_bot_cmd():
3882:     if getattr(sys, 'frozen', False):
3883:         exe_dir = os.path.dirname(sys.executable)
3884:         core_exe = os.path.join(exe_dir, 'EggBloom_v3_Core.exe')
3885:         if not os.path.exists(core_exe):
3886:             core_exe = os.path.join(exe_dir, '_internal', 'EggBloom_v3_Core.exe')
3887:         if os.path.exists(core_exe):
3888:             return [core_exe], os.path.dirname(core_exe)
3889:         return None, None
3890:     else:
3891:         script = PROJECT / 'bot_core.py'
3892:         if script.exists():
3893:             return [sys.executable, str(script)], str(PROJECT)
3894:         return None, None
```

- **frozen**：优先 exe 同目录的 `EggBloom_v3_Core.exe`，否则 `_internal\EggBloom_v3_Core.exe`（`3882`–`3888`），cwd = 该 exe 所在目录（`3888`）。
- **非 frozen**：`[sys.executable, PROJECT/bot_core.py]`，cwd = `PROJECT`（`3890`–`3893`）——即**解释器就是运行 webui_server 的那个解释器**。
- 找不到入口返回 `(None, None)`（`3889`、`3894`），`start_bot` 据此回"未找到 Bot 启动入口"（`3907`–`3908`）。

### 9.2 `start_bot()`（`3896`–`3930`）

- 进程级互斥：`_bot_lock`（`3898`）+ `_bot_process.poll() is None` 判定已在运行（`3899`–`3900`）；`_bot_lock` 定义于 `733`，`_bot_process` 于 `732`。
- 前置校验：`bot_qq` 为空则拒绝并提示"请先到配置中心填入 Bot QQ 号喵~"（`3904`–`3905`）；`api_key` 为空**只留 `pass` 注释不阻塞**（`3902`–`3903`）。
- 环境变量注入：`PYTHONUNBUFFERED=1`、`PYTHONIOENCODING=utf-8`（`3909`–`3911`）。
- 启动参数：`cwd=cwd`（`3915`）、`stdout=subprocess.PIPE`、`stderr=subprocess.STDOUT`（`3917`–`3918`）、`stdin=DEVNULL`（`3919`）、`text=True, encoding='utf-8', errors='replace'`（`3920`）、Windows 下 `CREATE_NO_WINDOW`（`3921`）。
- 状态：`S.bot_running = True`、`S.bot_start_time = time.time()`（`3923`–`3925`）。
- **日志去向**：`_start_bot_log_reader(_bot_process)`（`3927`）→ 起 daemon 线程逐行读 stdout，拼 `[Bot] ` 前缀写进内存协议日志 `PROTO_LOG`（`892`–`902`，核心 `895`–`898`），进程结束后追加 `[Bot] 进程已退出`（`901`）。因此 Bot 日志**只进内存环形缓冲（600 条）**，不落盘。
- 返回 `{'success': True, 'output': 'Bot 已启动', 'pid': ...}`（`3928`）；异常返回 `启动失败: {e}`（`3929`–`3930`）。

### 9.3 `stop_bot()`（`3932`–`3951`）

- 未运行（或 poll 非 None）→ 置 `_bot_process = None`、`S.bot_running = False`，返回"Bot 未运行"（`3935`–`3939`）。
- 运行中：`terminate()`（`3941`）→ `wait(timeout=5)`（`3943`）→ 超时 `kill()`（`3944`–`3945`）；异常返回"停止失败"（`3946`–`3947`）；成功后置空并把 `S.bot_running = False`（`3948`–`3950`）。
- 没有对子进程树做整树终止（对比 `main.js:151` 用 `taskkill /T`）。

### 9.4 状态查询（`3953`–`3967`）

- `alive` 来自真实进程 `poll()`（`3956`–`3959`）；但返回的 `bot_running` 是 `alive if S.bot_running else False`（`3962`）——即两处状态需同时为真。
- 同时返回 `pid`、`uptime`（alive 时才算，`3964`）、`qq_path`（`_get_qq_path()` `3872`–`3879`，读 `bot_config.json` 的 `qq_path`，用 `utf-8-sig` 解析 `3876`）、`protocol_enabled`（`3966`）。

### 9.5 相关入口

- `POST /api/bot/start`（`3050`–`3053`）、`POST /api/bot/stop`（`3055`–`3058`）直接转发 `start_bot()`/`stop_bot()` 的返回。
- 控制台 `/api/command` 的 `restart` 是 `stop_bot()` → `time.sleep(2)` → `start_bot()`（`3190`–`3195`）；`config_reload` 调 `push_bot_reload()`（`3196`–`3198`）。

---

## 10. 日志流

### 10.1 纯轮询，无流式通道

- **全文没有 SSE / chunked / WebSocket 实现**（无 `text/event-stream`、无 `Transfer-Encoding`，`_send_json` `1245`–`1260` 与 `_send_html` `1262`–`1279` 均一次性写 `Content-Length`）。
- 前端靠定时器轮询：
  - 协议日志面板 `API('/protocol/logs', POST, {"limit":200})` + `setInterval(renderProtoLogs, 1200)`（`webui_artistic_v3.html:829`）。
  - 仪表盘日志 `window._dashLogInterval = setInterval(loadDashLog, 3000)`（`webui_artistic_v3.html:830`），其中 `loadDashLog` 也打 `/protocol/logs`（`webui_artistic_v3.html:792`）。
  - 日志控制页 `loadLogs()` 打 `/protocol/logs`（`webui_artistic_v3.html:835`），并在 `mountChatlogs` 内挂 `setInterval(..., 1000)` 定时刷新（`webui_artistic_v3.html:837` 所在行块）。

### 10.2 内存环形缓冲

- `PROTO_LOG_MAX = 600`（`652`）、`PROTO_LOG = deque(maxlen=PROTO_LOG_MAX)`（`653`）、`PROTO_LOG_LOCK = threading.Lock()`（`654`）、`_LOG_CLEARED` 标志（`655`）。
- `push_proto_log(text)`：加锁 append `{"time": "%H:%M:%S", "text": text}` 并复位 `_LOG_CLEARED = False`（`657`–`661`）。写入来源：驱动状态回调（`673`–`675`）、Bot stdout 读取线程（`898`、`901`）、协议切换/配置保存（`3286`、`3295`、`3386`）、插件钩子错误（`3091`）。
- 清空：`POST /api/protocol/logs/clear` 在锁内 `PROTO_LOG.clear(); _LOG_CLEARED = True`（`3397`–`3403`）；服务启动时也清空并置位（`4021`–`4023`）。

### 10.3 `_collect_logs()` 的回退策略

```python
1026: def _collect_logs(limit=200):
1030:     with PROTO_LOG_LOCK:
1031:         proto = list(PROTO_LOG)
1032:         cleared = _LOG_CLEARED
1033:     if proto:
1034:         return proto[-max(1, min(limit, PROTO_LOG_MAX)):]
1035:     if cleared:
1036:         return []
1037:     return get_chat_logs(limit)
```

- 内存有内容 → 返回末 N 条（N 被钳制到 `[1, 600]`，`1034`）。
- 内存为空且**用户主动清空过** → 返回空，不回退磁盘（`1035`–`1036`，设计意图见 `1027`–`1028` 注释）。
- 否则回退磁盘 `get_chat_logs(limit)`（`1037`）。

### 10.4 磁盘日志：读哪些文件

`get_chat_logs(limit=200)`（`1039`–`1068`）按顺序遍历 **5 个候选文件**（`1042`–`1048`）：

1. `PROJECT/core_log.txt`（`1043`）
2. `PROJECT/qq_bot.log`（`1044`）
3. `~/AppData/Local/EggBloom_v3/qq_bot.log`（`1045`）
4. `~/AppData/Local/Temp/botcore_final.log`（`1046`）
5. `~/AppData/Local/Temp/eggbloom_bot.log`（`1047`）

解析规则（`1052`–`1065`）：
- 含 `[CHAT]` 的行：截取 `[CHAT]` 之后内容按 JSON 解析，成功则整包 append（`1056`–`1061`）——这是聊天记录的结构化来源。
- `len(line) > 8 and line[2] == ':'` → `{"text", "time": line[:8], "direction": "log"}`（`1062`–`1063`）。
- 形如 `[HH:MM:SS]` 的行（多重下标与 `isdigit` 判定 `1064`）→ 取第 10 字符之后作为正文（`1065`）。
- 最终返回 `logs[-limit:]`（`1068`）；整个函数体包在 `try/except Exception: pass`（`1041`、`1066`–`1067`）。

### 10.5 两个日志端点的差异

| 端点 | 行号 | 是否用 `_collect_logs` | 是否受"已清空"影响 |
|---|---|---|---|
| `GET/POST /api/protocol/logs` | `2203`–`2213` / `3392`–`3395` | 是（`2212`、`3394`） | 是 |
| `GET /api/chatlogs` | `1386`–`1389` | **否**，直接 `get_chat_logs(200)`（`1387`） | 否（永远读磁盘） |

- 前端从不调用 `/api/chatlogs`（第 4.13 节），日志控制页走的是 `/api/protocol/logs`。
- `GET /api/protocol/logs` 从**请求体**读 `limit`（`2205`–`2211`），这是 GET 语义异常点，见第 14 节。

---

## 11. 其他能力端点

| 能力 | 落点 | 行号 |
|---|---|---|
| **Token 统计** | `GET /api/token-stats` → `token_stats.snapshot()` + `token_stats.history(50)`；异常时返回 `{"error", "snapshot": {}, "recent": []}` | `2079`–`2086` |
| **记忆总览** | `GET /api/memory`：自学习消息数/群数（`get_self_learning_stats()` `959`–`985`，查 `self_learning_db/self_learning.db` 的 `messages` 表 `973`–`976`）+ 私聊记忆 KB（`get_memory_stats()` `988`–`1001`，查 `super_memory.db`）+ 表达 TOP20（`get_expressions()` `1005`–`1023`）；表达为空时回退**硬编码示例**（`1405`–`1409`） | `1396`–`1412` |
| **记忆列表 / 详情** | `GET /api/memory/list`（按 `scope_id` 聚合 `1422`–`1424`）、`GET /api/memory/detail?qq=`（逐轮 `user_msg`/`bot_reply` `1455`–`1458`） | `1414`–`1435`、`1437`–`1466` |
| **记忆清空（插件侧）** | `POST /api/plugins/action` → `plugin_id=ego, action=memory_clear`：直连 `ego._memory.DB_PATH` 执行三条 `DELETE` | `1744`–`1764`（GET 版）、`3609`–`3629`（POST 版） |
| **表情包列表/标签/上传/删除/导入** | `GET /api/memes`、`GET /api/memes/emotions`、`POST /api/memes/{upload,delete,tags,import}`、`GET /memes/<rel>` | `2243`–`2278`、`2280`–`2308`、`2970`–`3048`、`2311`–`2328` |
| **图片库** | `GET /api/gallery`（`image_cache` + `memes/memes` + AppData，最多 50 条，按 mtime 倒序） | `1391`–`1394`、`1091`–`1112` |
| **群聊工具白名单** | `GET /api/tools`（12 项，含 `group_default`）；配置侧由 `group_tool_allowlist` 控制，空名单即删除该键恢复默认 | `2152`–`2154`、`1071`–`1088`、`2500`–`2502` |
| **表情/情绪表达统计** | `get_expressions()` 读 `expressions` 表，`score = count * 20` | `1005`–`1023`、`1016`–`1019` |
| **生图（ComfyUI）** | `GET /api/imagegen/status`、`POST /api/imagegen/{generate,test,workflow}` | `2215`–`2240`、`2369`–`2478` |
| **本地 LLM 管理** | `POST /api/llama/{test,start,scan,stop,open}` | `2571`–`2848` |
| **Embedding 服务守护** | 非端点：import 期 `_ensure_embed_server()`（`514`–`517`）+ 30s 守护线程（`519`–`521`、`498`–`511`），端口 `8083`（`404`） | `396`–`523` |
| **联系人管理（群/好友）** | **不在本文件**：前端 `gmApi` 指向 `http://127.0.0.1:3005`（`html:2836`），`/api/groups*`（`html:2839` 等）、`/api/friends*`（`html:3010` 等）由 bot_core 提供 | `html:2835`–`2843`、`html:3005`–`3041` |
| **通知测试** | **不在本文件**：`fetch('http://127.0.0.1:3005/api/admin/notify_test', {method:'POST'})`（bot_core） | `html:837` 区块 |
| **通知配置写入** | `POST /api/config` 携带 `notify_admin`（`enabled`/`friend_add`/`group_increase`/`admin_qqs`），落 `bot_config.json`；读取用 `GET /api/config` | `html:836`、`html:837` 区块 → `2485`–`2569`、`2110`–`2150` |
| **底层控制台** | `POST /api/command`（`status`/`restart`/`config_reload`/`help`；`memory_reload`/`self_learn`/`clear_sessions`/`personality_reload` 明确返回"未实现"） | `3178`–`3206`，未实现分支 `3201`–`3202` |
| **Bot 状态卡** | `GET /api/bot/status` | `2156`–`2158` |

---

## 12. 线程模型

### 12.1 服务器线程

- `ThreadingMixIn + HTTPServer`，`daemon_threads = True`（`3989`–`3990`）→ **每个 HTTP 请求一个独立线程**，主线程只跑 `serve_forever()`（`4028`）。
- `BaseHTTPRequestHandler.log_message` 被覆写为空（`1221`–`1222`）→ **不打印访问日志**（鉴权/审计角度值得注意）。

### 12.2 后台常驻线程（均为 daemon）

| 线程 | 启动点 | 周期/触发 | 做什么 |
|---|---|---|---|
| 状态刷新 | `940`–`941` | `while True` + `sleep(1)`（`937`） | `fetch_from_bot_core("/api/status")`（`910`）合并进 `S`，再组装 `_STATUS_CACHE`（`913`–`934`） |
| Embedding 守护 | `519`–`521` | `sleep(30)`（`501`） | 探测 8083，掉线则 `_ensure_embed_server()`（`498`–`511`） |
| Bot stdout 读取 | `892`–`902`（由 `start_bot` 触发 `3927`） | 阻塞在 `readline` | 把子进程 stdout 每行写入 `PROTO_LOG`（`895`–`898`），退出时写"进程已退出"（`901`） |
| 打开站点 | `3328` | 一次性 | `threading.Thread(target=lambda: _wb.open(url), daemon=True)` |
| 插件自主 Agent | 无独立线程 | 请求线程内同步 | `web.autonomous` 在请求线程里跑 `wo.execute_task`（`3817`–`3824`） |

### 12.3 锁

| 锁 | 定义 | 保护对象 | 使用点 |
|---|---|---|---|
| `STATE_LOCK` | `583` | 全局 `S` 状态 | `667`、`719`、`913`、`2073`、`3092`、`3288`、`3923`、`3937`、`3949`、`3960` |
| `PROTO_LOG_LOCK` | `654` | `PROTO_LOG` deque + `_LOG_CLEARED` | `659`、`1030`、`3399`、`4021` |
| `_bot_lock` | `733` | `_bot_process` | `3898`、`3934`、`3956` |

- **未加锁的共享可变状态**：
  - `_STATUS_CACHE` 是普通 dict，后台线程写（`933`–`934`）、请求线程读（`1356`）——无锁（GIL 下不至于崩，但无一致性保证）。
  - `_meme_cache`（`1142`）在请求线程间读写，无锁（`1155`–`1164`）。
  - `_html_mtime` / `HTML` 全局（`1139`、`1208`）可被并发请求同时触发重载（`1307`）。
  - `_embed_proc`（`405`、`427`、`438`–`439`、`485`）无锁。
  - `_LOG_CLEARED` 有 `global` 声明分散在三处（`655`、`1029`、`3398`、`4008`）。

### 12.4 阻塞性操作（会长时间占用请求线程 / 客户端会看到挂起）

| 操作 | 最长阻塞 | 行号 |
|---|---|---|
| `POST /api/llama/start`：拉起 + 每秒轮询 `/health` 最多 **120 次** | ~120 s | `2718`–`2729`（`sleep(1)` `2719`） |
| `POST /api/llama/test`（llama-server 分支）：最多 **90 次** | ~90 s | `2632`–`2647`（`sleep(1)` `2633`） |
| `POST /api/providers/models`：逐个候选 URL，custom 6s、其余 15s | ~30 s（2 个候选） | `2911`、`2913`–`2938`（`urlopen(timeout=per_timeout)` `2918`） |
| `POST /api/llm/test` | 20 s | `3163` |
| `POST /api/imagegen/generate`：`wait_and_get_images(timeout=comfyui_timeout)` | 默认 120 s | `2428`（超时值 `2383`） |
| `GET /api/imagegen/status` / `POST /api/imagegen/test`：探 ComfyUI | 3 s / 自定义 | `2222`、`2453` |
| `POST /api/protocol/detect`：全盘（各盘符根 + 用户目录）深度 ≤3 遍历 | 取决于磁盘 | `94`–`185`（调用点 `3302`） |
| `POST /api/protocol/auto-detect`：上述 + 3 次端口探测（各 0.4s） | 数秒~数十秒 | `229`–`287`（`243`–`246`） |
| `POST /api/llama/scan`：盘符根 + 常见目录遍历 + 递归 | 取决于磁盘 | `2746`–`2817` |
| `POST /api/bot/stop`：`wait(timeout=5)` | 5 s | `3943` |
| `POST /api/command` 的 `restart`：`stop` + `sleep(2)` + `start` | ≥2 s（不含 stop 的 5 s） | `3190`–`3195` |
| 各记忆/统计 SQLite 查询 | 通常极短 | `971`–`977`、`995`–`997`、`1014`–`1018`、`1421`–`1425`、`1454`–`1459`、`1754`–`1758`、`3619`–`3624` |

- 后端到 bot_core 的请求超时都很短：`fetch_from_bot_core` 0.5 s（`625`）、`push_bot_reload` 2 s（`642`）、`_is_bot_core_alive` 0.15 s（`611`），并有 5 s 结果缓存 `_BOT_CORE_CACHE`（`603`）。
- `_Cache` 是手写 TTL 缓存（`588`–`599`），实例：`_BOT_CORE_CACHE`(5s, `603`)、`_SYS_CACHE`(2s, `649`)、`_SL_CACHE`(30s, `957`)、`_EXPR_CACHE`(30s, `1003`)。`get()` 过期返回 `None`（`593`–`596`）——因此缓存值本身不能是 `None`。

---

## 13. 值得注意的设计

1. **无框架、单文件、单长链分发**：整个后端只有 `Handler.do_GET` / `Handler.do_POST` 两条 `if path == ...` 长链（`1296`、`2333`），没有任何路由注册表；新增端点必须同时改这两处（`/api/plugins/action` 就是两处各写一遍的典型，`1512` 与 `3405`）。
2. **前端零构建、后端零依赖**：单文件 SPA 按 mtime 热重载（`1133`、`1201`–`1214`、`1306`–`1308`）；服务端只用标准库 `json/os/sys/time/threading/sqlite3/subprocess/socket/urllib`（`11`–`18`）。
3. **三级插件契约，保持向后兼容**：`PLUGIN_ACTIONS` → `get_config/set_config/toggle` → `execute_action`（`526`–`579`），且"未声明标准接口就返回 `None` 交回硬编码分支"（`533`–`534`、`543`–`545`）——这是一个刻意设计的渐进式迁移通道。
4. **Bot 日志走内存环形缓冲而非文件**：`_start_bot_log_reader` 把子进程 stdout 直接推进 600 条上限的 `PROTO_LOG`（`652`–`653`、`892`–`902`），配合"本次会话已清空则不再回落磁盘"的 `_LOG_CLEARED` 语义（`1027`–`1028`、`1035`–`1036`、`4021`–`4023`），保证面板显示的永远是"本次运行"的日志。
5. **凭据与配置彻底分离**：`bot_config.json` 永不落明文 Key（`946`、`2562`），Key 统一进 Windows 凭据管理器（`2510`–`2561`、`config.py:424`–`426`），回传给前端一律掩码（`84`–`91`、`2135`–`2148`），并用 `«redacted:...»` 占位符识别"这是掩码不是真 Key"（`72`–`81`）——形成"读掩码 → 写回占位符不覆盖真值"的闭环（`2530`–`2538`）。
6. **多驱动协议层 + 泛化路径探测**：不硬编码任何安装路径，靠"目录名模式 + 可执行文件名"双通道在盘符根/桌面/下载里深度 ≤3 扫描（`94`–`185`），再由端口占用情况判断"谁在跑"（`229`–`287`），并给出排序推荐（`278`、`285`–`286`）。
7. **启动前只读自检**：`_protocol_check_items` 要求文件型配置实际存在（`320`–`323`、`327`–`331`），还额外体检"驱动在跑但事件桥未连"这一最容易被误判为"连上了却没反应"的场景（`355`–`390`）。
8. **弹性的状态供给**：`/api/status` 优先返回后台线程每秒维护的 `_STATUS_CACHE`（`1356`–`1358`），避免每次请求都去接 bot_core（`908`–`937`）；`_BOT_CORE_CACHE` 用 5 s TTL 把存活探测的成本摊薄（`603`–`617`）。
9. **端口顺延 + 落盘发现**：`pick_port` 顺延 20 个端口（`4001`–`4005`），并把实际端口写 `last_port.txt`（`4017`）供 `main_window.py:56`–`66` 读取。
10. **默认安全姿态**：默认只绑 `127.0.0.1`（`4007`）+ Host 白名单（`1239`–`1243`）+ Origin 白名单（`1224`–`1237`）+ 静态资源扩展名白名单（`67`–`69`）+ POST CSRF 校验（`2341`–`2352`），并明确注释了此前 `HERE / path.lstrip("/")` 的目录穿越漏洞（`1328`–`1329`）。
11. **风险文案与二次确认被当成产品功能**：`PROTO_RISK_INFO`（`213`–`226`）在状态接口、自动识别、切换确认三处复用（`2190`、`280`–`283`、`3270`–`3280`），对"非官方登录通道会触发腾讯风控"做显式提示而非隐藏。
12. **写入侧的业务保护**：`group_idle_exit_min_silence` 钳到 ≥30（`2504`–`2509`），空 `group_tool_allowlist` 直接删键回默认（`2500`–`2502`），删除 provider 时连带清凭据（`2541`–`2549`）。
13. **Embedding 服务自愈**：import 期即拉起（`514`–`517`），30 s 守护重启（`498`–`511`），并把非 ASCII 路径镜像到 llama-server 同目录以规避原生程序读不了中文路径（`445`–`464`，注释 `446`–`450`）——一个非常具体的 Windows/中文路径工程妥协。
14. **`psutil` 缺失时静默造假数据**：`get_system_stats` 在 `ImportError` 时返回 `random.uniform` 的 CPU/内存（`1122`–`1124`），面板看不出是真数据还是随机数。

---

## 14. 风险与坏味道

### 14.1 路径处理隐患

| # | 问题 | 行号 | 说明 |
|---|---|---|---|
| R1 | **`/memes/<rel>` 未做目录包含性校验，可穿越读取 meme 目录之外的任意文件** | `2311`–`2327`、`1148`–`1174` | `rel` 仅做反斜杠替换（`2314`）、`unquote`（`2317`）、latin1→utf8（`2324`），**没有 `resolve()`/前缀校验**；`_serve_meme` 直接 `meme_dir / rel` 后 `.is_file()`（`1151`–`1152`）。静态分支（`1327`–`1335`）虽会拦住 `../`，但它不 `return`（落在 `1335` 判定失败后继续往下走），控制流仍会到达 `2311` 分支。属代码层可确证的缺陷；实际可利用性未做运行时验证 |
| R2 | **插件 UI 路径拼接未清洗 `plugin_id`** | `1479`、`1501` | `PROJECT / "plugins" / plugin_id / "ui.html"` 直接用 query 值，无 `resolve()` 包含性检查、无字符白名单，`plugin_id=../../..` 类输入可越出 `plugins/`（受"文件名必须是 `ui.html`"限制） |
| R3 | `/api/memes/tags` 未做名称校验 | `3016`–`3028` | 与 `upload`/`delete` 不同（`2975`–`2977`、`2999`–`3001` 都有 basename + 扩展名白名单），`tags` 直接 `meme_dir / name` 后 `write_text`（`3021`–`3024`）→ 可写 meme 目录外路径 |
| R4 | `/api/protocol/fs` 任意目录列举 | `3341`–`3365` | 入参 `path` 无白名单，默认 `C:\`（`3344`）；配合零鉴权即可枚举全盘目录 |
| R5 | `/api/qq-path` 直写配置文件 | `3208`–`3224` | 绕开 `save_config`（对比 `944`–`952`）：自己 load 整份 json（`3213`–`3216`）改键后整份 `open(...,'w')`（`3219`），会丢弃内存中未落盘的其它改动，且无 AppData 回退 |
| R6 | `last_port_dir` 死变量 | `4015`–`4017` | 创建了 `PROJECT/webui` 目录变量却用它之外的目标写文件 |
| R7 | `/api/llama/scan` 全盘扫描 | `2746`–`2817` | 未传 `root` 时遍历所有盘符根 + 用户目录 + Program Files（`2759`–`2770`），并有 400/60/10 三处上限（`2791`–`2794`、`2815`–`2816`）——成本高且信息暴露面大 |

### 14.2 鉴权与 CSRF 缺口

| # | 问题 | 行号 | 说明 |
|---|---|---|---|
| R8 | **全服务零身份认证** | 全文件 | 没有任何 token/session/密码校验；唯一的屏障是 Host 头（`1239`–`1243`）、Origin（仅 POST，`2341`–`2352`）与默认 127.0.0.1 绑定（`4007`）。任何本机进程都可调用全部端点 |
| R9 | **`GET /api/plugins/action` 可改变状态且无 CSRF 校验** | `1512`、`1694`–`1896` | `do_GET` 不做 Origin 检查（`1296`–`1301` 只有 Host 检查），而该端点支持 `ego` 的 `set_config`（`1714`–`1718`）、`memory_clear`（`1744`–`1764`）、`reload`（`1811`–`1826`）、`slang_clear_injected`（`1765`–`1776`）等写操作。恶意页面用 `<img src="http://127.0.0.1:8080/api/plugins/action?...">` 类请求可触发副作用（Host 校验在浏览器场景下仍会带 `127.0.0.1`，因此该防护对此无效） |
| R10 | 两份 Origin 判定逻辑不一致 | `1224`–`1237` vs `2341`–`2352` | 前者放行 `Origin: null`（`1233`），后者把 `null` 视为非法（`2348`）；同一语义两处实现 |
| R11 | `Host` 为空即放行 | `1243` | `bare in (..., "")`，缺失 Host 头的原始请求绕过 DNS rebinding 防护 |
| R12 | 无访问日志 | `1221`–`1222` | `log_message` 被置空，全部请求不留痕（安全隐患与排障困难） |
| R13 | `/api/config` POST 无字段白名单 | `2487`、`2551` | 除 `api_key`/`api_key_clear` 外任意键都会并入 `cfg` 并整份写入 `bot_config.json`（`2563`） |

### 14.3 重复代码

| # | 问题 | 行号 |
|---|---|---|
| R14 | `do_GET` 与 `do_POST` 各有一份近 480 行的 `/api/plugins/action` 实现 | `1512`–`1986` vs `3405`–`3863`（`slang_learner` 分支 `1528`–`1663` vs `3423`–`3528`；`ego` 分支 `1694`–`1896` vs `3559`–`3774`；`web_operator` `1936`–`1979` vs `3775`–`3829`；通用兜底 `1897`–`1985` vs `3830`–`3862`） |
| R15 | 两份实现行为已经漂移 | POST 版独有 `slang_inject_all`/`slang_reject_all`（`3743`–`3757`）、`slang_learner.clear_injected`（`3517`–`3523`）、`web_operator.chain_run`（`3814`–`3816`）、`web.autonomous`（`3817`–`3824`）、`ego` 的 `slang_inject_all`/`slang_reject_all`（`3743`–`3757` 邻域 `3753`–`3757`）与 `slang_stats` 包装差异（`1735`–`1737` 的 `memory_persona` 相同但 `get_examples` 传参不同：GET `1689` 传 `params`、POST `3590` 传 `params`） |
| R16 | `/api/plugins/page` 与 `/page` 两份逐行等价实现 | `1468`–`1488` vs `1490`–`1510` |
| R17 | `/` 路由的 `try` 与 `except` 里重复同一段逻辑 | `1305`–`1310` vs `1312`–`1315` |
| R18 | `/api/llama/test` 与 `/api/llama/start` 的 llama-server 启动+等待代码重复 | `2596`–`2660` vs `2678`–`2741` |
| R19 | `save_config` 与 `config.save_config` 两套实现 | `944`–`952` vs `config.py:444`–`470` |
| R20 | `/api/protocol/status` 与 `/api/protocol/config` 的配置读取重复 | `2168`–`2178` vs `2195`–`2200` |
| R21 | 前端有两份 `loadPlugins` | `webui_artistic_v3.html:129012` 偏移处 与 `webui_artistic_v3.html:344447` 偏移处 |

### 14.4 裸 except / 静默失败

- `except Exception: pass`（或等价）密集出现，抽样：`37`–`38`、`181`–`182`、`200`–`205`(否)、`248`–`249`、`294`–`296`、`414`–`415`、`420`–`421`、`435`–`436`、`463`–`464`、`493`–`495`(有 print)、`510`–`511`、`522`–`523`、`538`–`539`、`555`–`556`、`614`–`615`、`627`–`628`、`644`–`645`、`676`–`677`、`816`–`817`、`833`–`834`、`839`–`840`、`899`–`900`、`935`–`936`、`951`–`952`、`982`–`983`、`1000`–`1001`、`1020`–`1021`、`1066`–`1067`、`1110`–`1111`、`1197`–`1198`、`1213`–`1214`、`1235`–`1236`、`1259`–`1260`、`1278`–`1279`、`1740`–`1741`、`1792`–`1793`、`1901`–`1902`、`1935`、`2036`–`2037`、`2050`–`2051`、`2071`–`2072`、`2269`–`2271`、`2318`–`2319`、`2325`–`2326`、`2358`–`2359`、`2366`–`2367`、`2498`–`2499`、`2560`–`2561`、`2845`–`2846`、`2957`–`2958`、`3974`–`3975`、`3982`–`3983`、`4018`–`4019`、`4030`。
- **真正的裸 `except:`（无异常类型）**：`2294`（`except: pass`，解析 meme sidecar JSON）与 `2306`（`except: pass`，解析 `tags.json`）——连 `Exception` 都不写，会吞掉 `KeyboardInterrupt`/`SystemExit`。
- 后果示例：插件 `set_config` 失败（`1678`–`1682`、`1714`–`1718`）在很多路径下仍返回 `{"ok": True, ...}`；`save_plugin_state` 失败静默（`3982`–`3983`）但 `toggle` 仍回 `success: True`（`3099`）。
- `_STATUS_CACHE` 刷新线程整体包在 `except Exception: pass`（`935`–`936`）——异常时静默保留上一次数据，面板会显示陈旧值。

### 14.5 其它坏味道

| # | 问题 | 行号 |
|---|---|---|
| R22 | 巨型函数：`do_GET` 约 1035 行（`1296`–`2331`）、`do_POST` 约 1533 行（`2333`–`3866`），单个 `Handler` 类约 2646 行（`1220`–`3866`） |
| R23 | 代码注释自陈曾因子函数内 import 触发 `UnboundLocalError`（注释 `3155`–`3157`），说明函数过大已导致作用域事故 |
| R24 | `GET /api/protocol/logs` 从**请求体**读参数（`2205`–`2211`）——GET 带 body 语义错误；客户端谎报 `Content-Length` 可让请求线程阻塞在 `rfile.read` |
| R25 | 无条件写调试日志文件 `HERE/model-debug.log`（`1899`–`1902`、`2356`–`2359`、`2857`–`2860`、`3412`–`3415`、`3858`–`3861`）——本机该文件已 553,902 字节，生产环境只增不减；另有 `llama-health-debug.log`（`2642`–`2645`）、`EOF` |
| R26 | `_html_mtime` 初值 `0`（`1139`）导致首次 `/` 请求必然重复读盘（`1307`–`1308`） |
| R27 | `CONFIG_CACHE` 只写不读（定义 `954`；`.update` 于 `2566`、`2956`、`3127`、`3220`；无任何读取）——死缓存，误导读者 |
| R28 | 端口顺延后端到端断裂：服务端用 `pick_port` 顺延（`4001`–`4005`、`4010`），但 Electron 前端硬编码 `8080`（`main.js:143`、`main.js:169`、`main.js:173`、`main.js:204`）——一旦 8080 被占用，窗口会持续白屏重试（`main.js:171`–`175` 只重试 8080） |
| R29 | `_is_redacted_key` 覆盖面窄：只识别 `«redacted` 前缀与 `****` 后缀（`77`–`80`），其它掩码形态会被当作明文写入凭据管理器（`2530`–`2531`、`2552`–`2558`） |
| R30 | `psutil` 缺失时返回随机 CPU/内存（`1122`–`1124`）——静默假数据 |
| R31 | 硬编码本机绝对路径 `Path(r"D:\llama-b10068\llama-server.exe")`（`403`），作为 `_EMBED_BUILTIN_EXE` 兜底 |
| R32 | 用 `exec` 解析插件元数据（`2031`–`2032`，`ns = {"__builtins__": {}, "enabled": False}` 仅屏蔽内建、未做 AST 白名单） |
| R33 | 用 `'adapter' in content_py` 全文子串判断适配壳（`2048`–`2049`）——注释、字符串里出现 `adapter` 即被误判并从插件列表隐藏（`2055`–`2056`） |
| R34 | `S._protocol_manager` 未在 `State.__init__` 声明（`680`–`697`），全库靠 `getattr` 兜（`2161`、`3240`、`3281`）；`S.protocol_running` 由回调惰性创建（`669`） |
| R35 | `stop`（`3239`–`3249`）与 `switch`（`3287`）对 `S._protocol_manager` 的清理策略不一致（前者保留、后者置空） |
| R36 | `_meme_cache` 超限时整体 `clear()`（`1163`–`1164`）而非 LRU 淘汰；上限 `1200`（`1143`） |
| R37 | `/api/memes/import` 无数量/体积上限，整树复制用户指定目录（`3036`–`3044`） |
| R38 | 前端从不调用的死端点：`GET /api/chatlogs`（`1386`）、`GET /api/plugins/page`（`1468`） |
| R39 | `/desktop` 路由在本机必然失败（`1318`–`1323`，`resources/app` 下无 `desktop-ui` 目录） |
| R40 | 插件 UI 的 iframe 无 `sandbox` 属性（`webui_artistic_v3.html:841`、`html:3804`），插件页与主页面同源，可直接调用全部 API |
| R41 | `main.js` 用 `contextIsolation: false` + `nodeIntegration: true`（`main.js:164`–`165`），且把远端 HTTP 页面（`http://localhost:8080`）加载进启用 Node 集成的窗口（`main.js:169`）——渲染进程被注入即可拿到 Node 能力 |
| R42 | `_collect_logs` 的 limit 语义在内存命中与磁盘回退两条路径上不同（`1034` 返回 `[-limit:]`，`1037` 交给 `get_chat_logs` 内部再 `[-limit:]` `1068`），且内存路径把 limit 钳到 `≥1` 而磁盘路径未钳 |
| R43 | `/api/status` 的 `bot_uptime` 在缓存路径（`1357` 直接返回 `_STATUS_CACHE`）下是上次刷新的快照值，而后台线程每秒刷新（`937`）——精度依赖线程存活 |

---

## 15. 未能确认 / 边界声明

以下内容**未获确认**，本文档不做断言：

1. **所有运行时行为**：本次核查为纯静态阅读，**未启动 `webui_server.py`、未发送任何 HTTP 请求**（遵从任务纪律）。端口顺延的实际触发、R1/R2/R3 三个路径穿越缺陷的实际可利用性、以及 `pick_port` 耗尽后的表现，均属"代码层可推出、运行时未验证"。
2. **`protocols/` 包内各驱动适配器的实现细节**：本文仅依据 `protocol_manager.py` 的门面转发（`protocol_manager.py:36`–`106`）与 `webui_server.py` 的调用点推断交互（如 `exe_path`/`qq_path`/`api_base_url`/`ws_port`/`report_url`/`_napcat_dir`/`runtime_dir`/`bridge_state`/`SYNC_ONEBOT_URL`/`ensure_effective_url` 的存在性与语义来自引用点 `325`–`351`、`365`、`protocol_manager.py:80`–`83`），**未通读** `protocols/` 源码。
3. **bot_core 的 `3005` 端口端点集**：`/api/status`、`/api/plugins`、`/reload_config`（`910`、`1989`、`637`）、以及前端的 `/api/groups*`、`/api/friends*`、`/api/admin/notify_test`（`html:2836`、`html:837`）均在 `bot_core.py`（375 KB）中，超出本次范围，未核查。
4. **`webui_artistic_v3.html` 未通读**（按任务要求仅 grep 定位调用点）。因此"前端不调用某端点"的结论仅覆盖 `API('...')`、`fetch('/api/...')`、`fetch('http://127.0.0.1:...')` 这三种字面量形态；通过变量拼接构造的 URL 无法排除。第 4.13 节中引用字节偏移的调用点（如 `loadMemory`、`loadGallery`、`loadPlugins` 第二份）因源文件为超长单行，未能给出精确行号，仅给出偏移量与所在函数名。
5. **`constrast`/打包产物的实际目录布局**：`webui_server.py:42`–`45` 的 frozen 分支与 `config.py:18`–`21` 的 `SCRIPT_DIR` 在打包后是否始终一致，未在打包产物上验证。
6. **`.env` 实际内容**：`resources/app/.env`（75 B）未读取，因此"env 补缺"（`config.py:412`–`422`）的实际影响范围未评估。
7. **`providers.json`**：仅确认其被用作 `/api/config` GET 的回退数据源（`2121`–`2123`）与 `/api/providers/models` 的本地模型目录来源（`2871`–`2880`），未逐条核对内容。
8. 第 14.1 节 R1/R3 的"可利用性"判断基于控制流阅读；`urlparse(self.path).path`（`1302`）与 `BaseHTTPRequestHandler` 对请求行的处理是否会对 `..` 做归一化，未实测。

---

*文档生成方式：全文静态阅读 + 交叉 grep 验证，未修改任何被核查文件（仅新建本文件）。*
