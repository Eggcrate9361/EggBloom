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
       budget=600, cfg=None) -> str
```

装填顺序（也是优先级，预算不够先丢后面的）：

| # | 段 | 来源 | 边界 |
|---|---|---|---|
| 1 | 身份 | `people.card(uid, gid, private=…)` | 私聊才带 `scope=private` 的事实 |
| 2 | 称呼 | `people_alias.prompt_line(uid)` | 私聊/群聊共用同一份 |
| 3 | 跨场景 | `sandglass_bridge.cross_scene_recent` | 私聊取群里的；群聊**只取本人私聊过的** |
| 4 | 检索 | 私聊 `get_memory_prompt_block(light=True)`；群聊 `memory_context.build_block(sources=("sand",))` | 群聊只取沙漏源（super_memory 群侧由 EGO 注入，同源=双注） |
| 5 | 群画像 | `build_people_block(gid)` | 只按群取，不含私聊内容 |

日志（每轮最多两行，没注入就不打）：

```
[记忆] uid=3150007406 scene=group 注入 4 段/532 字（身份 92 / 称呼 18 / 跨场景 120 / 检索 302）
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
| `event` | **暂无存储层，返回 False**（不装作成功） | — |

## 怎么加一个新记忆源

1. 在 `memory_api.py` 里写个 `_xxx_block(...)`，拿不到就返回 `None`（异常自己吞，不许拖垮别的段）；
2. 在 `recall()` 的 `parts` 里**按优先级**插进去，带一个中文标签（日志要用）；
3. 别在 `bot_core` 里直接 import 那个源 —— `tests/test_sandglass_recall.py` 会拦
   （`bot_core` 里出现这些读口名就红）。

## 验收

```bash
python tests/test_memory_api.py       # 21 项：装填/预算/异常隔离/写口分发
python tests/test_sandglass_recall.py # 含"bot_core 不许有第二个读口"的反向断言
grep -n "people.card\|build_people_block\|get_memory_prompt_block" bot_core.py   # 应为 0
```

## 已知没做的

- `remember(kind="event")` 还没有存储层（super_memory 的 `events` 是另一条链，按人过滤
  在 1.5.10 已做，但没接进 `remember`）；
- 群聊检索仍只取沙漏，super_memory 的 facts 走 EGO 钩子注入（两处都在，但边界清楚：
  一个给"情景"、一个给"画像"）。真要合成一个池子，是 1.6.0 的事。
