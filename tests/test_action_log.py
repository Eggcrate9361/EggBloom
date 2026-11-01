# -*- coding: utf-8 -*-
"""动作入账 → 记忆库（2026-11-01 用户报障「bot 自己做了，但它不知道」）。

## 判据

1. **短期账本**：`record()` 存下 source（从谁那儿）/ ref（哪张图）/ group_id；
2. **长期记忆**：`PERSIST_KINDS` 里的动作要真的落进 `events` 表，
   `remember("event")` 不再是空实现；
3. **不写脏东西**：`say` 这类不落记忆库（说话已在历史里，再写会淹掉事件）；
   跑测试时默认不写库（否则一条 `record(target="777")` 就污染生产记忆）；
4. **写记忆失败不影响动作**：记忆库坏了，动作照记。

为了让"落库"这条真被测到，本文件用 `EGGBLOOM_SUPER_MEMORY_DB` 指到临时库，
再用 `EGGBLOOM_ALLOW_ACTION_EVENTS=1` 放行 —— 必须在 import 之前设好。

运行：python tests/test_action_log.py
"""
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="egg_action_log_")
_DB = os.path.join(_TMP, "super_memory.db")
os.environ["EGGBLOOM_SUPER_MEMORY_DB"] = _DB
os.environ["EGGBLOOM_ALLOW_ACTION_EVENTS"] = "1"

import self_actions as sa  # noqa: E402
from harness import memory_api as ma  # noqa: E402

# 账本也指到临时文件，别动真的 self_actions.jsonl
sa.LOG_FILE = os.path.join(_TMP, "self_actions.jsonl")
sa._MEM = None

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


def events():
    if not os.path.exists(_DB):
        return []
    conn = sqlite3.connect(_DB)
    try:
        return list(conn.execute(
            "SELECT user_id, scope_id, event_type, summary FROM events"
            " ORDER BY timestamp").fetchall())
    finally:
        conn.close()


# ── 1. 短期账本：字段齐全 ────────────────────────────────────────────
rec = sa.record("meme_save", scene="group", target="829854136", group_id="829854136",
                source="3150007406", ref="collected/ab12.jpg",
                detail="发疯｜白发少年翻白眼")
check("record 返回 source", rec.get("source") == "3150007406", rec)
check("record 返回 ref", rec.get("ref") == "collected/ab12.jpg", rec)
check("record 返回 group_id", rec.get("group_id") == "829854136", rec)
check("detail 上限放宽到 120 字", sa.record("meme_save", detail="x" * 300)["detail"].__len__() == 120)
check("最近动作能按场景取出", any(r.get("kind") == "meme_save"
                                for r in sa.recent(scene="group", minutes=60)))

# ── 2. 长期记忆：真的落进 events 表 ─────────────────────────────────
rows = events()
check("★ 存表情包写进了记忆库", len(rows) >= 1, rows)
check("★ 事件摘要带来源与图名",
      any("存了一张表情包" in r[3] and "3150007406" in r[3] for r in rows), rows)
check("事件按说话人归属（群里=来源人）",
      any(r[0] == "3150007406" and r[1] == "829854136" for r in rows), rows)
check("事件类型记下动作种类",
      any(r[2] == "meme_save" for r in rows), rows)

# ── 3. 白名单：说话不落记忆库 ───────────────────────────────────────
before = len(events())
sa.record("say", scene="private", target="10001", detail="随口一句")
check("★ `say` 不进 events（不淹掉真事件）", len(events()) == before, len(events()) - before)

# ── 4. set_persist(False)：只记短期 ─────────────────────────────────
sa.set_persist(False)
before = len(events())
sa.record("poke", scene="group", target="829854136", group_id="829854136")
check("关掉持久化后不写库", len(events()) == before, len(events()) - before)
check("但短期账本照记", any(r.get("kind") == "poke" for r in sa.recent(scene="group", minutes=60)))
sa.set_persist(True)

# ── 5. memory_api.remember("event") 不再是空实现 ─────────────────────
before = len(events())
ok = ma.remember("event", uid="3150007406", text="手动补记：发了一张图",
                 scene="private", group_id="3150007406",
                 meta={"scope": "private", "scope_id": "3150007406",
                       "user_id": "3150007406", "event_type": "image"})
check("★ remember(event) 返回 True", ok is True, ok)
check("★ remember(event) 真的写进去了", len(events()) == before + 1, len(events()) - before)
check("remember(event) 空文本 → False", ma.remember("event", text="") is False)
check("未知 kind 仍返回 False", ma.remember("nope", text="x") is False)

# ── 6. 测试态默认不写库（防污染生产记忆）────────────────────────────
os.environ.pop("EGGBLOOM_ALLOW_ACTION_EVENTS", None)
_argv = sys.argv[:]
sys.argv = ["test_something.py"]
try:
    before = len(events())
    sa.record("poke", scene="group", target="829854136", group_id="829854136")
    check("★ 测试进程默认不写记忆库", len(events()) == before, len(events()) - before)
finally:
    sys.argv = _argv
    os.environ["EGGBLOOM_ALLOW_ACTION_EVENTS"] = "1"

# ── 7. 记忆库坏了不影响动作 ─────────────────────────────────────────
_saved = ma.remember
ma.remember = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("库坏了"))
try:
    r = sa.record("meme_save", scene="group", target="829854136",
                  group_id="829854136", source="1", ref="collected/x.jpg",
                  detail="测试")
    check("★ 写记忆抛异常也不影响记账", r.get("kind") == "meme_save", r)
finally:
    ma.remember = _saved

# ── 8. render 说清"从谁那儿" ────────────────────────────────────────
blk = sa.render(scene="group", target="829854136", minutes=120, limit=20)
check("render 有【你刚做过的事】标题", "你刚做过的事" in blk, blk[:40])
check("★ render 里能看出这张是存的表情包", "存了一张表情包" in blk, blk[:200])
check("★ render 里带来源", "来源 3150007406" in blk, blk[:200])

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
