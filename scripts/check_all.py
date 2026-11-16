# -*- coding: utf-8 -*-
"""一条命令跑完「改完必跑」的自检（2026-11-09）。

为什么有它
----------
这些检查原来散在 5 个脚本里，全靠人记得逐个敲。而这条链上的失败几乎全是静默的：
内联 script 语法坏了整块顶层函数不执行（报错还指向不相干的行）、字段链漏一步
「恢复默认」按钮整片消失、v3→v4 页面对等退化页面照样渲染得挺好看。所以合成一条：
分项跑 -> 逐项给结论 -> 末尾一行汇总 + 非 0 退出码。

分项（顺序即依赖顺序：纯静态 -> 结构 -> 浏览器）
  1. js_syntax         webui_artistic_v4.html 内联 <script> 语法（要 node）
  2. fieldmeta_chain   字段链三边：v4 / _fieldmeta.json / DEFAULT_CONFIG（只读）
  3. secrets           敏感信息扫描（默认口径：已跟踪 + 未跟踪，跳过 ignored）
     secrets_rules     基础设施判据自检（ssh/IP/家目录，11 条夹具）
     history           历史闸：所有非归档 ref 的 blob + 提交信息 + tag 附注
     history_rules     历史闸自检（临时仓库，钉住 --batch 对 tree 的解析）
     package_rules     发行包闸自检（内容/结构/溯源，造坏包断言能抓）
     surface           对外可见面验收（包闸会拦、历史闸会拦、判据只有一份）
     hooks             三个提交钩子已安装、与 .githooks/ 副本一致
  4. page_parity       v3 -> v4 页面功能对等（13 页）
  5. config_parity     配置中心逐项验收：静态两项 + 浏览器项（要 playwright）
  6. drive_tokens      drive.tick 的 token 记账（假 agent_service，不跑模型）
  7. hotrestart        热重载 / 自重启插件的动作与平台闸（不真重启）

用法
----
**用虚拟环境的解释器跑，别用系统 python3。** 本脚本用 sys.executable 起子脚本，
子项看到的就是你调它的那个解释器：系统 python3 里没有 playwright，config_parity
会直接 SKIP（退出码 2），看起来像环境坏了，其实是调错了入口。

    .venv/bin/python scripts/check_all.py             # Linux / macOS，全跑
    .venv\\Scripts\\python.exe scripts\\check_all.py   # Windows，全跑
    <解释器> scripts/check_all.py -k js_syntax        # 只跑某一项（可重复）
    <解释器> scripts/check_all.py --list              # 看分项名
    <解释器> scripts/check_all.py --fast              # 跳过浏览器项

退出码
------
    0 = 全通过（每一项都真跑了且过了）
    1 = 有失败（某项真跑了但没过，或子脚本缺失 / 解释器起不来）
    2 = 有跳过 —— 依赖缺失（没 node / 没 playwright）或你自己加了 --fast，
        该项**没跑**。跳过不算通过，发布前必须补依赖跑到 0。
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable or "python"

#: (分项名, 脚本, 额外参数, 说明, 是否浏览器项)
CHECKS = [
    ("js_syntax", "check_js_syntax.py", [], "内联 script 语法", False),
    ("fieldmeta_chain", "check_fieldmeta_chain.py", [], "字段链三边一致（只读）", False),
    ("secrets", "check_secrets.py", [], "敏感信息扫描", False),
    ("secrets_rules", "check_secrets.py", ["--selftest"], "基础设施判据自检（ssh/IP/家目录）", False),
    ("history", "check_history.py", [], "历史闸：所有非归档 ref 的 blob + 提交信息", False),
    ("history_rules", "check_history.py", ["--selftest"], "历史闸自检（blob 解析对齐）", False),
    ("package_rules", "check_package.py", ["--selftest"], "发行包闸自检（内容/结构/溯源）", False),
    ("surface", "tests/test_release_surface.py", [], "对外可见面：包/历史/判据单一来源", False),
    ("hooks", "install_git_hooks.py", ["--check"], "提交钩子已装且与版本化副本一致", False),
    ("identity", "tests/test_identity_numbers.py", [], "身份号判据 + 工作树 0 命中", False),
    ("page_parity", "audit_page_parity.py", [], "页面功能对等 v3->v4", False),
    ("config_parity", "audit_config_parity.py", [], "配置中心逐项验收", True),
    ("drive_tokens", "tests/test_drive_tokens.py", [], "长期主动的 token 记账", False),
    ("hotrestart", "tests/test_hotrestart.py", [], "重启/重载插件行为", False),
]

#: 2 = 「没跑成/没跑全」（依赖缺失、只跑了静态项），不算真失败，单独标 SKIP
SKIP_CODES = {2}


def _parse(argv):
    """极简参数解析：-k 可重复，其余按标志处理。不引 argparse 保持可移植。"""
    names, fast = [], False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-k", "--only") and i + 1 < len(argv):
            names.append(argv[i + 1])
            i += 2
            continue
        if a.startswith("--only="):
            names.append(a.split("=", 1)[1])
        elif a == "--fast":
            fast = True
        elif a in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        i += 1
    known = {c[0] for c in CHECKS}
    if names:
        bad = [n for n in names if n not in known]
        if bad:
            print("[!] 不认识的项：%s（--list 看可选项）" % "、".join(bad))
            sys.exit(2)
    return (set(names) or known), fast


def _run(name, script, extra, desc, env):
    """跑一个子脚本，原样转发输出。返回 (状态, 耗时, 备注)。"""
    # 带 `/` 的按仓库根解析（tests/… 下的测试），否则按 scripts/ 解析；绝对路径原样用。
    if os.path.isabs(script):
        path = script
    else:
        path = os.path.join(ROOT if "/" in script else HERE, script)
    if not os.path.exists(path):
        return "MISS", 0.0, "脚本不存在：" + script
    print("\n" + "=" * 70)
    print("> %s —— %s（%s）" % (name, desc, script))
    print("=" * 70)
    t0 = time.time()
    try:
        r = subprocess.run([PY, path] + list(extra), cwd=ROOT, env=env,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
        out, code = (r.stdout or "") + (r.stderr or ""), r.returncode
    except subprocess.TimeoutExpired:
        out, code = "[!] 超时（>900s）", 1
    except Exception as e:  # 起不来也算失败，别静默
        out, code = "[!] 起不来：%r" % (e,), 1
    dt = time.time() - t0
    for ln in out.rstrip().splitlines():      # 缩进两位，标明归属
        print("  " + ln)
    if code == 0:
        return "PASS", dt, ""
    if code in SKIP_CODES:
        return "SKIP", dt, "退出码 %d（依赖缺失 / 只跑了一部分）" % code
    return "FAIL", dt, "退出码 %d" % code


def main(argv):
    if "--list" in argv:
        for name, script, extra, desc, browser in CHECKS:
            print("  %-16s %-28s %s%s" % (name, script, desc,
                                          "（浏览器）" if browser else ""))
        return 0

    want, fast = _parse(argv)
    env = dict(os.environ)
    # 子脚本以 `python scripts/xxx.py` 跑时 sys.path[0] 是 scripts/，
    # 个别脚本要 import config（项目根），统一补上免得逐个踩。
    env["PYTHONPATH"] = ROOT + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"

    rows = []
    t_all = time.time()
    for name, script, extra, desc, browser in CHECKS:
        if name not in want:
            continue
        if fast and browser:
            rows.append((name, "SKIP", 0.0, "--fast 跳过"))
            continue
        rows.append((name,) + _run(name, script, extra, desc, env))

    dt_all = time.time() - t_all
    print("\n" + "=" * 70)
    print("汇总")
    print("=" * 70)
    for name, state, dt, note in rows:
        print("  %-16s %-5s %6.1fs  %s" % (name, state, dt, note))
    bad = [r for r in rows if r[1] in ("FAIL", "MISS")]
    skip = [r for r in rows if r[1] == "SKIP"]
    print("-" * 70)
    print("共 %d 项：通过 %d / 失败 %d / 跳过 %d，用时 %.1fs"
          % (len(rows), len(rows) - len(bad) - len(skip), len(bad), len(skip), dt_all))
    if bad:
        print("[FAIL] " + "、".join(r[0] for r in bad))
        return 1
    if skip:
        print("[SKIP] " + "、".join(r[0] for r in skip) + " —— 不算通过，把依赖补齐再跑")
        return 2
    print("[OK] 全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
