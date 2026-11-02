# -*- coding: utf-8 -*-
"""记忆归属（2026-11-02，用户：他们做了什么、说了什么 bot 知道是谁做的）。

## 要的是什么

- 按私聊人分开记忆（§63/§68 已做：facts/events/messages 按 scope_id+user_id）；
- **bot 知道谁是谁**：鸡蛋/开发者/老大/爹 是同一个人；Texas 是朋友；
- 每条记忆标上**是谁的**，不匿名。

## 契约

1. `people.resolve_hit(text)` → (uid, 命中的那个叫法)；多候选命中不猜；
2. `people.label(uid, with_uid=False)` → 「老大（鸡蛋/开发者）」；
3. `bot_core._mention_identity_hint()`：消息里点到熟人 → 一句「谁是谁」；
4. `super_memory.get_prompt_block` 每行带 `【名字】` 前缀（事实/事件/好感度）；
5. `memory_context` 的检索条目同样带前缀。

运行：python tests/test_memory_attribution.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


# ── 1. people：别名 / 标签 / 反查 ────────────────────────────────
import people  # noqa: E402

OLD_EXTRA, OLD_BASE = people.EXTRA_FILE, people.PEOPLE_FILE
tmp = tempfile.mkdtemp()
people.EXTRA_FILE = os.path.join(tmp, "people_extra.json")
people.PEOPLE_FILE = os.path.join(tmp, "people.json")
people.reload()

OWNER, TEXAS = "3150007406", "2011257385"
try:
    people.set_alias(OWNER, "老大")
    for a in ("鸡蛋", "开发者", "爹"):
        people.add_alias(OWNER, a)
    people.add_fact(OWNER, "身份", "主要管理员（老大/爹）")
    people.set_alias(TEXAS, "Texas")
    people.add_fact(TEXAS, "身份", "朋友（老大的朋友）")

    uid, said = people.resolve_hit("鸡蛋说今晚不睡")
    check("★ 反查命中主人", uid == OWNER, uid)
    check("★ 带回他用的叫法", said == "鸡蛋", said)
    uid2, said2 = people.resolve_hit("Texas 帮我看看")
    check("★ 反查命中 Texas", uid2 == TEXAS, uid2)
    check("多候选不猜", people.resolve_hit("小abc")[0] == "")

    lbl = people.label(OWNER, with_uid=False)
    check("标签含三个叫法", all(x in lbl for x in ("老大", "鸡蛋", "开发者")), lbl)
    check("标签不带 QQ", OWNER not in lbl, lbl)

    # ── 2. bot_core：谁是谁提示 ─────────────────────────────
    import bot_core as bc  # noqa: E402

    hint = bc._mention_identity_hint("鸡蛋说今晚不睡", TEXAS)
    check("★ 提示里点出是同一个人", "同一个人" in hint, hint[:60])
    check("★ 提示带 QQ", OWNER in hint, hint[:80])
    check("提示带身份", "主要管理员" in hint, hint[:100])
    check("说的是自己就不提示", bc._mention_identity_hint("鸡蛋呢", OWNER) == "")
    check("无关句子不提示", bc._mention_identity_hint("今天天气不错", TEXAS) == "")

    # ── 3. super_memory：记忆带【名字】 ─────────────────────
    os.environ.setdefault("EGGBLOOM_SUPER_MEMORY_DB",
                          os.path.join(tmp, "sm.db"))
    from plugins.ego.super_memory import SuperMemory  # noqa: E402

    sm = SuperMemory()
    # 直接落一条事实（按库里**真实列名**插，别猜 schema —— 老库还有 embedding/tier）
    import sqlite3  # noqa: E402
    from plugins.ego import super_memory as sm_mod  # noqa: E402

    conn = sqlite3.connect(sm_mod.DB_PATH)
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(facts)").fetchall()}
    row = {"scope_id": TEXAS, "user_id": TEXAS, "category": "身份",
           "content": "高三学生", "confidence": 0.9,
           "created_at": 0, "created": 0, "last_used": 0}
    use = {k: v for k, v in row.items() if k in cols}
    if "embedding" in cols:
        use["embedding"] = ""
    if "tier" in cols:
        use["tier"] = "long"
    keys = ",".join(use)
    qs = ",".join("?" * len(use))
    conn.execute(f"INSERT INTO facts({keys}) VALUES({qs})", tuple(use.values()))
    conn.commit()
    conn.close()

    # 没有 embedding 的行会被向量召回闸挡掉（这是对的行为），测试只验"带不带名字"
    try:
        sm.config["use_vector_recall"] = False
    except Exception:
        pass
    blk = sm.get_prompt_block("private", TEXAS, current_msg="我昨天干了什么",
                              focus_user=TEXAS)
    check("★ 记忆块有【名字】前缀", "【Texas" in blk, blk[:120])
    check("记忆内容还在", "高三学生" in blk, blk[:120])

    # ── 4. 源码约束：检索块也带归属 ─────────────────────────
    mc = open(os.path.join(ROOT, "harness", "memory_context.py"), encoding="utf-8").read()
    check("memory_context 会加归属前缀", '【{_tag}】' in mc)
finally:
    people.EXTRA_FILE, people.PEOPLE_FILE = OLD_EXTRA, OLD_BASE
    people.reload()
    import shutil  # noqa: E402

    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
