# 记忆的唯一读写口（P0-3）

> FIXPLAN-1.5.11 §4：一个读口 `recall()`，一个写口 `remember()`，预算与排序一处算。
> 实现：`harness/memory_api.py`。

## 为什么收口

1.5.10 之前，`bot_core` 里有**五处**各自去读记忆：`people.card`、`people_alias`、
`_cross_scene_block`、`get_memory_prompt_block`（私聊）、`build_people_block` +
`memory_context.build_block`（群聊）。后果只有一个：没人说得清这一轮 prompt 里的
记忆从哪来、为什么是这几条、总预算多少。

现在：

```
bot_core ──recall(uid, …)──▶ harness/memory_api ──▶ people / sandglass / memory_context
                          └─▶ 一行 [记忆] 日志
```

## 读口

```python
recall(uid, *, scene="group", group_id="", query="", private=False,
       budget=900, cfg=None) -> str
```

装填顺序（也是优先级，预算不够先丢后面的）：

| # | 段 | 来源 | 边界 |
|---|---|---|---|
| 1 | 身份 | `people.card(uid, gid, private=…)` | 私聊才带 `scope=private` 的事实 |
| 2 | 称呼 | `people_alias.prompt_line(uid)` | 私聊/群聊共用同一份 |
| 3 | 跨场景 | `sandglass_bridge.cross_scene_recent` | 私聊取群里的；群聊**只取本人私聊过的** |
| 4 | 检索 | `memory_context.build_block`：私聊 `scope="private"` + `sources=("sand","sm")`；群聊 `scope="group"` + `sources=("sand",)`（默认） | 群聊默认只取沙漏源（super_memory 群侧由 EGO 注入，同源=双注）；`group_memory_vector_enable` 打开后群聊也带 `sm` |
| 5 | 群画像 | `build_people_block(gid)` | 只按群取，不含私聊内容 |

日志（每轮最多两行，没注入就不打）：

```
[记忆] uid=1000000005 scene=group 注入 4 段/532 字（身份 92 / 称呼 18 / 跨场景 120 / 检索 302）
[记忆] 超预算丢弃：群画像
```

## 写口

```python
remember(kind, *, uid="", text="", scene="", group_id="", meta=None) -> bool
```

| kind | 落点 | meta |
|---|---|---|
| `user_msg` | 沙漏 `log_user_message` | `{"nick": 昵称}` |
| `bot_msg` | 沙漏 `log_bot_reply` | — |
| `fact` | `people.add_fact` | `{"key": 类别, "scope": "global"/"private"}` |
| `event` | `plugins.ego.super_memory.add_action_event` → `events` 表（按 `user_id` / `scope_id` 隔离）；落库失败返回 `False`（不装作成功） | `{"scope","scope_id","user_id","event_type","importance"}` |

## 向量源（super_memory）的边界与降级

这一节只描述**现状**，不改实现；对应代码 `plugins/ego/super_memory/__init__.py`
与 `harness/memory_context.py`。

### 群聊开关（默认关）

`harness/memory_api.py` 的 `_retrieval()`：

| 场景 | sources（默认） | 开关打开后 |
|---|---|---|
| 私聊 | `("sand","sm")` | 不变 |
| 群聊 | `("sand",)` | `("sand","sm")` |

开关 `group_memory_vector_enable`（走 `config.load_config().get(..., False)`）
或环境变量 `EGGBLOOM_GROUP_MEMORY_VECTOR`，**默认 `False`**。理由：

1. **不改线上行为**：关着时群聊逐字节保持现状（只取沙漏）；
2. **避 EGO 双注**：super_memory 群侧的【核心记忆】已由 EGO `before_reply` 注入，
   builder 再取同源会双注（见 `memory_context.collect` 的注释）；
3. 开关只切 `sources`，不动 `scope=("group", gid)`、不动 builder、不动注入顺序 ——
   打开后也走**同一个** builder，不会串私聊。

配置键没有写进 `config.py` 的 `DEFAULT_CONFIG`（本轮不改 config.py）：靠 `.get`
的默认值 `False` 兜底，需要打开时在 `bot_config.json` 里显式写 `true`。

### `messages`（沙漏情景层）本轮不补向量

- 当前**有向量**的只有 super_memory 的 `facts` / `events` 两张表（`embedding` 列，
  值为 `json.dumps(float_list)`，另有 `embed_sig` 记口径 `引擎:模型:维度`；
  `reembed_all()` 负责按当前口径重算）。
- `messages` 表（`after_reply` 写的 `user_msg + bot_reply` 原文）**没有 `embedding` 列**；
  沙漏情景层走 `sandglass_bridge.search_scored_context` 的**融合分**，也不是向量。
- 群聊开关打开的 `"sm"` 只覆盖 `facts`/`events` 的语义召回。**本轮不给 `messages` /
  沙漏情景层补向量**：不加列、不跑 `reembed_all()`、不改 `search_scored_context`。

