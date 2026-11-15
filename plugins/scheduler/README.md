# scheduler —— 统一调度器

**全项目只留一处 sleep。**

## 为什么需要它

在此之前项目里有三套各自为政的定时器：

| 位置 | 实现 | 用途 |
|---|---|---|
| `bot_core.py` | `threading.Timer` | 消息缓冲合并（3/5/8 秒，裸图 4 秒，整批硬顶 8 秒） |
| `proactive_talk` | 自己的 `_heartbeat_loop` | 主动开口（默认 900 秒） |
| `slang_learner` | 独立间隔循环 | 抓热词 |

三处各自 sleep、互不可见。**再新增"定时任务"就是第四套** —— 所以这个插件存在的第一目的是收编，而不是新增。

## 它做什么 / 不做什么

- **做**：到点广播一个事件。
- **不做**：它不知道也不关心订阅方要干什么。

```python
# 调度器只管：到点了 → ctx.emit("distill.nightly", target="...")
# 订阅方在自己的插件里：
def on_event(ctx, event_name, payload):
    if event_name == "distill.nightly":
        ...   # 真正的活
```

调度与业务解耦 —— 这是它相对"再写一个 while True"的核心价值。

## 任务类型

| 类型 | 参数 | 说明 |
|---|---|---|
| `interval` | `every`（秒，≥5） | 每 N 秒 |
| `daily` | `at`（HH:MM） | 每天 |
| `weekly` | `at` + `weekday`（0=周一） | 每周某天 |

## 设计要点

- **单 tick 线程 + 单 worker 线程**：tick 只判断"到点了吗"并投递队列；真正派发在 worker 里做。
  所以订阅方慢（比如要调 AI 花几秒）**不会拖慢整个调度**。
- **同一任务不重入**：上一轮没跑完就跳过本轮并计数（`skipped`），不堆积。
- **不累积漂移**：`next_run` 由排程点反推，不是 `now + interval`。
- **断点不补跑**：机器休眠/程序停了几天，唤醒后只跳到"下一个未来时刻"，不会一次性补跑几十次。
- **持久化**：`plugins/state/scheduler.json`，重启不丢。

## 用 WebUI 管理

插件页「定时任务」：新建 / 启停 / 立即执行 / 删除，显示下次触发倒计时、已运行次数、跳过次数、上次错误。

对应动作（`/api/plugins/action`，`plugin_id=scheduler`）：

| action | 参数 | 说明 |
|---|---|---|
| `list` | — | 任务表 + 引擎状态 + 统计 |
| `add` | `name, kind, every/at/weekday, event, payload` | 新建 |
| `update` | `id` + 要改的字段 | 修改 |
| `remove` | `id` | 删除 |
| `run_now` | `id` | 立即执行一次（不改变排程） |
| `toggle` | `id` | 启用 / 停用 |

## 自检

```bash
python tests/test_scheduler.py     # 24 项：排程计算 / 真实触发 / 重入保护 / 动作接口 / 持久化
```

## 迁移计划（逐步收编那三套定时器）

迁移期间两边并存会重复触发，所以**逐个来**，每个都单独提交：

1. `slang_learner` 的抓热词间隔 → 调度器任务 ✅ 建议先做（最简单）
2. `proactive_talk` 的 900s 心跳 → 调度器任务
3. `bot_core` 的消息缓冲 `threading.Timer` → **不动**。它是毫秒级、每群一个、随消息重置的
   短时定时器，与"任务调度"不是同一类问题，混进来只会让调度器变脏。
