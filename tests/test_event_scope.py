# -*- coding: utf-8 -*-
"""事件的"按人隔离"边界（2026-11-01 用户质疑「按人隔离有什么必要」）。

## 结论（也写成可执行判据）

- **事实 / 他人事件 → 必须按人**：10-31 真机事故就是群里
  「事件：吃饭」「身份：老大」跨人注入，模型把 A 的事安到 B 头上；
- **Bot 自己的动作 → 不该按人**：那是我干的事。B 问"你昨天存的那张表情包呢"，
  不该因为那张图是 A 发的就答"没存过"。

所以拆成两个 SQL 片段：`_user_scope_filter`（事实，按人）与
`_event_scope_filter`（事件，动作类放行、其余按人）。

用临时库跑，不碰生产记忆：`EGGBLOOM_SUPER_MEMORY_DB` 必须在 import 之前设。
"""
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="egg_event_scope_")
_DB = os.path.join(_TMP, "super_memory.db")
os.environ["EGGBLOOM_SUPER_MEMORY_DB"] = _DB

import plugins.ego.super_memory as sm  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


GID = "829854136"
A, B = "3150007406", "2011257385"


def _emb(text):
    """像生产一样给事件算向量（没有嵌入服务时返回空串，并关掉向量召回）。"""
    try:
        v = sm._embedder.embed(text)
        return json.dumps(v) if v else ""
    except Exception:
        return ""


if not _emb("probe"):
    sm._memory.config["use_vector_recall"] = False


def seed():
    """两条事件：Bot 的动作（来源 A）、A 自己的事（他人事件）。"""
    conn = sqlite3.connect(_DB)
    now = time.time()
    conn.execute("DELETE FROM events")
    rows = [
        (GID, A, "meme_save", "存了一张表情包：发疯｜白发少年翻白眼（来自 A）",
         "", "", now - 60, 0.7, _emb("存了一张表情包：发疯"), "long"),
        (GID, A, "social", "A 昨天去吃了火锅", "", "", now - 60, 0.8, _emb("A 昨天去吃了火锅"), "long"),
    ]
    conn.executemany(
        "INSERT INTO events (scope_id, user_id, event_type, summary, participants,"
        " emotion, timestamp, importance, embedding, tier) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


seed()


def texts(focus_user, scope="group", message="表情包"):
    res = sm._memory.recall_key_memories(scope, GID, message, limit=10, focus_user=focus_user)
    return [r.get("content") or r.get("summary") or "" for r in res]


# ── 1. 动作类：谁问都能看到（作用域内）──────────────────────────────
for who in (A, B):
    got = " ".join(texts(who))
    check(f"★ 动作事件对 {who} 可见", "存了一张表情包" in got, got[:120])

# ── 2. 他人事件：仍然按人 ──────────────────────────────────────────
got_b = " ".join(texts(B))
check("★ 别人的事不串给 B（火锅不出现）", "火锅" not in got_b, got_b[:120])
got_a = " ".join(texts(A))
check("本人的事对本人可见（A 看得到火锅）", "火锅" in got_a, got_a[:120])

# ── 3. 无 focus_user 时不过滤（保持旧行为）──────────────────────────
got_none = " ".join(texts(""))
check("无 focus_user 不过滤", "火锅" in got_none and "存了一张表情包" in got_none,
      got_none[:120])

# ── 4. 私聊：老行（空 user_id）仍算本人的 ───────────────────────────
conn = sqlite3.connect(_DB)
now = time.time()
conn.execute("DELETE FROM events")
conn.executemany(
    "INSERT INTO events (scope_id, user_id, event_type, summary, participants,"
    " emotion, timestamp, importance, embedding, tier) VALUES (?,?,?,?,?,?,?,?,?,?)",
    [(B, "", "social", "老行：他说过今天加班", "", "", now - 60, 0.8, "", "long"),
     (B, A, "social", "别人的事：A 的老家在南方", "", "", now - 60, 0.8, "", "long")])
conn.commit()
conn.close()
res_priv = sm._memory.recall_key_memories("private", B, "加班", limit=10, focus_user=B)
blob = " ".join(r.get("content") or "" for r in res_priv)
check("私聊：老行（空 user_id）仍算本人", "加班" in blob, blob[:120])
check("私聊：别人的事仍不串进来", "老家" not in blob, blob[:120])

# ── 5. 片段本身的自检 ──────────────────────────────────────────────
sql_g, args_g = sm._event_scope_filter("group", A)
check("群事件过滤片段含动作白名单", "event_type IN" in sql_g, sql_g)
check("群事件过滤参数 = 动作类型 + 本人",
      len(args_g) == len(sm.ACTION_EVENT_TYPES) + 1 and args_g[-1] == A, args_g[-1:])
check("无 focus_user 时不加过滤", sm._event_scope_filter("group", "") == ("", ()))
check("事实过滤保持不变（仍严格按人）",
      sm._user_scope_filter("group", A) == (" AND user_id=?", (A,)),
      sm._user_scope_filter("group", A))
check("say/普通事件不在动作白名单里", "say" not in sm.ACTION_EVENT_TYPES, sm.ACTION_EVENT_TYPES)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