边界理由：① EGO 钩子已经注入了 `messages` 那一路的【核心记忆】，再给情景层补一份
向量召回会进一步重叠（双注）；② 全文逐条 embedding 的成本随对话量线性增长，而情景层
本来靠「最近原文窗口 + 融合分」覆盖，语义检索边际收益最低；③ 本轮只做「把已有向量源
接进群聊主读口」，不是重建存储层。真要给情景层做语义召回，是单独的存储改造（建列 +
回填 + ANN 索引），**本轮不做，也不承诺可用**。

### 全表扫描算余弦的量级上限（无 ANN 索引）

`SuperMemory.recall_key_memories()` 的取数路径：

1. `use_vector_recall=True` 且有 query → `_embedder.embed(query)` 生成查询向量 `qvec`
   （进程内 LiGGUF DLL 优先，失败降级 llama-server HTTP `127.0.0.1:8083`）；
2. SQL 按 `scope_id` 捞候选：`facts` `confidence>0.6 … ORDER BY confidence DESC,
   last_used DESC LIMIT 100`、`events` `importance>0.5 … ORDER BY timestamp DESC
   LIMIT 100`（另有 decay 过滤）。**没有向量索引**（无 faiss / hnsw / sqlite-vss /
   ANN），候选顺序与向量无关，排序在 Python 侧做；
3. 每条候选 `json.loads(embedding)` → `_vec_sim(qvec, ev)` **逐条算余弦**；
   `embed_sig` 与当前口径不符的旧向量 ×0.8 降权；
4. Python 侧按 `rank_score`（= sim/importance + tier_boost）排序，`collect()` 再用
   τ=0.35 过闸，`build_block()` 截 `budget/max_items`。

量级：`memory_context` 每源捞 `per_source_limit=6` 条传给
`recall_key_memories(limit=6)`，但 SQL 里写死的 **`LIMIT 100` 才是候选上限** ——
故**单轮单源最多对 `100(facts) + 100(events) = 200` 行各算一次余弦**，再截到
`limit*2 = 12` 条返回。也就是说「算余弦」这一步被 `LIMIT 100` 封顶在
~200 次/轮/源；但**扫描与排序仍是 O(该 scope 下 facts/events 行数)**，随记忆量
线性增长，去掉 LIMIT 就是真正的全表逐条算余弦。要规模化必须引入 ANN 索引或把向量
搬进向量库 —— **本轮不做**。

### embedding 服务不可用时的降级口径

`_Embedder.embed()`：DLL 加载失败 → 走 HTTP；HTTP 连续失败累计 `_fail_count >= 5`
→ `_available()` 返回 False → `embed()` 返回 `None`。`recall_key_memories()` 里：

```python
use_vec = bool(config.use_vector_recall) and bool(current_msg)
qvec = _embedder.embed(current_msg) if use_vec else None
if not qvec:
    use_vec = False
```

`use_vec=False` 时：

- `_sim()` 恒返回 `0.0`（不算余弦）；SQL 的 `(? = 0 OR embedding != '')` 放行所有行，
  **不要求有向量**；
- `importance` 退成 `facts.confidence` / `events` 的时间衰减重要度 `decayed`，
  `rank_score = importance + tier_boost`（permanent +0.25 / long 0 / short −0.15）；
- `memory_context._super_mem_candidates()` 取 `score = sim if sim>0 else rank`
  （`rank` 截到 1.0），`collect()` 用 τ=0.35 过闸。

→ **降级后走的是「置信度 / 新鲜度 / 分层」的启发式排序，不是语义相似排序**：
同一句 query 换个说法不再影响顺序，τ 闸此时卡的是 `rank_score` 而非 cosine。
不会报错、也不会空灌无分数据，但「语义相关」这件事没有了；配置
`use_vector_recall=False` 是同一条路径（主动关向量）。

## 怎么加一个新记忆源

1. 在 `memory_api.py` 里写个 `_xxx_block(...)`，拿不到就返回 `None`（异常自己吞，不许拖垮别的段）；
2. 在 `recall()` 的 `parts` 里**按优先级**插进去，带一个中文标签（日志要用）；
3. 别在 `bot_core` 里直接 import 那个源 —— `tests/test_sandglass_recall.py` 会拦
   （`bot_core` 里出现这些读口名就红）。

## 验收

```bash
python tests/test_memory_api.py       # 23 项：装填/预算/异常隔离/写口分发
python tests/test_memory_group_vector.py  # 群聊向量源开关：默认关/开启/私聊不受影响/不改 config.py
python tests/test_sandglass_recall.py # 含"bot_core 不许有第二个读口"的反向断言
grep -n "people.card\|build_people_block\|get_memory_prompt_block" bot_core.py   # 应为 0
```

## 已知没做的

- 群聊检索**默认**仍只取沙漏（super_memory 的 facts 由 EGO 钩子注入）。
  `group_memory_vector_enable`（默认关）可把向量源接进群聊主读口；边界、降级与
  量级上限见上节《向量源（super_memory）的边界与降级》。
- `messages` / 沙漏情景层**本轮不补向量**（见上节），要做得先补存储层 + ANN 索引。
- event 这一条不再是「没做」：`remember(kind="event")` 自 2026-11-01 起写进
  super_memory `events` 表（见写口表），旧文档「暂无存储层，返回 False」已作废。
