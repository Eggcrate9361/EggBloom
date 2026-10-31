# -*- coding: utf-8 -*-
"""tests/test_fact_queue.py —— 事实抽取管线：src + 置信度 + 待确认队列

1.5.11 尾巴（设计文档 §3）：事实必须带来源与置信度，**低置信度只进待确认队列，
不进 prompt**。2026-10-31 真机的血泪：抽取器把「我是鸡蛋」「概率：多少」当事实
直接入库，每轮注入，模型于是乱认人。

这个文件锁五件事：

  A. 置信度按句子形态给：自述高（我叫X）、瞬时状态低（我在X / 今天X了）
  B. 低置信度 → `fact_candidates`，**不进** `facts`
  C. 高置信度 → 直接进 `facts`
  D. `pending_candidates` 列得出来；`confirm_candidate` 升为正式事实；
     `discard_candidate` 删掉
  E. 读侧只读 `facts`，队列里的内容不会被注入

纯 Python + 临时库（DB_PATH 由环境变量重定向），不碰生产库。
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="eggbloom_factq_")
os.environ["EGGBLOOM_SUPER_MEMORY_DB"] = os.path.join(_TMP, "q.db")

_spec = importlib.util.spec_from_file_location(
    "eb_super_memory_factq", os.path.join(ROOT, "plugins", "ego", "super_memory", "__init__.py"))
sm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sm)
sm._embedder.embed = lambda text: None

MEM = sm._memory
GID, UID = "900000009", "3150009999"

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


def facts():
    conn = sqlite3.connect(sm.DB_PATH)
    try:
        return conn.execute("SELECT category, content, confidence FROM facts").fetchall()
    finally:
        conn.close()


def candidates():
    conn = sqlite3.connect(sm.DB_PATH)
    try:
        return conn.execute("SELECT id, category, content, confidence FROM fact_candidates").fetchall()
    finally:
        conn.close()


def main():
    print("[A] 置信度按形态给")
    hi = MEM.extract_facts(GID, UID, "我叫小明")
    check("自述名字抽到了", any(f["content"] == "小明" for f in hi), hi)
    check("自述名字置信度 ≥ 0.6",
          all(f["confidence"] >= 0.6 for f in hi if f["content"] == "小明"), hi)

    lo = MEM.extract_facts(GID, UID, "今天下雨了")
    check("瞬时事件抽到了", any(f["category"] == "事件" for f in lo), lo)
    check("瞬时事件置信度 < 0.6",
          all(f["confidence"] < 0.6 for f in lo if f["category"] == "事件"), lo)

    print("\n[B] 低置信度进队列，不进 facts")
    MEM._store_facts(GID, UID, lo, source_msg="今天下雨了")
    check("facts 里没有它", not any("雨" in c for _, c, _ in facts()), facts())
    queued = candidates()
    check("队列里有它", any("雨" in c for _, _, c, _ in queued), queued)

    print("\n[C] 高置信度直接入库")
    MEM._store_facts(GID, UID, hi, source_msg="我叫小明")
    check("facts 里有小明", any(c == "小明" for _, c, _ in facts()), facts())
    check("小明不在队列里", not any(c == "小明" for _, _, c, _ in candidates()), candidates())

    print("\n[D] 确认与丢弃")
    pend = MEM.pending_candidates(limit=10, scope_id=GID)
    check("pending_candidates 列得出来", len(pend) >= 1, pend)
    cid = pend[0]["id"]
    ok = MEM.confirm_candidate(cid)
    check("confirm 成功", ok is True)
    check("确认后进了 facts", any(c == pend[0]["content"] for _, c, _ in facts()), facts())
    check("确认后从队列消失", not any(i == cid for i, _, _, _ in candidates()), candidates())

    # 再放一条进去丢掉
    MEM._store_facts(GID, UID, [{"category": "位置", "content": "在教室", "confidence": 0.3}],
                     source_msg="我在教室")
    pend2 = MEM.pending_candidates(limit=10, scope_id=GID)
    check("新候选进队", len(pend2) >= 1, pend2)
    if pend2:
        check("discard 成功", MEM.discard_candidate(pend2[0]["id"]) is True)
        check("丢弃后队列为空", not any(i == pend2[0]["id"] for i, _, _, _ in candidates()))

    print("\n[E] 读侧只看 facts")
    # 新放一条低置信度进来（不确认），它不能出现在召回里
    MEM._store_facts(GID, UID, [{"category": "偏好", "content": "喜欢：香菜", "confidence": 0.3}],
                     source_msg="我喜欢香菜")
    got = MEM.recall_key_memories("group", GID, "", limit=20, focus_user=UID)
    texts = " ".join(str(m.get("content", "")) for m in got)
    check("正式事实在召回里", "小明" in texts, texts[:160])
    check("未确认的候选不在召回里", "香菜" not in texts, texts[:160])

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
