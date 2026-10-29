# -*- coding: utf-8 -*-
"""1.5.10 G（vitals）回归：落盘 / cold_risk / 衰减 / 事件 / 病表 / 边界 / 开关。

只防回归；真机验收（天气、真互动）另记。
"""
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from plugins.vitals import core  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))


print("\n[1] 状态结构与容错")
st = core.default_state(1.0)
check("八个状态齐全", all(k in st for k in core.STATES) and len(core.STATES) == 8, core.STATES)
check("坏值当 0（不抛）", core.normalize({"cold": "x", "fever": 2})["cold"] == 0.0)
check("越界夹到 0–1", core.clamp(5) == 1.0 and core.clamp(-1) == 0.0)

print("\n[2] cold_risk 5 组手算")
cases = [
    ((5, 10, 30, True, 12), round((0.25 + 0.2 + 0.1 + 0.15) * 1.3, 4)),   # 冬 全中
    ((15, 3, 60, False, 6), 0.0),                                        # 夏 舒适
    ((9, 9, 50, False, 7), 0.45),                                        # 体感低+温差
    ((20, 10, 30, False, 10), 0.3),                                      # 温差+干燥
    ((12, 2, 90, True, 1), round((0.15) * 1.3, 4)),                      # 冬 雨雪
]
for (args, want) in cases:
    got = core.cold_risk(*args)
    check(f"cold_risk{args} = {want}", abs(got - want) < 1e-6, got)

print("\n[3] 5 天衰减到 0 且不为负")
s = core.default_state()
s["cold"] = 0.9
for _ in range(5):
    s = core.decay(s, 24)
check("cold 衰减到 0", abs(s["cold"]) < 1e-9, s["cold"])
check("永不为负", all(v >= 0 for k, v in s.items() if k in core.STATES), s)
s2 = core.decay(core.default_state(), 24 * 30)
check("长期不衰减成负数", all(v >= 0 for k, v in s2.items() if k in core.STATES), s2)

print("\n[4] 事件驱动")
s = core.default_state()
s = core.apply_event(s, "message.in", {"text": "闭嘴 滚", "hour": 15})
check("被骂 → wounded/guard 上升", s["wounded"] > 0.1 and s["guard"] > 0.05, s)
s = core.apply_event(core.default_state(), "message.in", {"text": "谢谢 喜欢你", "hour": 15})
check("被夸 → 回血", s["wounded"] == 0.0 and s["loneliness"] == 0.0, s)
s = core.apply_event(core.default_state(), "message.in", {"text": "在吗", "hour": 2})
check("深夜 → insomnia/irritability", s["insomnia"] >= 0.2 and s["irritability"] >= 0.1, s)
s = core.apply_event(core.default_state(), "no_reply_10min", {})
check("10 分钟无人回应 → loneliness", s["loneliness"] >= 0.15, s)

print("\n[5] 病表 12 条 + 缺字段报错")
p = os.path.join(ROOT, "plugins", "vitals", "data", "vitals_diseases.json")
items = core.load_diseases(p)
check("病表 12 条全部可解析", len(items) == 12, len(items))
check("每条字段齐全", all({"trigger", "effects", "decay", "recover"} <= set(d) for d in items))
bad = os.path.join(HERE, "_bad_diseases.json")
io.open(bad, "w", encoding="utf-8").write(json.dumps({"diseases": [{"id": "x", "name": "y"}]}))
try:
    core.load_diseases(bad)
    check("缺字段抛错（不静默跳过）", False)
except ValueError:
    check("缺字段抛错（不静默跳过）", True)
finally:
    os.remove(bad)

print("\n[6] 边界与表达")
s = core.default_state()
s["wounded"] = 0.9
check("求助信号匹配（含不想活）", bool(core.HELP_RE.search("我不想活了")))
check("自伤/辱骂各自匹配", bool(core.SELF_HARM_RE.search("我想死")) and bool(core.ABUSE_RE.search("傻逼")))
check("↔重置接口清零", all(core.reset_all(s)[k] == 0 for k in core.STATES))
check("受伤不注入攻击性", "受伤" in core.symptom_line({"wounded": 0.9}) or core.symptom_line({"wounded": 0.9}) == "")
fp = core.form_params({"cold": 0.8})
check("生病只会更短（≤1）", 0 < fp["max_chars_factor"] <= 1 and fp["max_parts_factor"] <= 1, fp)
check("无症状零注入", core.symptom_line(core.default_state()) == "")


print("\n[7] 插件 UI 与动作通道")
import plugins.vitals as vp  # noqa: E402
check("PLUGIN_ACTIONS 声明", isinstance(getattr(vp, "PLUGIN_ACTIONS", None), dict)
      and {"get_vitals", "get_diseases", "reset_vitals"} <= set(vp.PLUGIN_ACTIONS))
check("动作可调用且返回 dict", isinstance(vp.PLUGIN_ACTIONS["get_vitals"]({}), dict))
check("ui.html 存在", os.path.isfile(os.path.join(ROOT, "plugins", "vitals", "ui.html")))

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
