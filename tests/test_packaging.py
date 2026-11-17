# -*- coding: utf-8 -*-
"""tests/test_packaging.py —— 发行暂存树不能漏东西（2026-11-06）。

`sync-dist.ps1` 只按 `$dirItems` 整目录搬；`separation/` / `autonomy/`
不在 rootExts 里，漏一行源码全对、发行包里却是空的。

另有一类更隐蔽的失败（2026-11-14）：该脚本是含中文的 UTF-8 .ps1，
PowerShell 5.1 对无 BOM 的文件按系统 ANSI 解码，解析器直接报一堆
「缺少右 }」而拒绝执行。同步没发生，7-Zip 却照常压缩上一版暂存树。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

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
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


def main():
    p = os.path.join(ROOT, "scripts", "sync-dist.ps1")
    raw = open(p, "rb").read()
    check("sync-dist.ps1 带 UTF-8 BOM（5.1 否则按 ANSI 解码）",
          raw.startswith(b"\xef\xbb\xbf"), f"head={raw[:3]!r}")
    text = raw.decode("utf-8-sig")
    m = re.search(r"\$dirItems\s*=\s*@\((.*?)\)", text, re.S)
    block = m.group(1) if m else ""
    check("dirItems 含 separation", '"separation"' in block, block[:200])
    check("dirItems 含 autonomy", '"autonomy"' in block, block[:200])
    check("separation 包存在", os.path.isdir(os.path.join(ROOT, "separation")))
    check("autonomy 包存在", os.path.isdir(os.path.join(ROOT, "autonomy")))

    # 发行包运行时不应带上 pending/lock 之类本机状态（它们不进源码库）
    check("lock 不按目录清单搬", "SEPARATION.lock" not in block)
    check("rootAlways 带 lock", '"SEPARATION.lock"' in text)
    check("rootAlways 带 archive", '"constraints.archive"' in text)
    ex_m = re.search(r"\$excludeFiles\s*=\s*@\((.*?)\n\)", text, re.S)
    ex_block = ex_m.group(1) if ex_m else ""
    check("★ 不用源码空表情索引覆盖运行库 tags.json",
          '"tags.json"' in ex_block, ex_block[-500:])
    check("★ 不用源码描述桩覆盖运行库 descriptions.json",
          '"descriptions.json"' in ex_block, ex_block[-500:])

    print("=" * 58)
    if FAIL:
        print(f"失败 {FAIL} 条：")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print(f"通过 {PASS} / 失败 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
