# -*- coding: utf-8 -*-
"""用例：诊断文案必须给出**可执行**的下一步（2026-11-05）。

三种环境：
  · UAC 关 + 非管理员 → 关闭 EggBloom，以管理员身份启动
  · UAC 关 + 已是管理员 → 看日志（QQ 版本）
  · UAC 开 → 点本页「以管理员启动」
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import webui.webui_server as ws  # noqa: E402


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


def hint(uac_disabled, elevated):
    old_u, old_e = ws._uac_disabled, ws._self_is_elevated
    try:
        ws._uac_disabled = lambda: uac_disabled
        ws._self_is_elevated = lambda: elevated
        return ws._no_injection_hint()
    finally:
        ws._uac_disabled, ws._self_is_elevated = old_u, old_e


def main():
    print("[A] UAC 关 + 非管理员：必须让人关了应用再管理员启动")
    h = hint(True, False)
    check("★ 说「关闭 EggBloom」", "关闭 EggBloom" in h, h[:60])
    check("★ 说「以管理员身份启动」", "以管理员身份启动" in h)
    check("★ 说明为什么（UAC 关闭）", "EnableLUA" in h or "UAC" in h)
    check("不再叫人点按钮（点了也没用）", "以管理员启动」" not in h)

    print("\n[B] UAC 关 + 已是管理员：让去看日志")
    h2 = hint(True, True)
    check("★ 提到日志 [Hook]", "[Hook]" in h2, h2[:60])
    check("不再让人提权（已经提了）", "关闭 EggBloom" not in h2)

    print("\n[C] UAC 开：指本页按钮")
    h3 = hint(False, False)
    check("★ 指「以管理员启动」按钮", "以管理员启动" in h3, h3[:60])
    check("说明会弹 UAC", "UAC" in h3)

    print("\n[D] 提权入口也拦（UAC 关时别说『已请求提权』）")
    src = io.open(os.path.join(ROOT, "webui", "webui_server.py"),
                  encoding="utf-8", errors="replace").read()
    i = src.find("def _launch_snowluma_elevated(")
    # 2026-11-05：优先走高权限助手，UAC 变成兜底 —— 窗口要够长才扫得到
    blk = src[i:i + 5200]
    check("★ 入口里判 UAC（兜底）", "_uac_disabled()" in blk)
    check("★ 关闭 EggBloom 提示还在（助手不可用时）", "关闭 EggBloom" in blk)
    check("★ 优先走高权限助手", "spawn_session" in blk)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
