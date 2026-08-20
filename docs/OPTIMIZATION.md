# EggBloom 优化清单

基于代码实测整理的可执行优化项，按性价比排序。每个项目标注问题、位置、改动量、收益、风险。

- P0：收益明确、改动小、风险低，建议优先
- P1：收益中等或改动较大，按需处理
- P2：架构级，投入大，长期演进才值得

## P0 性能类

### 1. super_memory 事实/事件 embedding 双重计算
- **问题**：`_upsert_fact` 和 `_store_events` 中 `if _embedder.embed(x): emb = json.dumps(_embedder.embed(x))`，同一文本推理两次。DLL 路径持全局锁 `_DLL_LOCK` 串行化，每次本地推理约 46ms，会造成一次多余阻塞。
- **位置**：`plugins/ego/super_memory/__init__.py:429-430`、`:506-507`
- **改动**：先算一次 `emb = _embedder.embed(x)`，非空再 `json.dumps`。约 4 行。
- **收益**：消除每次事实/事件入库的重复推理（~46ms），对高频消息群聊有实际累积收益。
- **风险**：无。本地模型，不涉及 token 成本与正确性。

### 2. 私聊历史摘要缓存无 TTL
- **问题**：`_HISTORY_CACHE`/`_HISTORY_CACHE_FP`/`_HISTORY_CACHE_OLD` 的 `p_` 前缀条目仅在 chat_history 已清空时回收，活跃 QQ 的缓存会一直驻留。历史摘要由 LLM 生成（成本）且可能过期（内容陈旧）。
- **位置**：`bot_core.py:7552-7556`（已有回收逻辑，缺 TTL）
- **改动**：在回收循环中给 `p_` 条目加时间戳 TTL（如 24h），与群缓存 `_ctx_ttl` 对齐。约 5 行。
- **收益**：避免私聊摘要无限驻留，降低陈旧摘要注入旧信息的概率。
- **风险**：低。需验证 LLM 摘要重新生成是否正常。

### 3. LLM 主链路 urllib 每次新建连接
- **问题**：`_call_deepseek_sync` 及多个 API 调用每次 `urllib.request.urlopen` 新建 TCP 连接，无连接池，高频群聊下握手开销重复。
- **位置**：`bot_core.py:5447-5463`（`_call_deepseek_sync`、流式收尾）、`:5823`、`:5896`
- **改动**：改用 `requests.Session`（复用 TCP + HTTP 长连接），或 `http.client.HTTPConnection` 保持。配置中 api_call 已用 Session，但 LLM 调用仍是 urllib。约 20 行。
- **收益**：减少每次 LLM 调用的握手/排队延迟，高并发场景提升明显。
- **风险**：中。需要保持超时、代理、上下文 `CTX`（SSL）行为一致。

## P0 代码/架构类

### 4. bot_core.py 单文件过大
- **问题**：7566 行 / 38 万字符，画图、搜索、远程控制、群画像、工具调用等域集中在一个文件，修改冲突和回归风险高。
- **位置**：`bot_core.py`（全文件）
- **改动**：按域拆模块，如 `image_services.py`（画图）、`remote_control.py`（截屏/音量/电源/进程）、`web_services.py`（搜索/浏览器）、`tools.py`（function calling 定义）。函数签名不动，只移动。
- **收益**：可维护性、可测试性、并行开发。
- **风险**：高。涉及大量全局变量和 import 顺序，需回归测试。

### 5. webui_server.py 手写 if/elif 路由链
- **问题**：3969 行单文件，`do_GET`/`do_POST` 长 if/elif 链，新增路由需在多个分支重复维护。
- **位置**：`webui/webui_server.py`
- **改动**：抽路由表 `ROUTES = {"/api/groups": handler_fn}`，按 GET/POST 分发。约 100 行重构。
- **收益**：路由可注册、可测试、可加鉴权中间件。
- **风险**：中。需保持现有所有端点行为兼容。

## P1 一致性/资源

### 6. 多把全局锁 + 状态 dict 靠人工维护
- **问题**：6 把锁 + 多个全局 dict（chat_history/GROUP_SESSION/_HISTORY_CACHE 等），跨线程一致性依赖人工维护，演进易引入竞态。
- **位置**：`bot_core.py`（全局区）
- **改动**：可选引入 `threading.RLock` 统一入口，或把相关状态收敛到单一 `AppState` 类。
- **收益**：降低竞态/状态丢失风险。
- **风险**：中。属于架构级改动。

### 7. 轮询与推送双通道冗余
- **问题**：`get_new_messages` 轮询 + HTTP 事件推送并存，水位线去重逻辑复杂（重启基线/重复消息）。
- **位置**：`bot_core.py:2059`
- **改动**：以推送为主通道，轮询仅作为推送不可用时的兜底（如 30s 间隔而非 3s）。
- **收益**：减少 QQ 侧请求量，简化去重逻辑。
- **风险**：中。需要确认所有协议驱动都稳定推送。

### 8. ConversationState 每轮 2 次磁盘 IO
- **问题**：`ConversationState.__init__` 每轮 `_load()` 读 JSON，`save` 写 JSON，一次对话 2 次磁盘 IO。
- **位置**：`plugins/ego/personality_engine/__init__.py:181-210`、save 方法
- **改动**：进程内 LRU 缓存 + 定时/退出落盘，或按会话合并读写。
- **收益**：高频群聊下减少每轮 IO。
- **风险**：低。注意崩溃丢状态（已有 `save_state` 兜底节奏可参考）。

## P2 数据/查询

### 9. self_learning 每轮 3 次 SQL + 新连接
- **问题**：每轮对话多次 SQLite 查询且每次新建连接，非 WAL 模式。
- **位置**：`self_learning.py`、`bot_core.py:6505`
- **改动**：复用单连接 + `PRAGMA journal_mode=WAL`。
- **收益**：减少连接开销，并发读不受写阻塞。
- **风险**：低。

### 10. slang_injector "高频"实为字典序前 5
- **问题**：黑话注入时选前 5 条按字典序，非真正高频。
- **位置**：`plugins/ego/slang_learner/__init__.py`
- **改动**：按实际使用次数排序取 top-N。
- **收益**：黑话注入更贴合实际聊天。
- **风险**：低。

### 11. super_memory 召回无内存缓存
- **问题**：每次召回 100+100 条相似度重算，未缓存。
- **位置**：`plugins/ego/super_memory/__init__.py`
- **改动**：对高频 query 加短期 LRU（如 5 分钟）。
- **收益**：减少重复向量计算。
- **风险**：低。注意记忆更新后缓存失效。

## 文档类

### 12. `_KNOWN_NAMES` 人名硬编码
- **问题**：`bot_core.py:5942` 写死名单，改配置需改代码。
- **改动**：移到 config。
- **风险**：低。改动小。

### 13. 调试残留日志、魔法数字
- **问题**：`[调试]` 日志散落、硬编码文案与数字。
- **改动**：移入 logging 分级 + 常量提取。低优先级，属于清理。

---

## 建议实施顺序

1. **P0-1 embedding 双重计算**（最小、零风险）
2. **P0-2 私聊缓存 TTL**（小改动）
3. **P0-3 连接池**（中等，收益在高并发）
4. P1/P2 项按实际运行瓶颈再决定

每项改动前后都应跑 `tests/test_bot_core_events.py` 做回归。