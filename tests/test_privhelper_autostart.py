# -*- coding: utf-8 -*-
"""用例：高权限助手必须开机 / 登录自启（P0-1，2026-11-05）。

背景：install_privhelper.ps1 原先 Register-ScheduledTask 没传 -Trigger，
任务 Triggers 为空 —— 今天只能手工 Start-ScheduledTask，每重启一次就要救一次。

这里只钉住**安装脚本的配置**（离线可跑）；真机是否真的注册上，
另看 Export-ScheduledTask 导出的任务 XML（验收：Triggers 非空 + RestartCount 3）。
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

SCRIPT = os.path.join(ROOT, "scripts", "install_privhelper.ps1")

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
    with io.open(SCRIPT, encoding="utf-8-sig", errors="replace") as f:
        src = f.read()
    # 去掉整行注释再断言：注释里提到 "-Trigger" 不该算命中
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))

    print("[A] 触发器定义")
    check("★ 有 $triggers 数组", "$triggers = @(" in code)
    check("★ 开机触发 AtStartup", "New-ScheduledTaskTrigger -AtStartup" in code)
    check("★ 登录触发 AtLogOn", "New-ScheduledTaskTrigger -AtLogOn" in code)

    print("\n[B] 注册时真的传进去了")
    i = code.find("Register-ScheduledTask")
    reg = code[i:code.find("Start-ScheduledTask", i)] if i >= 0 else ""
    check("★ Register-ScheduledTask 带 -Trigger $triggers",
          "-Trigger $triggers" in reg, reg[:120])
    check("★ 身份仍是 SYSTEM / Highest",
          'New-ScheduledTaskPrincipal -UserId "SYSTEM"' in code
          and "-RunLevel Highest" in code)

    print("\n[C] 失败与错过触发的兜底")
    check("★ RestartCount 3", "-RestartCount 3" in code)
    check("★ StartWhenAvailable", "-StartWhenAvailable" in code)
    check("★ MultipleInstances 不重复起", "IgnoreNew" in code
          or "-MultipleInstances IgnoreNew" in code)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
