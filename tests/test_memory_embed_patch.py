# -*- coding: utf-8 -*-
"""tests/test_memory_embed_patch.py —— 记忆向量链路补丁（2026-11-03）

依据 `docs/plan/记忆向量链路补丁方案.md`：向量模型**不换**，改修几处既有缺陷。
本用例按方案里的编号逐条锁死：

  A. P0-1 嵌「带类别前缀」的文本（facts `[category] content` / events `[事件] summary`）
  B. P0-2 每条内容**只嵌一次**（原来是"判断一次、取值再一次"）
  C. P1-2 每条向量带口径签名 `引擎:模型:维度`，且真的落库
  D. P1-2 迁移幂等：老库缺 `embed_sig` 列时补上，重复跑不报错
  E. P1-1 维度不一致**计数**并只告警一次（不再静默归零），`get_vector_stats` 能看到
  F. P0-3 `reembed_all`：dry_run 不写库、实跑按新口径重算并回填签名、幂等
  G. P1-2 跨口径的旧向量**降权 ×0.8**，不是直接丢

用临时库跑，不碰生产记忆：`EGGBLOOM_SUPER_MEMORY_DB` 必须在 import 之前设。
"""
import json
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="egg_embed_patch_")
_DB = os.path.join(_TMP, "super_memory.db")
os.environ["EGGBLOOM_SUPER_MEMORY_DB"] = _DB

import plugins.ego.super_memory as sm  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

GID = "1000000003"      # 合成群号（真实号不进仓库，提交规范 §七）
UID = "1000000001"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


# ── 用确定性假嵌入替换真服务（本机没有 embedding 服务）────────────────
CALLS = []


def fake_embed(text):
    CALLS.append(str(text))
    n = len(str(text)) % 9 + 1
    return [float(n), 0.5, 0.25, 0.125, 1.0, 0.0, 0.5, 0.5]


sm._embedder.embed = fake_embed
sm._embedder._available = lambda: True
sm._memory.config["use_vector_recall"] = True
sm._memory.config["embed_min_sim"] = 0.0


def reset_calls():
    del CALLS[:]


