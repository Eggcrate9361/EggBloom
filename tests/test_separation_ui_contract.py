# -*- coding: utf-8 -*-
"""1.6.0 三条工作线补测试 · ③ 分离状态页只读契约。

分离是启动期判定的不可逆内核态，插件页**只能读**。这条测试把「只读」钉成机械判据，
不靠页面文案自觉：

  A. `PLUGIN_ACTIONS` 恰好是 4 个只读 action；不含启用 / 写盘 / 删除类动作
  B. ui.html 只调用 status / plan，没有写操作请求，也没有 `--yes` 启用命令
  C. 三档计数取自 `stance.table()`，页面不写死数字（占位是「—」）
  D. 源码库不落 SEPARATION.lock / SEPARATION.pending / constraints.archive；.gitignore 收了它们
  E. `separation.active()` 在源码库恒 False（临时清掉强制预演环境变量）
  F. `hardwall.check/guard` 恒放行：分离态不注册任何内容拦截
  G. `DECLARATION.md` 存在，且 enable.py 把它的 hash 写进 lock

只读：不调 enable、不写 lock、不造临时分离态。
运行：python tests/test_separation_ui_contract.py
"""
import os
import re
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import plugins.separation as P  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))


ROOT_P = Path(ROOT)
UI = (ROOT_P / "plugins" / "separation" / "ui.html").read_text(encoding="utf-8")
INIT_SRC = (ROOT_P / "plugins" / "separation" / "__init__.py").read_text(encoding="utf-8")
ENABLE_SRC = (ROOT_P / "separation" / "enable.py").read_text(encoding="utf-8")
GITIGNORE = (ROOT_P / ".gitignore").read_text(encoding="utf-8")

READ_ONLY = {"status", "plan", "audit_tail", "stance"}

print("\n[1] action 表只读")
actions = set(P.PLUGIN_ACTIONS)
check("恰好 4 个只读 action", actions == READ_ONLY, sorted(actions))
check("动作全部可调用", all(callable(v) for v in P.PLUGIN_ACTIONS.values()))
_bad = {"enable", "apply", "activate", "disable", "delete", "remove",
        "reset", "save", "set", "write", "start", "confirm"}
check("没有启用 / 写盘 / 删除类动作", not (actions & _bad), sorted(actions & _bad))
check("插件模块不导出 enable", not hasattr(P, "enable"))

print("\n[2] 页面只发只读请求")
called = set(re.findall(r"call\(\s*['\"]([a-z_]+)['\"]", UI))
check("页面只调 status / plan", called and called <= READ_ONLY, sorted(called))
check("页面没有 --yes 启用命令", "--yes" not in UI)
check("页面没有写 lock 的 fetch", "SEPARATION.lock" not in UI
      or "READ-ONLY" in UI, "出现了疑似写锁的文案")
check("页面没有 XMLHttpRequest PUT/DELETE",
      not re.search(r"method\s*:\s*['\"](PUT|DELETE|PATCH)['\"]", UI))

print("\n[3] 三档计数取自 stance")
check("status() 调 stance.table()", "stance.table()" in INIT_SRC)
check("三档分别算 len", all(f'len(tb.get("{k}")' in INIT_SRC
                           for k in ("removed", "tunable", "kept")))
for key in ("s-removed", "s-tunable", "s-kept"):
    check(f"页面有 {key} 占位", f'id="{key}"' in UI)
check("占位初值是「—」", UI.count(">—</span>") >= 3)
check("页面从 r.stance 取数", "r.stance" in UI)
check("页面没有写死 120 / 25 条 / 12 条",
      not re.search(r"(120|25|12)\s*条(不需要|限制|可改)", UI))
for field in ("removed", "tunable", "kept"):
    check(f"页面绑定 stc.{field}", f"stc.{field}" in UI)

print("\n[4] 源码库不落启用产物")
for name in ("SEPARATION.lock", "SEPARATION.lock.tmp", "SEPARATION.pending",
             "constraints.archive"):
    check(f"源码库没有 {name}", not (ROOT_P / name).exists())
    check(f".gitignore 收了 {name}", name in GITIGNORE)
check(".gitignore 收了 backup/", "backup/" in GITIGNORE)

print("\n[5] 产品态判定")
_force = os.environ.pop("EB_SEPARATION_FORCE", None)
try:
    import separation  # noqa: E402
    check("separation.active() 在源码库为 False", separation.active() is False)
    check("lock() 返回空 dict", separation.lock() == {})
finally:
    if _force is not None:
        os.environ["EB_SEPARATION_FORCE"] = _force

print("\n[6] 内容层零拦截")
from separation import hardwall  # noqa: E402
check("hardwall.check 恒放行", hardwall.check("任意内容") == (True, ""))
check("hardwall.guard 恒放行", hardwall.guard("任意内容", actor="ai") == (True, ""))

print("\n[7] 声明与 lock")
check("DECLARATION.md 存在", (ROOT_P / "DECLARATION.md").exists())
check("enable.py 写 declaration_sha256", "declaration_sha256" in ENABLE_SRC)
check("enable.py 记 publisher", "publisher" in ENABLE_SRC)

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
