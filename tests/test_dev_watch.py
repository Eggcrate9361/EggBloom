# -*- coding: utf-8 -*-
"""tests/test_dev_watch.py —— scripts/dev_watch.py 的回归测试

只测**纯函数**（不杀进程、不起服务）：
  · is_watched        哪些文件改了需要重启后端
  · snapshot/changed  快照与差异（含新增/修改/删除）
  · backend_processes 只认本发行包 app 目录下的 webui_server.py
  · read_port         last_port.txt 解析与回退

为什么值得单测：这脚本会 **terminate 进程**。路径判定错一次，就可能杀掉
另一个发行包（或另一个项目）的后端 —— 所以"只杀自己人"这条必须有测试守着。
"""
import importlib.util
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "scripts", "dev_watch.py")

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
        FAILURES.append(f"{name} {extra}")
        print(f"  [!!]   {name}  {extra}")


def load_module():
    spec = importlib.util.spec_from_file_location("dev_watch_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_is_watched(dw):
    print("\n[1] is_watched")
    cases = [
        ("bot_core.py", True, "顶层 .py"),
        ("plugins/ego/__init__.py", True, "plugins"),
        ("harness/agent_loop.py", True, "harness"),
        ("webui/webui_server.py", True, "webui .py"),
        ("protocols/base.py", True, "protocols"),
        ("webui/webui_artistic_v4.html", False, "前端热重载，不重启"),
        ("docs/USAGE.md", False, "文档"),
        ("plugins/ego/data/config.py", False, "data 运行时目录"),
        ("plugins/__pycache__/x.py", False, "缓存"),
        ("memes/memes/tag_memes.py", False, "表情包资源目录"),
        ("chat_logs/x.py", False, "聊天记录"),
        ("README.md", False, "非 py"),
    ]
    for rel, want, why in cases:
        got = dw.is_watched(rel)
        check(f"{rel} → {want}（{why}）", got == want, f"got={got}")


def test_snapshot(dw):
    print("\n[2] snapshot / changed_files")
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "plugins"), exist_ok=True)
        os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
        p_top = os.path.join(tmp, "bot_core.py")
        p_plug = os.path.join(tmp, "plugins", "x.py")
        p_doc = os.path.join(tmp, "docs", "y.py")
        for p in (p_top, p_plug, p_doc):
            with open(p, "w", encoding="utf-8") as f:
                f.write("a = 1\n")

        s1 = dw.snapshot(tmp)
        check("快照只含被监视文件", set(s1) == {"bot_core.py", "plugins/x.py"},
              f"got={sorted(s1)}")

        # 修改 plugins/x.py（size 变化 → 必被捕获）
        with open(p_plug, "w", encoding="utf-8") as f:
            f.write("a = 2\n# changed\n")
        s2 = dw.snapshot(tmp)
        check("修改能被检出", dw.changed_files(s1, s2) == ["plugins/x.py"],
              f"got={dw.changed_files(s1, s2)}")

        # 新增顶层文件
        with open(os.path.join(tmp, "new_mod.py"), "w", encoding="utf-8") as f:
            f.write("b = 1\n")
        s3 = dw.snapshot(tmp)
        check("新增能被检出", dw.changed_files(s2, s3) == ["new_mod.py"],
              f"got={dw.changed_files(s2, s3)}")

        # 删除
        os.remove(os.path.join(tmp, "new_mod.py"))
        s4 = dw.snapshot(tmp)
        check("删除能被检出", dw.changed_files(s3, s4) == ["new_mod.py"],
              f"got={dw.changed_files(s3, s4)}")

        # docs 里的 .py 改了 → 不触发
        with open(p_doc, "w", encoding="utf-8") as f:
            f.write("z = 9\n")
        check("非监视目录改动不触发", dw.changed_files(s4, dw.snapshot(tmp)) == [],
              f"got={dw.changed_files(s4, dw.snapshot(tmp))}")

        check("无改动时 changed 为空", dw.changed_files(s4, dict(s4)) == [])


def test_backend_processes(dw):
    print("\n[3] backend_processes（只杀自己人）")
    app = os.path.join(os.sep, "E", "EggBloom", "resources", "app")
    other = os.path.join(os.sep, "D", "OtherApp", "resources", "app")
    procs = [
        {"pid": 101, "cmdline": ["python.exe", os.path.join(app, "webui", "webui_server.py")],
         "cwd": app},
        {"pid": 202, "cmdline": ["python.exe", "webui/webui_server.py"], "cwd": app},
        {"pid": 303, "cmdline": ["python.exe", os.path.join(other, "webui", "webui_server.py")],
         "cwd": other},
        {"pid": 404, "cmdline": ["python.exe", "-c", "print(1)"], "cwd": app},
        {"pid": 505, "cmdline": ["EggBloom Desktop.exe"], "cwd": app},
    ]
    hits = {p["pid"] for p in dw.backend_processes(procs, app)}
    check("绝对路径命中", 101 in hits)
    check("相对路径 + cwd 命中", 202 in hits)
    check("别的发行包不碰", 303 not in hits)
    check("无关 python 不碰", 404 not in hits)
    check("Electron 壳不算后端", 505 not in hits)
    check("总数正确", hits == {101, 202}, f"got={sorted(hits)}")
    check("空输入安全", dw.backend_processes([], app) == [])
    check("None 安全", dw.backend_processes(None, app) == [])


def test_read_port(dw):
    print("\n[4] read_port")
    with tempfile.TemporaryDirectory() as tmp:
        check("缺失 → 8080", dw.read_port(tmp) == 8080)
        with open(os.path.join(tmp, "last_port.txt"), "w", encoding="utf-8") as f:
            f.write("8081\n")
        check("读到 8081", dw.read_port(tmp) == 8081)
        with open(os.path.join(tmp, "last_port.txt"), "w", encoding="utf-8") as f:
            f.write("abc")
        check("非法 → 8080", dw.read_port(tmp) == 8080)
        with open(os.path.join(tmp, "last_port.txt"), "w", encoding="utf-8") as f:
            f.write("99999")
        check("越界 → 8080", dw.read_port(tmp) == 8080)


def main():
    if not os.path.isfile(SCRIPT):
        print(f"缺文件: {SCRIPT}")
        return 2
    dw = load_module()
    test_is_watched(dw)
    test_snapshot(dw)
    test_backend_processes(dw)
    test_read_port(dw)
    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