def main():
    print("[A] P0-1：嵌的是「带类别前缀」的文本")
    reset_calls()
    sm._memory._store_facts(GID, UID, [{"category": "偏好", "content": "不喜欢：香菜"}], "原文")
    check("facts 嵌 `[类别] 内容`", "[偏好] 不喜欢：香菜" in CALLS, CALLS)
    check("不是裸 content", "不喜欢：香菜" not in
          [c for c in CALLS if not c.startswith("[")], CALLS)

    reset_calls()
    sm._memory._store_events(GID, [{"event_type": "social", "summary": "A 去吃了火锅",
                                    "importance": 0.8}], UID)
    check("events 嵌 `[事件] 摘要`", "[事件] A 去吃了火锅" in CALLS, CALLS)

    print("\n[B] P0-2：每条内容只嵌一次")
    reset_calls()
    sm._memory._store_facts(GID, UID, [{"category": "身份", "content": "高三学生"}], "原文")
    check("★ 一条事实 → 一次 embed", len(CALLS) == 1, CALLS)
    reset_calls()
    sm._memory._store_events(GID, [{"event_type": "social", "summary": "只写一次",
                                    "importance": 0.9}], UID)
    check("★ 一条事件 → 一次 embed", len(CALLS) == 1, CALLS)

    print("\n[C] P1-2：向量带口径签名，并落库")
    sig_direct = sm._embed_sig([0.1] * 8)
    check("签名形状 引擎:模型:维度", sig_direct.count(":") == 2, sig_direct)
    check("维度写进去了", sig_direct.endswith(":8"), sig_direct)
    check("能区分引擎", sig_direct.startswith(("ligguf:", "http:", "unknown:")), sig_direct)
    conn = sqlite3.connect(_DB)
    rows = conn.execute("SELECT content, embed_sig FROM facts WHERE content LIKE '%高三%'").fetchall()
    conn.close()
    check("★ facts 落库带 embed_sig", bool(rows) and rows[0][1] == sig_direct, rows)

    conn = sqlite3.connect(_DB)
    ev = conn.execute("SELECT summary, embed_sig FROM events WHERE summary='只写一次'").fetchall()
    conn.close()
    check("★ events 落库带 embed_sig", bool(ev) and ev[0][1] == sig_direct, ev)

    print("\n[D] P1-2 迁移：老库补列且幂等")
    mig_db = os.path.join(_TMP, "migrate.db")
    src = sqlite3.connect(_DB)
    dst = sqlite3.connect(mig_db)
    src.backup(dst)
    src.close()
    try:
        dst.execute("ALTER TABLE facts DROP COLUMN embed_sig")
        dst.execute("ALTER TABLE events DROP COLUMN embed_sig")
        dst.commit()
        before = [r[1] for r in dst.execute("PRAGMA table_info(facts)").fetchall()]
        check("准备：老库没有 embed_sig 列", "embed_sig" not in before, before)
    finally:
        dst.close()
    _old_path = sm.DB_PATH
    try:
        sm.DB_PATH = mig_db
        sm._memory._init_db()
        sm._memory._init_db()          # 再跑一次必须不报错（幂等）
        c2 = sqlite3.connect(mig_db)
        fcols = [r[1] for r in c2.execute("PRAGMA table_info(facts)").fetchall()]
        ecols = [r[1] for r in c2.execute("PRAGMA table_info(events)").fetchall()]
        c2.close()
        check("★ facts 补上 embed_sig", "embed_sig" in fcols, fcols)
        check("★ events 补上 embed_sig", "embed_sig" in ecols, ecols)
    finally:
        sm.DB_PATH = _old_path

    print("\n[E] P1-1：维度不一致要计数，不只告警一次")
    sm._DIM_MISMATCH["count"] = 0
    sm._DIM_MISMATCH["pairs"].clear()
    s = sm._vec_sim([0.1] * 8, [0.1] * 4)
    check("维度不一致仍返回 0.0（行为不变）", s == 0.0, s)
    check("★ 计数 +1", sm._DIM_MISMATCH["count"] == 1, sm._DIM_MISMATCH)
    check("记下了维度对", (8, 4) in sm._DIM_MISMATCH["pairs"], sm._DIM_MISMATCH["pairs"])
    sm._vec_sim([0.1] * 8, [0.1] * 4)
    check("★ 计数会累加（能看出严重程度）", sm._DIM_MISMATCH["count"] == 2)
    check("维度对只记一次", len(sm._DIM_MISMATCH["pairs"]) == 1, sm._DIM_MISMATCH["pairs"])
    check("等长仍能算出相似度", abs(sm._vec_sim([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9)

    stats = sm.get_vector_stats()
    check("★ get_vector_stats 暴露 dim_mismatch", stats.get("dim_mismatch") == 2, stats)
    check("★ get_vector_stats 暴露 dim_seen", list(stats.get("dim_seen") or []) == [(8, 4)],
          stats.get("dim_seen"))
    check("get_vector_stats 暴露当前口径", bool(stats.get("embed_sig")), stats.get("embed_sig"))

    print("\n[F] P0-3：reembed_all")
    # 造一条「旧口径」的向量：直接改库，模拟补丁之前写入的数据
    conn = sqlite3.connect(_DB)
    conn.execute("UPDATE facts SET embedding=?, embed_sig='' WHERE content LIKE '%高三%'",
                 (json.dumps([9.0] * 8),))
    conn.commit()
    old_emb = conn.execute("SELECT embedding FROM facts WHERE content LIKE '%高三%'").fetchone()[0]
    conn.close()
    reset_calls()
    dry = sm._memory.reembed_all(dry_run=True)
    check("dry_run 成功", dry.get("ok") is True, dry)
    check("dry_run 标了 dry_run", dry.get("dry_run") is True)
    check("★ dry_run 数到了 facts", (dry.get("facts") or 0) >= 1, dry)
    conn = sqlite3.connect(_DB)
    still = conn.execute("SELECT embedding FROM facts WHERE content LIKE '%高三%'").fetchone()[0]
    conn.close()
    check("★ dry_run 不写库", still == old_emb, (still[:20], old_emb[:20]))
    check("dry_run 也用新口径嵌", "[身份] 高三学生" in CALLS, CALLS)

    real = sm._memory.reembed_all()
    check("实跑成功", real.get("ok") is True and real.get("dry_run") is False, real)
    check("实跑同样计数", (real.get("facts") or 0) >= 1, real)
    conn = sqlite3.connect(_DB)
    new_emb, new_sig = conn.execute(
        "SELECT embedding, embed_sig FROM facts WHERE content LIKE '%高三%'").fetchone()
    conn.close()
    check("★ 实跑改写了向量", new_emb != old_emb)
    check("★ 实跑回填了签名", new_sig == sig_direct, new_sig)
    check("向量能解析回列表", isinstance(json.loads(new_emb), list))

    again = sm._memory.reembed_all()
    conn = sqlite3.connect(_DB)
    emb2 = conn.execute("SELECT embedding FROM facts WHERE content LIKE '%高三%'").fetchone()[0]
    conn.close()
    check("★ 幂等：再跑一次结果一样", again.get("ok") is True and emb2 == new_emb)

    scope_only = sm._memory.reembed_all(scope_id="不存在的群")
    check("scope_id 过滤生效（不存在的 scope 什么都不改）",
          scope_only.get("ok") is True and scope_only.get("facts") == 0, scope_only)

    print("\n[G] P1-2：跨口径只降权，不全丢")
    conn = sqlite3.connect(_DB)
    conn.execute("DELETE FROM facts WHERE category='对比'")
    now = time.time()
    vec = json.dumps([0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    cur_prefix = sm._current_embed_prefix()
    conn.execute(
        "INSERT INTO facts (scope_id, user_id, category, content, confidence, source_msg,"
        " created_at, last_used, embedding, tier, embed_sig) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (GID, UID, "对比", "同一句话的新口径", 0.9, "", now, now, vec, "long",
         cur_prefix + "8"))
    conn.execute(
        "INSERT INTO facts (scope_id, user_id, category, content, confidence, source_msg,"
        " created_at, last_used, embedding, tier, embed_sig) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (GID, UID, "对比", "同一句话的旧口径", 0.9, "", now, now, vec, "long",
         "ligguf:old-model:8"))
    conn.commit()
    conn.close()

    res = sm._memory.recall_key_memories("group", GID, "同一句话", limit=20)
    sims = {r.get("content"): r.get("sim") for r in res if "口径" in str(r.get("content"))}
    same = sims.get("[对比] 同一句话的新口径")
    cross = sims.get("[对比] 同一句话的旧口径")
    check("两条都召回了（旧口径没被直接丢）", same is not None and cross is not None, sims)
    if same is not None and cross is not None:
        check("★ 旧口径 = 新口径 × 0.8", abs(cross - same * 0.8) < 1e-6, (same, cross))
        check("旧口径不是 0", cross > 0, cross)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
