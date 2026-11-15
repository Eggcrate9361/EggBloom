# -*- coding: utf-8 -*-
"""tests/test_vitals_gate.py —— vitals（显示名「疾病与精神疾病」）内部闸

2026-11-01 用户报"疾病与精神疾病不能正常使用"：插件页打开了，但状态永远是 0、
页面一直"无采样"。根因是 `enabled()` 默认取 manifest 的 `default_enabled`（False），
而配置里根本没有 `vitals_enable` 这个键 → `_on_event` 每次第一行就 return。

本用例锁住：
  A. 未配置 `vitals_enable` → 跟随插件开关（True）
  B. 显式 False → 关（配置页能一键关）
  C. 显式 True → 开
  D. `_on_event("message.in")` 真的会改状态并落盘
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import importlib  # noqa: E402
v = importlib.import_module("plugins.vitals")

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


class FakeCtx:
    def __init__(self, cfg=None):
        self.cfg = cfg or {}

    def get_config(self, k, default=None):
        return self.cfg.get(k, default)

    def log(self, msg, level="info"):
        pass


def main():
    print("[A] 未配置 vitals_enable")
    v._CTX = FakeCtx({})
    check("跟随插件开关（True）", v.enabled() is True, v.enabled())

    print("\n[B] 显式关闭")
    v._CTX = FakeCtx({"vitals_enable": False})
    check("vitals_enable=False → 关", v.enabled() is False, v.enabled())

    print("\n[C] 显式开启")
    v._CTX = FakeCtx({"vitals_enable": True})
    check("vitals_enable=True → 开", v.enabled() is True, v.enabled())

    print("\n[D] 事件真的会改状态（message.in）")
    td = tempfile.mkdtemp(prefix="eb_vitals_")
    v._STATE_PATH = os.path.join(td, "vitals.json")
    v._STATE = None
    v._CTX = FakeCtx({})
    before = v.load_state()
    v._on_event(v._CTX, "message.in",
                {"text": "烦死了", "group_id": "1", "at_bot": True, "ts": 0})
    after = v.load_state()
    changed = any(abs(float(after.get(k, 0)) - float(before.get(k, 0))) > 1e-6
                  for k in ("cold", "fever", "insomnia", "loneliness", "wounded",
                            "guard", "irritability", "burnout"))
    check("状态有变化", changed, {"wounded": after.get("wounded"), "guard": after.get("guard")})
    check("落盘存在", os.path.isfile(v._STATE_PATH), v._STATE_PATH)
    if os.path.isfile(v._STATE_PATH):
        disk = json.load(open(v._STATE_PATH, encoding="utf-8"))
        check("盘上的值一致", abs(float(disk.get("burnout", 0)) - float(after.get("burnout", 0))) < 1e-6,
              disk.get("burnout"))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
