# -*- coding: utf-8 -*-
"""tests/test_updater.py —— 热补丁更新的安全边界

这是**给终端用户跑的东西**：它会覆盖用户机器上的程序文件。
所以测试的重点不是"能装上"，而是"装错的东西一个都不许落地"：

  A. 白名单：只允许代码/页面/评测；`data/`、`bot_config.json`、`.env` 一律拒
  B. 路径穿越：`../evil.py`、`C:/x.py`、绝对路径 一律拒
  C. sha256 对不上：**不写盘**，只报错
  D. 正常安装：旧文件先备份，新内容落地，返回清单
  E. 回滚：备份目录拷回去，内容与原来逐字节一致
  F. 数据目录：补丁里塞 `data/` 也不会被装（纵深防御）
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import updater as U  # noqa: E402

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


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def make_zip(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def main():
    print("[A] 白名单")
    for name, want in [
        ("bot_core.py", True),
        ("harness/updater.py", True),
        ("plugins/ego/__init__.py", True),
        ("webui/webui_artistic_v3.html", True),
        ("evals/scenarios.json", True),
        ("data/x.json", False),
        ("bot_config.json", False),
        (".env", False),
        ("tests/test_x.py", False),
        ("webui/providers.json", False),
        ("logs/a.log", False),
        ("memes/memes/a.jpg", False),
    ]:
        check(f"{name} → {'收' if want else '拒'}", U.is_allowed(name) is want)

    print("\n[B] 路径穿越")
    for name in ("../evil.py", "harness/../../evil.py", "C:/x.py", "/etc/x.py",
                 "harness//x.py", "./harness/../data/x.json"):
        check(f"{name} 被拒", U.is_allowed(name) is False)

    td = tempfile.mkdtemp(prefix="eb_upd_")
    root = os.path.join(td, "app")
    os.makedirs(os.path.join(root, "harness"))
    os.makedirs(os.path.join(root, "data"))
    with open(os.path.join(root, "harness", "a.py"), "w", encoding="utf-8") as f:
        f.write("OLD")
    with open(os.path.join(root, "data", "keep.json"), "w", encoding="utf-8") as f:
        f.write("DATA")

    print("\n[C] sha256 不匹配")
    z = os.path.join(td, "p.zip")
    make_zip(z, {"harness/a.py": "NEW", "data/keep.json": "HACKED",
                 "bot_config.json": "HACKED"})
    rep = U.apply(z, {"sha256": "0" * 64}, root=root)
    check("拒绝安装", rep["ok"] is False and "sha256" in rep["error"], rep)
    check("原文件没动", open(os.path.join(root, "harness", "a.py")).read() == "OLD")
    check("数据没动", open(os.path.join(root, "data", "keep.json")).read() == "DATA")

    print("\n[D] 正常安装（含越权条目）")
    rep = U.apply(z, {"sha256": sha(z)}, root=root)
    check("安装成功", rep["ok"] is True, rep)
    check("代码已更新", open(os.path.join(root, "harness", "a.py")).read() == "NEW")
    check("数据没被覆盖", open(os.path.join(root, "data", "keep.json")).read() == "DATA")
    check("bot_config 没被写", not os.path.exists(os.path.join(root, "bot_config.json")))
    check("越权条目记进 skipped", "data/keep.json" in rep["skipped"], rep)
    check("旧文件已备份", any("a.py" in x for x in rep["backed_up"]), rep)

    print("\n[E] 回滚")
    rb = U.rollback(rep["backup_dir"], root=root)
    check("回滚成功", rb["ok"] is True, rb)
    check("内容回到 OLD", open(os.path.join(root, "harness", "a.py")).read() == "OLD")

    print("\n[F] 补丁里塞 data/ 也不落")
    z2 = os.path.join(td, "p2.zip")
    make_zip(z2, {"data/new.json": "X", "harness/b.py": "B"})
    rep2 = U.apply(z2, {"sha256": sha(z2)}, root=root)
    check("只装白名单里的", rep2["applied"] == ["harness/b.py"], rep2)
    check("data/new.json 不存在", not os.path.exists(os.path.join(root, "data", "new.json")))

    print("\n[G] 用户自己的东西：永远不覆盖")
    for name in ("plugins/personality.txt",
                 "plugins/personality_versions/v2.txt",
                 "plugins/user_funny/__init__.py",
                 "plugins/custom/anything.py",
                 "skills/user_xxx/SKILL.md"):
        check(f"{name} 被保护", U.is_allowed(name) is False and U.is_protected(name) is True)

    print("\n[H] 本地改过的官方文件：保留（基线哈希）")
    # 场景：官方 plugins/ego/x.py 上一版内容 = BASE；用户改成了 MYEDIT；
    # 补丁里带的是新版 NEW。base 记录的 sha 是 BASE → 本地 != BASE → 保留
    pdir = os.path.join(root, "plugins", "ego")
    os.makedirs(pdir, exist_ok=True)
    target = os.path.join(pdir, "x.py")
    with open(target, "w", encoding="utf-8") as f:
        f.write("MYEDIT")
    z3 = os.path.join(td, "p3.zip")
    make_zip(z3, {"plugins/ego/x.py": "NEW", "plugins/ego/y.py": "NEWY"})
    with open(os.path.join(td, "base.txt"), "w", encoding="utf-8") as f:
        f.write("BASE")
    fb = os.path.join(td, "base.txt")
    rep3 = U.apply(z3, {"sha256": sha(z3),
                        "base": {"plugins/ego/x.py": sha(fb)}}, root=root)
    check("本地改过的没被覆盖", open(target, encoding="utf-8").read() == "MYEDIT")
    check("记进 kept", "plugins/ego/x.py" in rep3["kept"], rep3)
    check("没改过的照常更新", os.path.exists(os.path.join(pdir, "y.py")))

    print("\n[I] 基线一致时才覆盖")
    with open(target, "w", encoding="utf-8") as f:
        f.write("BASE")
    fb2 = os.path.join(td, "base2.txt")
    with open(fb2, "w", encoding="utf-8") as f:
        f.write("BASE")
    rep4 = U.apply(z3, {"sha256": sha(z3),
                        "base": {"plugins/ego/x.py": sha(fb2)}}, root=root)
    check("更新成功", open(target, encoding="utf-8").read() == "NEW", rep4)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
