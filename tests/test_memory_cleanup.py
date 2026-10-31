# -*- coding: utf-8 -*-
"""tests/test_memory_cleanup.py —— 1.5.11 记忆清洗（events 按人 / [身份] 去噪）

背景（2026-10-30 真机遗留，交接文档 §6）

  1. **`events` 跨人**：events 表只有 scope_id、没有"这是谁的事"，
     群里所有人的事件混在一行里 —— 「事件：吃饭」会出现在别人的对话里。
  2. **`[身份]` 事实质量差**：群里随口一句「我是鸡蛋」「我是小狗」被正则抽成
     `身份`，再经 `persona_kernel` 进【长期人格】，每轮无条件注入给所有人。

本文件锁四件事：

  A. 事件写入带 `user_id`；**群聊召回严格按人**（老行 user_id 为空 = 无法归属，
     宁可不提）；私聊作用域本来只有一个人，老行仍算本人（避免升级后失忆）
  B. 老库启动时**幂等迁移**：events / persona_kernel 补 `user_id` 列，
     persona_kernel 唯一索引换成 (scope_id, user_id, key)
  C. 身份事实**形态闸**：裸名词丢弃；身份词尾 / 自报名字保留；玩笑信号丢弃
  D. `persona_kernel` 按人隔离，低置信度（玩笑/接话）不进

纯 Python + 临时库，不联网、不碰生产库（DB_PATH 由环境变量重定向）。
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# ★ 必须在加载插件之前设：模块级 `_memory = SuperMemory()` 会在 import 时建表/迁移，
#   不重定向就会对**生产库**动手（先例：tests/_sandbox.py 的 NEXSANDBASE_HOME）。
_TMP = tempfile.mkdtemp(prefix="eggbloom_memclean_")
_FRESH_DB = os.path.join(_TMP, "fresh.db")
os.environ["EGGBLOOM_SUPER_MEMORY_DB"] = _FRESH_DB

# 单独按文件加载，绕过 plugins.ego 包 __init__ 的一堆同级插件单例
_spec = importlib.util.spec_from_file_location(
    "eb_super_memory_under_test",
    os.path.join(ROOT, "plugins", "ego", "super_memory", "__init__.py"),
)
sm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sm)

# 单测不依赖 embedding 服务 / DLL
sm._embedder.embed = lambda text: None

GID = "900000001"
A = "3150000001"
B = "3150000002"

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def _rows(sql, args=()):
    conn = sqlite3.connect(sm.DB_PATH)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _exec(sql, args=()):
    conn = sqlite3.connect(sm.DB_PATH)
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def _recall_events(scope, scope_id, uid):
    items = sm._memory.recall_key_memories(scope, scope_id, "", limit=20, focus_user=uid) or []
    return [m["content"] for m in items if m.get("type") == "event"]


def _recall_facts(scope, scope_id, uid):
    items = sm._memory.recall_key_memories(scope, scope_id, "", limit=20, focus_user=uid) or []
    return [m["content"] for m in items if m.get("type") == "fact"]


# ── A. 事件按人 ─────────────────────────────────────────────
def test_events_per_user():
    print("[A] 事件按人：写入带 user_id，群聊召回严格过滤")
    ev_a = sm._memory.extract_events(GID, "我今天生日了", A)
    sm._memory._store_events(GID, ev_a, A)
    ev_b = sm._memory.extract_events(GID, "我中奖了", B)
    sm._memory._store_events(GID, ev_b, B)

    owners = sorted(r[0] for r in _rows("SELECT user_id FROM events WHERE scope_id=?", (GID,)))
    check("events 落库带 user_id", owners == sorted([A, B]), owners)

    ca = _recall_events("group", GID, A)
    cb = _recall_events("group", GID, B)
    check("A 只召回自己的事件", any("生日" in c for c in ca) and not any("中奖" in c for c in ca), ca)
    check("B 只召回自己的事件", any("中奖" in c for c in cb) and not any("生日" in c for c in cb), cb)

    print("     老数据（user_id 为空）无法归属 → 群聊不提、私聊仍算本人")
    _exec(
        "INSERT INTO events (scope_id, user_id, event_type, summary, participants, emotion, timestamp, importance, tier) "
        "VALUES (?, '', '事件', '没人认领的旧事件', '', '', ?, 0.9, 'long')",
        (GID, time.time()),
    )
    _exec(
        "INSERT INTO events (scope_id, user_id, event_type, summary, participants, emotion, timestamp, importance, tier) "
        "VALUES (?, '', '事件', '我私聊的老事件', '', '', ?, 0.9, 'long')",
        (A, time.time()),
    )
    ca2 = _recall_events("group", GID, A)
    check("群聊不注入无法归属的老事件", not any("没人认领" in c for c in ca2), ca2)
    pm = _recall_events("private", A, A)
    check("私聊仍能看到自己的老事件", any("我私聊的老事件" in c for c in pm), pm)

    print("     二参兼容（user_id 缺省 = 空，行为同改前）")
    legacy_call = sm._memory.extract_events(GID, "我今天生日了")
    check("extract_events 二参仍可用",
          bool(legacy_call) and legacy_call[0].get("user_id") == "", legacy_call)


# ── C. 身份去噪 ─────────────────────────────────────────────
def _facts(text):
    return sm._memory.extract_facts(GID, A, text)


def _identity(text):
    return [f for f in _facts(text) if f["category"] == "身份"]


def test_identity_denoise():
    print("\n[C] 身份事实形态闸")
    check("「我是鸡蛋」不进身份", not _identity("我是鸡蛋"), _identity("我是鸡蛋"))
    check("「我是小狗」不进身份", not _identity("我是小狗"), _identity("我是小狗"))
    check("「我是来吃饭的」不进身份", not _identity("我是来吃饭的"))
    check("「我是学生哈哈」不进身份（玩笑信号）", not _identity("我是学生哈哈"), _identity("我是学生哈哈"))

    f = _identity("我是学生")
    check("「我是学生」保留且置信度 ≥0.8",
          bool(f) and f[0]["content"] == "学生" and f[0]["confidence"] >= 0.8, f)

    f = _identity("我是大二学生")
    check("「我是大二学生」保留（长身份）",
          bool(f) and f[0]["content"] == "大二学生" and f[0]["confidence"] >= 0.9, f)

    f = _identity("我叫小明")
    check("「我叫小明」保留为身份（自报名字）",
          bool(f) and f[0]["content"] == "小明" and f[0]["confidence"] >= 0.8, f)

    f = _identity("我是一名产品经理")
    check("量词被剥掉、显式自述保留",
          bool(f) and f[0]["content"] == "产品经理" and f[0]["confidence"] >= 0.8, f)

    pref = [x for x in _facts("我喜欢吃火锅") if x["category"] == "偏好"]
    check("非身份事实仍走 0.7 默认置信度",
          bool(pref) and pref[0]["confidence"] == 0.7, pref)


# ── D. persona_kernel 按人 ─────────────────────────────────
def test_persona_per_user():
    print("\n[D] 长期人格按人隔离 + 低置信度不进")
    sm._memory._update_persona_kernel(
        GID, [{"category": "身份", "content": "学生", "confidence": 0.9}], A)
    sm._memory._update_persona_kernel(
        GID, [{"category": "身份", "content": "程序员", "confidence": 0.9}], B)

    pa = sm._memory.get_persona_export(GID, focus_user=A, scope="group")
    pb = sm._memory.get_persona_export(GID, focus_user=B, scope="group")
    check("A 的长期人格只有自己的身份", pa.get("user_identity") == "学生", pa)
    check("B 的长期人格只有自己的身份", pb.get("user_identity") == "程序员", pb)

    sm._memory._update_persona_kernel(
        GID, [{"category": "身份", "content": "鸡蛋", "confidence": 0.35}], B)
    pb2 = sm._memory.get_persona_export(GID, focus_user=B, scope="group")
    check("低置信度事实不覆盖长期人格", pb2.get("user_identity") == "程序员", pb2)

    print("     群聊里老的人格行（user_id 空）不再注入给任何人")
    _exec("INSERT INTO persona_kernel (scope_id, user_id, key, value, confidence, last_updated) "
          "VALUES (?, '', 'user_identity', '老数据', 0.95, ?)", (GID, time.time()))
    pa2 = sm._memory.get_persona_export(GID, focus_user=A, scope="group")
    check("群聊不注入无法归属的人格行", pa2.get("user_identity") == "学生", pa2)


# ── E. 事实也按人（与 1.5.10 的 focus_user 口径对齐）────────────
def test_facts_per_user():
    print("\n[E] 事实按人")
    sm._memory._store_facts(
        GID, A, [{"category": "偏好", "content": "喜欢：吃火锅", "confidence": 0.9}], "我喜欢吃火锅")
    sm._memory._store_facts(
        GID, B, [{"category": "偏好", "content": "喜欢：打游戏", "confidence": 0.9}], "我喜欢打游戏")

    fa = _recall_facts("group", GID, A)
    fb = _recall_facts("group", GID, B)
    check("A 只召回自己的事实", any("吃火锅" in c for c in fa) and not any("打游戏" in c for c in fa), fa)
    check("B 只召回自己的事实", any("打游戏" in c for c in fb) and not any("吃火锅" in c for c in fb), fb)


# ── B. 老库迁移 ────────────────────────────────────────────
def test_legacy_migration():
    print("\n[B] 老库迁移：补列 + 换唯一索引 + 幂等")
    legacy_db = os.path.join(_TMP, "legacy.db")
    conn = sqlite3.connect(legacy_db)
    conn.executescript("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            participants TEXT DEFAULT '',
            emotion TEXT DEFAULT '',
            timestamp REAL NOT NULL,
            importance REAL DEFAULT 0.6
        );
        CREATE TABLE persona_kernel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            confidence REAL DEFAULT 0.8,
            last_updated REAL NOT NULL
        );
        CREATE UNIQUE INDEX idx_persona_key ON persona_kernel(scope_id, key);
        INSERT INTO events (scope_id, event_type, summary, participants, emotion, timestamp, importance)
            VALUES ('g1', '事件', '迁移前的老事件', '', '', 1.0, 0.9);
        INSERT INTO persona_kernel (scope_id, key, value, confidence, last_updated)
            VALUES ('g1', 'user_identity', '鸡蛋', 0.9, 1.0);
    """)
    conn.commit()
    conn.close()

    sm.DB_PATH = legacy_db
    sm._memory = sm.SuperMemory()      # __init__ → _init_db → 迁移

    ecols = [r[1] for r in _rows("PRAGMA table_info(events)")]
    pcols = [r[1] for r in _rows("PRAGMA table_info(persona_kernel)")]
    check("events 补上 user_id 列", "user_id" in ecols, ecols)
    check("persona_kernel 补上 user_id 列", "user_id" in pcols, pcols)
    check("老事件没被删", _rows("SELECT COUNT(*) FROM events")[0][0] == 1)
    check("老人格行没被删", _rows("SELECT COUNT(*) FROM persona_kernel")[0][0] == 1)

    idx_cols = [r[2] for r in _rows("PRAGMA index_info(idx_persona_key)")]
    check("唯一索引改为 (scope_id, user_id, key)",
          idx_cols == ["scope_id", "user_id", "key"], idx_cols)

    # 幂等：再启动一次不该炸、不该丢数据
    try:
        sm.SuperMemory()
        ok = True
        err = ""
    except Exception as e:            # pragma: no cover - 只为报错可读
        ok = False
        err = repr(e)
    check("重复迁移幂等", ok and _rows("SELECT COUNT(*) FROM events")[0][0] == 1, err)


def main():
    test_events_per_user()
    test_identity_denoise()
    test_persona_per_user()
    test_facts_per_user()
    test_legacy_migration()

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
