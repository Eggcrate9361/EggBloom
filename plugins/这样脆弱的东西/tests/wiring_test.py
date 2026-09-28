# -*- coding: utf-8 -*-
"""接线自测：走一遍 distill 产出 → 审核通过 → 落账 → 渲染 的完整通路。

用隔离的 target_id，跑完把产生的所有文件与历史记录清干净，不碰真实数据。
"""
import importlib
import json
import os
import shutil
import sys
import time
import types

sys.stdout.reconfigure(encoding="utf-8")
import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))
PLUGIN_DIR_T = _os.path.dirname(_HERE)
APP = _os.path.dirname(_os.path.dirname(PLUGIN_DIR_T))
PLUGIN_DIR_T = os.path.dirname(_HERE)
PKG_DIR = os.path.join(APP, "plugins", "这样脆弱的东西")
sys.path.insert(0, APP)

pkg = types.ModuleType("persona_distill")
pkg.__path__ = [PKG_DIR]
sys.modules["persona_distill"] = pkg

L = importlib.import_module("persona_distill.ledger")
RM = importlib.import_module("persona_distill.review_manager")
PM = importlib.import_module("persona_distill.personality_model")

TARGET = "__wiringtest__"
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def make_payload(rid, features, level="L3", delta=19, mode="full"):
    stats = {"unique": 19, "raw_total": 49, "merged_duplicates": 30,
             "chars": 259, "avg_len": 13.6, "max_len": 66, "noise_count": 1,
             "link_count": 2, "repeat_total": 21, "span": "2026-08-05 ~ 2026-08-15",
             "days": 7, "hour_hist": {1: 4}, "sources": {}}
    quality = {"mode": mode, "unique_messages": 19, "raw_messages": 49,
               "delta_messages": delta, "chars": 259, "span": stats["span"],
               "days": 7, "feasibility": {"ok": True, "ratio": 0.002,
                                          "grade": "超纲", "hint": "数据量仅为 L3 门槛的 0%。"},
               "llm": {"calls": 9, "tokens": 24261, "available": True},
               "critic": {"checked": 10, "dropped": 1, "weakened": 2, "added": 1},
               "citations": {"valid": 169, "invalid": 0, "dropped_entries": 0}}
    emap = {"M0001": {"q": "你好", "d": "2026-08-05", "h": 15},
            "M0018": {"q": "宝宝", "d": "2026-08-15", "h": 10}}
    led = L.load(TARGET)
    planned = L.plan(led, features, {"mode": mode, "delta_messages": delta}, None)
    return {
        "id": rid, "target_id": TARGET, "target_name": "测试对象", "level": level,
        "mode": mode, "data_volume": 19, "raw_volume": 49, "delta_messages": delta,
        "stats": stats, "counts": {"chat_logs": 26}, "features": features,
        "patterns": {"样本充分度": {"value": "19 条独立消息 —— 很薄", "confidence": 0.9,
                                "evidence": []}},
        "evidence_map": emap, "quality": quality,
        "ledger_plan": L.view(planned),
        "ledger_commit": {"id_map": {"fp_demo_1": "M0001"}, "quotes": emap},
        "config_snapshot": {"evidence_quotes": 3, "inject_min_confidence": 0.4,
                            "continuous": True},
        "created_at": time.time(),
    }


# ── 清场 ──
for f in (L._path(TARGET),
          os.path.join(RM.DISTILLS_DIR, f"{TARGET}_L3.md"),
          os.path.join(RM.DISTILLS_DIR, f"{TARGET}_L3.md.1"),
          os.path.join(RM.EVIDENCE_DIR, "dist_wire_1.json"),
          os.path.join(RM.EVIDENCE_DIR, "dist_wire_2.json"),
          RM.PENDING_FILE):
    if os.path.exists(f):
        os.remove(f)
RM._write_json(RM.HISTORY_FILE,
               [r for r in RM.get_history() if r.get("target_id") != TARGET])

feat1 = {
    "linguistics": [{"label": "高频词", "value": "你好×13", "confidence": 0.72,
                     "evidence": ["M0001"], "source": "llm"}],
    "relationship": [{"label": "亲昵称呼", "value": "叫你宝宝", "confidence": 0.55,
                      "evidence": ["M0018"], "source": "llm"}],
}

print("=== 第一轮：蒸馏产出 → 审核通过 ===")
RM.save_pending(make_payload("dist_wire_1", feat1))
check("待审核已写入", RM.has_pending())
res = RM.approve()
check("审核通过返回成功", res.get("success") is True, str(res)[:80])
check("账本已建立", L.exists(TARGET))
led = L.load(TARGET)
check("账本轮次 = 1", led["runs"] == 1, f"runs={led['runs']}")
check("账本记录两条结论",
      sum(len(v) for v in led["entries"].values()) == 2)
check("水位已记 1 条指纹", (led.get("seen") or {}).get("count") == 1,
      str((led.get("seen") or {}).get("count")))
check("历史里有 ledger_plan", bool((RM.get_record("dist_wire_1") or {}).get("ledger_plan")))

md = open(res["persona_path"], encoding="utf-8").read()
print(f"  人格文件 {len(md)} 字")
check("文件里有「本轮变化」段", "本轮变化" in md)
check("文件里有状态徽章", "活跃" in md)
check("文件里有原文引证", "M0001「你好」" in md)
check("文件里有人格注入卡", "人格注入 · " in md)
check("注入卡含高频词", "你好×13" in md)
check("文件标了连续蒸馏轮次", "连续蒸馏第 1 轮" in md)

print("\n=== 第二轮：只有一条拿到新证据 ===")
feat2 = {
    "linguistics": [{"label": "高频词", "value": "你好×13", "confidence": 0.8,
                     "evidence": ["M0001", "M0031"], "source": "llm"}],
}
RM.save_pending(make_payload("dist_wire_2", feat2, delta=2, mode="incremental"))
res2 = RM.approve()
led = L.load(TARGET)
check("账本轮次 = 2", led["runs"] == 2, f"runs={led['runs']}")
hf = [e for items in led["entries"].values() for e in items
      if e["label"] == "高频词"][0]
ba = [e for items in led["entries"].values() for e in items
      if e["label"] == "亲昵称呼"][0]
check("高频词 确认次数 = 2", hf["confirmations"] == 2, f"={hf['confirmations']}")
check("亲昵称呼 仍活跃（只沉默了一轮）", ba["state"] == "active", f"={ba['state']}")
check("历史里有 2 条记录",
      len([r for r in RM.get_history() if r.get("target_id") == TARGET]) == 2)
check("ledger_note 有内容", bool(res2.get("ledger_note")), res2.get("ledger_note", ""))

print("\n=== 清理 ===")
for f in (L._path(TARGET),
          os.path.join(RM.DISTILLS_DIR, f"{TARGET}_L3.md"),
          os.path.join(RM.DISTILLS_DIR, f"{TARGET}_L3.md.1"),
          RM.PENDING_FILE):
    if os.path.exists(f):
        os.remove(f)
for rid in ("dist_wire_1", "dist_wire_2"):
    ev = os.path.join(RM.EVIDENCE_DIR, f"{rid}.json")
    if os.path.exists(ev):
        os.remove(ev)
RM._write_json(RM.HISTORY_FILE,
               [r for r in RM.get_history() if r.get("target_id") != TARGET])
check("测试残留已清干净",
      not L.exists(TARGET) and not RM.has_pending()
      and not any(r.get("target_id") == TARGET for r in RM.get_history()))

print("\n" + "=" * 46)
print(f"结果：{'全部通过' if not fails else '失败 ' + str(fails)}")
sys.exit(1 if fails else 0)
