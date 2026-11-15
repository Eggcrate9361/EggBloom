# -*- coding: utf-8 -*-
"""check_all.py 的自检：确认「汇总器」不会把失败/跳过说成全绿。

为什么写它：check_all.py 是自检的统一入口，它自己判错就是新的静默源 ——
典型错法是把子脚本的退出码 2（依赖缺失、没真跑）当成 PASS，人就会以为全绿。
做法：把 CHECKS 换成临时假脚本（固定退出码）再调 main()，不碰真脚本。

用法：python tests/test_check_all.py
"""
import importlib.util
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # tests/ 上跳一层就是仓库根
CHK = os.path.join(ROOT, "scripts", "check_all.py")
FAILED = []


def load():
    spec = importlib.util.spec_from_file_location("check_all", CHK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake(mod, name, code):
    """把 CHECKS 换成单个固定退出码的假脚本项。"""
    d = tempfile.mkdtemp(prefix="ca-")
    p = os.path.join(d, name + ".py")
    io_open(p, "import sys\nsys.exit(%d)\n" % code)
    mod.CHECKS = [(name, p, [], name, False)]
    return mod


def io_open(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def case(name, cond, out=""):
    if cond:
        print("[OK]   " + name)
    else:
        print("[FAIL] " + name)
        FAILED.append(name)
        if out:
            print("       " + str(out).strip()[-1200:])


def run_case(name, code, argv=()):
    """在独立模块实例上跑 main()，返回 (退出码, 全部 stdout)。"""
    import contextlib
    import io
    mod = fake(load(), name, code)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = mod.main(list(argv))
        except SystemExit as e:          # --help / 不认识的项 会 sys.exit
            rc = e.code
    return rc, buf.getvalue()


def main():
    import contextlib
    import io

    rc, out = run_case("a", 0)
    case("全通过时退出码 0", rc == 0 and "全绿" in out, out)

    rc, out = run_case("a", 1)
    case("有失败时退出码 1，点名该项且不说全绿",
         rc == 1 and "[FAIL] a" in out and "全绿" not in out, out)

    # ★ 核心：退出码 2 是「没跑成/没跑全」，绝不能被当成通过
    rc, out = run_case("a", 2)
    case("退出码 2 记为 SKIP、整体 2，不当全绿",
         rc == 2 and "SKIP" in out and "全绿" not in out, out)

    rc, out = run_case("a", 0, ["--fast"])
    case("--fast 下静态项照跑（本项非浏览器项）", rc == 0 and "PASS" in out, out)

    # --list 走真实清单（不替换 CHECKS）：每一项都要列出来。
    # 不写死条数 —— 加一项就改一次测试是白耗；只要求 ≥5 且清单里的名字一个不漏。
    mod = load()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(["--list"])
    out = buf.getvalue()
    want = [c[0] for c in load().CHECKS]
    case("--list 列出全部真实分项且退出码 0",
         rc == 0 and len(want) >= 5 and all(n in out for n in want), out)

    rc, out = run_case("a", 0, ["-k", "nope"])
    case("不认识的项名退出码 2 且给出提示", rc == 2 and "不认识的项" in out, out)

    # 子脚本文件不存在 -> MISS + 1（不是静默跳过）
    mod = load()
    mod.CHECKS = [("gone", "no_such_script_xyz.py", [], "缺失", False)]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main([])
    out = buf.getvalue()
    case("子脚本缺失记为 MISS 且退出码 1", rc == 1 and "MISS" in out, out)

    # 子脚本超时/起不来 -> FAIL（这里用不存在解释器模拟起不来）
    mod = load()
    mod.PY = "/nonexistent/python-xyz"
    mod.CHECKS = [("boom", "check_all.py", [], "起不来", False)]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main([])
    out = buf.getvalue()
    case("解释器起不来记为 FAIL 且退出码 1", rc == 1 and "起不来" in out, out)

    print("-" * 60)
    if FAILED:
        print("失败 %d 条：%s" % (len(FAILED), "、".join(FAILED)))
        return 1
    print("自检全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
