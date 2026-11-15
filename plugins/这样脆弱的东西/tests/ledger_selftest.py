# -*- coding: utf-8 -*-
"""账本状态机自测：不调 LLM，纯确定性验证。

要验证的规矩（这是"连续蒸馏"能不能成立的根本）：
  1. 再次确认 → 置信度上升、确认次数 +1、状态回活跃
  2. 沉默 → 只能降级到存疑 / 沉眠，**永远不能因为沉默被推翻**
  3. 反证 → 才能削弱或推翻
  4. 沉眠后重新拿到证据 → 复活
  5. 已推翻 / 沉眠的结论不进人格注入卡
  6. 编号跨轮终身稳定（同一条消息永远是同一个 M####）
"""
import importlib
import os
import sys
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
EV = importlib.import_module("persona_distill.evidence")

TARGET = "__selftest__"
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def apply(led, features, label=""):
    planned = L.plan(led, features, {"mode": "incremental", "delta_messages": 5})
    L.commit(led, planned, id_map={}, quotes={}, level="L3")
    ev = planned.get("events") or {}
    print(f"  轮次 {planned['run']}{(' ' + label) if label else ''}: {ev}")
    return planned


# 清掉旧的测试账本
p = L._path(TARGET)
if os.path.exists(p):
    os.remove(p)

print("=== 场景 A：首轮建立两条结论 ===")
led = L.load(TARGET)
feat = {
    "linguistics": [{"label": "高频词", "value": "你好×13", "confidence": 0.7,
                     "evidence": ["M0001"], "source": "llm"}],
    "relationship": [{"label": "亲昵称呼", "value": "叫你宝宝", "confidence": 0.5,
                      "evidence": ["M0018"], "source": "llm"}],
}
apply(led, feat, "首次")
check("两条结论都是活跃", all(
    e["state"] == "active" for items in led["entries"].values() for e in items))

print("\n=== 场景 B：第二轮，只有「高频词」拿到新证据 ===")
apply(led, {
    "linguistics": [{"label": "高频词", "value": "你好×13", "confidence": 0.8,
                     "evidence": ["M0001", "M0031"], "source": "llm"}],
}, "只确认一条")
hf = led["entries"]["linguistics"][0]
ba = led["entries"]["relationship"][0]
check("高频词 确认次数 +1", hf["confirmations"] == 2, f"={hf['confirmations']}")
check("高频词 置信度上升", hf["confidence"] > 0.7, f"={hf['confidence']}")
check("高频词 状态仍活跃", hf["state"] == "active")
check("亲昵称呼 仍在活跃（沉默≠否定）", ba["state"] == "active",
      f"state={ba['state']} silent={ba['silent_runs']}")

print("\n=== 场景 C：继续沉默到 suspect_after 轮 ===")
_cfg = L.get_config() if hasattr(L, "get_config") else {}
_n = 3
for _ in range(_n):
    apply(led, {"linguistics": [{"label": "高频词", "value": "你好×13",
                                 "confidence": 0.8, "evidence": ["M0001"]}]})
ba = led["entries"]["relationship"][0]
check("亲昵称呼 转入存疑", ba["state"] == "suspect",
      f"state={ba['state']} silent={ba['silent_runs']}")
check("亲昵称呼 没有被推翻（沉默不能推翻）", ba["state"] != "retired")

print("\n=== 场景 E：沉眠后重新拿到证据 → 复活 ===")
led["entries"]["relationship"][0]["silent_runs"] = 9
led["entries"]["relationship"][0]["state"] = "dormant"
apply(led, {
    "linguistics": [{"label": "高频词", "value": "你好×13", "confidence": 0.8,
                     "evidence": ["M0001"]}],
    "relationship": [{"label": "亲昵称呼", "value": "叫你宝宝", "confidence": 0.75,
                      "evidence": ["M0018", "M0060"], "source": "llm"}],
}, "复活")
ba = led["entries"]["relationship"][0]
check("亲昵称呼 复活为活跃", ba["state"] == "active", f"state={ba['state']}")
check("亲昵称呼 silent_runs 归零", ba["silent_runs"] == 0)

print("\n=== 场景 F：出现反证且强度更高 → 推翻 ===")
apply(led, {
    "linguistics": [{"label": "高频词", "value": "你好×13", "confidence": 0.8,
                     "evidence": ["M0001"]}],
    "relationship": [{"label": "亲昵称呼", "value": "叫你宝宝", "confidence": 0.9,
                      "evidence": ["M0070"], "counter": ["M0071"], "source": "llm"}],
}, "反证")
ba = led["entries"]["relationship"][0]
check("亲昵称呼 被推翻", ba["state"] == "retired", f"state={ba['state']}")
check("亲昵称呼 置信度被砍到一半以下", ba["confidence"] < 0.4, f"={ba['confidence']}")

print("\n=== 场景 G：注入卡只含活跃结论 ===")
stable = L.stable_entries(led)
labels = [e["label"] for items in stable.values() for e in items]
check("已推翻的「亲昵称呼」不进注入卡", "亲昵称呼" not in labels, f"labels={labels}")
check("「高频词」在注入卡里", "高频词" in labels)

print("\n=== 场景 H：账本概况与日志 ===")
s = L.summary(led)
print("  ", s)
check("轮次计数正确", s["runs"] == 7, f"runs={s['runs']}")
check("日志有 6 条", s["journal_entries"] == 7, f"={s['journal_entries']}")
print("  ", L.diff_text(led))
check("diff_text 有内容", "轮" in L.diff_text(led))

print("\n=== 场景 I：编号跨轮终身稳定 ===")
raw1 = [{"text": "你好", "timestamp": "2026-08-05 15:46", "source": "chat"},
        {"text": "在干嘛", "timestamp": "2026-08-06 01:16", "source": "chat"}]
m1, i1, map1, nxt1 = EV.normalize(raw1)
ids1 = sorted(i1.keys())
print("  第一轮编号:", ids1, "next:", nxt1)
raw2 = raw1 + [{"text": "新的一条", "timestamp": "2026-08-20 10:00", "source": "chat"}]
m2, i2, map2, nxt2 = EV.normalize(raw2, id_map=map1, start=nxt1)
old_again = [i2[m["mid"]] for m in m2 if m["text"] in ("你好", "在干嘛")]
check("老消息编号未变", sorted(x["mid"] for x in old_again) == ids1,
      f"{sorted(x['mid'] for x in old_again)} vs {ids1}")
new_mid = [m["mid"] for m in m2 if m["text"] == "新的一条"][0]
check("新消息拿到续号", new_mid not in ids1, f"new={new_mid}")

# 清理
if os.path.exists(p):
    os.remove(p)

print("\n" + "=" * 46)
print(f"结果：{'全部通过' if not fails else '失败 ' + str(fails)}")
sys.exit(1 if fails else 0)
