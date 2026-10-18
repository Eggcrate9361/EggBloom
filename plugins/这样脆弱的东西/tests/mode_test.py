# -*- coding: utf-8 -*-
"""模式判定单测（纯函数，不调 LLM）。"""
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

DE = importlib.import_module("persona_distill.distill_engine")

CFG = {"continuous": True, "incremental_max_messages": 300}
EMPTY_LED = {"entries": {}}
LED = {"entries": {"linguistics": [{"eid": "linguistics::高频词"}]}}

fails = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {got}" + ("" if ok else f"（应为 {want}）"))
    if not ok:
        fails.append(name)


print("=== 模式判定 ===")
check("空账本 → full", DE.decide_mode(CFG, EMPTY_LED, [1, 2, 3], "auto", True), "full")
check("首次运行 → full", DE.decide_mode(CFG, LED, [1], "auto", True), "full")
check("关掉连续 → full",
      DE.decide_mode({"continuous": False}, LED, [1], "auto", False), "full")
check("少量新增 → incremental",
      DE.decide_mode(CFG, LED, [1] * 50, "auto", False), "incremental")
check("大量新增 → full",
      DE.decide_mode(CFG, LED, [1] * 500, "auto", False), "full")
check("边界 300 条 → incremental",
      DE.decide_mode(CFG, LED, [1] * 300, "auto", False), "incremental")
check("显式 full → full",
      DE.decide_mode(CFG, LED, [1] * 5, "full", False), "full")
check("显式 incremental → incremental",
      DE.decide_mode(CFG, LED, [1] * 5000, "incremental", False), "incremental")
check("无新增且 auto → 交给调用方拦截",
      DE.decide_mode(CFG, LED, [], "auto", False), "inline_none")

print("\n" + "=" * 40)
print(f"结果：{'全部通过' if not fails else '失败 ' + str(fails)}")
sys.exit(1 if fails else 0)
