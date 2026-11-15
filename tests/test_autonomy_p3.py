# -*- coding: utf-8 -*-
"""1.6.0 P3 · 自主开发层归位：判据唯一、心跳裁决、名字冲突防线。

钉死五件事：

  A. 账本实现物理落在 `autonomy/ledger.py`，老路径是纯转发壳（含私有纯函数）
  B. 策略判据只有一份：按文件加载的 `plugins/autonomy/policy.py` 与
     `autonomy/policy.py` 给出同一张等级表、同一套 guard
  C. `plugins/autonomy/` 不能有 `__init__.py`；即便把 plugins/ 插到
     sys.path 最前，`import autonomy` 仍解析到根下自主层（否则主链拿错包）
  D. `plugin_manager.discover()` 显式跳过该目录，plugin.json 份数口径不变
  E. `autonomy/heartbeat.py` 纯函数链路：材料只放事实，say/do 都过 guard，
     L3 未确认返回 confirm，未知动作按 L3 从严，解析失败一律 none

只读：不改文件、不注册工具、不跑分离态启用。
运行：python tests/test_autonomy_p3.py
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from autonomy import heartbeat, ledger, policy  # noqa: E402
from harness.proactivity import ledger as old_ledger  # noqa: E402
from plugins import plugin_manager as pm  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))


ROOT_P = Path(ROOT)
POLICY_SHIM = ROOT_P / "plugins" / "autonomy" / "policy.py"

print("\n[A] 账本归位与老路径兼容")
check("实现文件在 autonomy/ledger.py", (ROOT_P / "autonomy" / "ledger.py").is_file())
check("老路径是转发壳", "from autonomy import ledger as _impl"
      in (ROOT_P / "harness" / "proactivity" / "ledger.py").read_text(encoding="utf-8"))
check("老路径 scan 与实现是同一个函数", old_ledger.scan is ledger.scan)
check("老路径保留私有纯函数 _job_mode", callable(getattr(old_ledger, "_job_mode", None)))
check("老路径能读到实现里的常量", old_ledger.BUBBLE_WINDOW_S == ledger.BUBBLE_WINDOW_S)

print("\n[B] 策略判据唯一")
spec = importlib.util.spec_from_file_location("eb_policy_shim_probe", str(POLICY_SHIM))
shim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shim)
check("按文件加载的壳有 guard", callable(getattr(shim, "guard", None)))
check("等级表同源", shim.LEVELS == policy.LEVELS)
check("裁决同源", shim.guard("msg.send", 2) == policy.guard("msg.send", 2))
check("未登记动作按 L3 拒绝", shim.guard("no.such.action", 3)["verdict"] == "deny")

print("\n[C] 顶层包名字冲突防线")
check("plugins/autonomy 没有 __init__.py",
      not (ROOT_P / "plugins" / "autonomy" / "__init__.py").exists())
probe = ("import sys, os; "
         "sys.path.insert(0, os.path.join(os.getcwd(), 'plugins')); "
         "import autonomy; print(autonomy.__file__)")
r = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True,
                   text=True, timeout=30)
got = (r.stdout or "").strip().splitlines()
resolved = got[-1] if got else ""
check("plugins/ 优先时 import autonomy 仍指向根下包",
      resolved.replace("\\", "/").endswith("/autonomy/__init__.py"), resolved)

print("\n[D] 插件扫描器显式跳过策略目录")
check("discover 有 NON_PLUGIN_PACKAGES", "autonomy" in pm.PluginManager.NON_PLUGIN_PACKAGES)
m = pm.PluginManager.__new__(pm.PluginManager)
check("discover() 不含 autonomy", "autonomy" not in m.discover(), m.discover())
check("plugin.json 仍是 27 份",
      len(list((ROOT_P / "plugins").glob("*/plugin.json"))) == 27)
# 2026-11-13：25 → 27，加的是 plugins/memewiki（梗百科）与 plugins/hotrestart（热重载）。
# 这条断言的本意是「搬 autonomy 没顺手多认或少认一个插件」，
# 所以是显式钉数、不是动态统计 —— 加插件就要在这里改一次，是预期成本。

print("\n[E] 心跳纯函数链路")
now = time.time()
mat = heartbeat.materials_from_state(
    {"last_user_ts_by": {"123": now - 7200},
     "pending_topics": {"123": "作业写完了吗"}},
    "123", now=now)
check("材料带惦记的事", mat.get("pending") == "作业写完了吗", mat)
check("材料带沉默时长", "小时" in str(mat.get("silent_for")), mat)
check("材料不带结论词", not any(w in str(mat) for w in ("应该", "建议", "要不要动")))
prompt = heartbeat.build_prompt(mat, 2)
check("提示词带等级 L2", "L2" in prompt)
check("提示词是心跳那一轮", "心跳" in prompt)

check("say 在 L2 放行", heartbeat.evaluate('{"act":"say","text":"在吗"}', level=2)["act"] == "say")
check("say 在 L1 被拒", heartbeat.evaluate('{"act":"say","text":"在吗"}', level=1)["act"] == "none")
r3 = heartbeat.evaluate('{"act":"do","action":"pay"}', level=3)
check("L3 未确认 → confirm", r3["act"] == "confirm" and r3["verdict"] == "need_confirm", r3)
r3b = heartbeat.evaluate('{"act":"do","action":"pay"}', level=3, confirmed=True)
check("L3 确认后 → do", r3b["act"] == "do", r3b)
r4 = heartbeat.evaluate('{"act":"do","action":"not.registered"}', level=3)
check("未登记动作 deny", r4["act"] == "none" and r4["verdict"] == "deny", r4)
r5 = heartbeat.evaluate("这里没有 JSON")
check("解析失败一律 none", r5["act"] == "none" and bool(r5["error"]), r5)
check("evaluate 不写盘不发送", "applied" not in r5 and "sent" not in r5)

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
