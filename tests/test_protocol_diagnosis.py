# -*- coding: utf-8 -*-
"""tests/test_protocol_diagnosis.py —— 协议页启动要给真结论（2026-11-05）

## 现象

协议页点「启动协议层」，界面显示「协议层已启动」，但 3000/3001 一个都没监听，
消息一条收不到。原因是 EggBloom 以普通权限拉起 SnowLuma，注入 QQ（高完整性）
时**不报错、静默跳过** —— 界面却照报成功。

## 判据

① `/api/protocol/start` 返回的是**链路结论**，不是"已启动"四个字
② 三种失败形态要说清楚：没注入 / 取不到登录号 / 进程没起来
③ 幂等分支（已在运行）也要给结论，不能只说"未重复启动"
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

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


def main():
    src = open(os.path.join(ROOT, "webui", "webui_server.py"),
               encoding="utf-8", errors="replace").read()

    print("[A] 有诊断函数，且 start 分支在用它")
    check("★ 定义了 _protocol_start_diagnosis", "def _protocol_start_diagnosis(" in src)
    check("★ start 返回诊断结果", '_protocol_start_diagnosis()' in src)
    check("★ 幂等分支也有结论", '_protocol_start_diagnosis(3.0)' in src)
    check("不再只回'已启动'", '"output": "协议层已启动"}' not in src)

    print("\n[B] 三种失败形态都说人话")
    for kw, why in (

        ("以管理员启动", "给出可执行的下一步（本页按钮，不是外部脚本）"),
        ("没有注入 QQ", "说明为什么注入不了"),
        ("取不到登录号", "OneBot 通了但没登录"),
        ("协议层进程没起来", "进程都没起来"),
    ):
        check(f"★ 提到「{kw}」", kw in src, why)

    print("\n[C] 诊断真的会去查端口与登录号")
    i = src.find("def _protocol_start_diagnosis(")
    blk = src[i:i + 2000]
    check("查 3000", "_port_ok(3000)" in blk)
    check("查 5099", "_port_ok(5099)" in blk)
    check("调 get_login_info", "get_login_info" in blk)
    check("有等待上限（不会卡死界面）", "deadline" in blk)

    print("\n[E] 以管理员启动（QQ 为 High 完整性时的正路）")
    check("★ 有 /api/protocol/start_elevated", "/api/protocol/start_elevated" in src)
    check("★ 用 Start-Process -Verb RunAs", "-Verb RunAs" in src)
    check("★ 起在运行时目录", "-WorkingDirectory" in src)
    check("★ 只启动不连接（连接交给 start）",
          "_launch_snowluma_elevated" in src and "只启动进程" in src)
    html2 = open(os.path.join(ROOT, "webui", "webui_artistic_v4.html"),
                 encoding="utf-8", errors="replace").read()
    check("★ 页面有「以管理员启动」按钮", "pStartAdmin" in html2 and "以管理员启动" in html2)
    check("页面会提示 UAC", "UAC 弹窗点" in html2)
    check("★ 提权后自动连一次", "await new Promise(res => setTimeout(res, 15000))" in html2
          or "setTimeout(res, 15000)" in html2)

    print("\n[D] 前端把结论显示出来")
    html = open(os.path.join(ROOT, "webui", "webui_artistic_v4.html"),
                encoding="utf-8", errors="replace").read()
    check("启动按钮展示 output", "say(!!r.success, r.output" in html)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
