# -*- coding: utf-8 -*-
"""embed 看门狗回归（2026-11-13，服务器日志对账 §8 的 1+2）。

真机现场：exe 被解析成仓库**目录**时 `exists()` 也为真，`Popen` 去 exec 一个目录
→ `[Errno 13] Permission denied`；固定 30s 的看门狗在 59 小时内刷了 7,085 次
「拉起失败」，而 systemd 只看到 `active`。

两条修复：
  1. 判据从 `exists()` 改成 `isfile()` + `os.access(..., X_OK)`；
  2. 看门狗连续失败指数退避（30→900s 封顶）、成功 / 恢复归零、失败留痕，
     显式关掉向量检索按「配置意图」处理而不是故障。

本用例**不 import webui_server**（import 会拉起真服务与守护线程），改用
AST 抽出这三个函数、在带 stub 的命名空间里编译执行，既能验行为也不碰现场。

运行：python tests/test_embed_watchdog.py
"""
from __future__ import annotations

import ast
import io
import os
import re
import sys
import tempfile
import types
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC_PATH = os.path.join(ROOT, "webui", "webui_server.py")
SRC = io.open(SRC_PATH, encoding="utf-8").read()
TREE = ast.parse(SRC)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


def fn_src(name):
    for n in TREE.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(SRC, n) or ""
    return ""


ENSURE = fn_src("_ensure_embed_server")
TICK = fn_src("_embed_watch_tick")
WATCH = fn_src("_watch_embed_server")
BACKOFF = fn_src("_embed_backoff")
BARE = re.compile(r"except\b[^:\n]*:\s*\n\s*pass\b")

# ── 1. 静态闸：目录判据 / 退避 / 不许再吞异常 ────────────────────
check("★ 三个函数都还在", all((ENSURE, TICK, WATCH, BACKOFF)))
check("★ exe 用 isfile 而不是 exists", "os.path.isfile(str(exe))" in ENSURE)
check("★ model 用 isfile 而不是 exists", "os.path.isfile(str(model))" in ENSURE)
check("★ 不再有 exe.exists()", "exe.exists()" not in ENSURE)
check("★ 校验可执行权限", "os.access(str(exe), os.X_OK)" in ENSURE)
check("★ _ensure_embed_server 没有裸 except pass", BARE.search(ENSURE) is None,
      (BARE.search(ENSURE).group(0) if BARE.search(ENSURE) else ""))
check("★ 巡检单轮没有裸 except pass", BARE.search(TICK) is None)
check("★ 守护循环没有裸 except pass", BARE.search(WATCH) is None)
check("★ 守护循环不再写死 sleep(30)", "time.sleep(30)" not in WATCH)
check("★ 守护循环消费单轮返回的退避值", "_embed_watch_tick()" in WATCH)
check("★ 巡检调用退避函数", "_embed_backoff(" in TICK)
check("★ 失败留痕", "连续第" in TICK and "退避" in TICK)
check("★ use_vector_recall=False 不算故障", "use_vector_recall" in TICK)

# ── 2. 行为闸：抽出 _embed_backoff / _embed_watch_tick 单独执行 ──────
class _Env:
    def __init__(self):
        self.rc = 1            # connect_ex 返回码：0 = 存活
        self.boom = False      # socket 探测抛异常
        self.cfg = {}
        self.launch_ok = False
        self.launch_boom = False
        self.launch_calls = 0
        self.logs = []


ENV = _Env()


class _Sock:
    def __init__(self, env):
        self.env = env

    def settimeout(self, _x):
        pass

    def connect_ex(self, _addr):
        if self.env.boom:
            raise OSError("socket 故意炸")
        return self.env.rc

    def close(self):
        pass


class _SocketModule:
    AF_INET = 2
    SOCK_STREAM = 1

    def socket(self, *_a):
        return _Sock(ENV)


def _cfg_stub():
    return ENV.cfg


def _launch_stub():
    ENV.launch_calls += 1
    if ENV.launch_boom:
        raise RuntimeError("拉起故意炸")
    return ENV.launch_ok


def _print_stub(*a):
    ENV.logs.append(" ".join(str(x) for x in a))


def fresh_ns():
    global ENV
    ENV = _Env()
    ns = {
        "_EMBED_WATCH_BASE": 30,
        "_EMBED_WATCH_MAX": 900,
        "_EMBED_PORT": 8083,
        "_embed_fail_streak": 0,
        "socket": _SocketModule(),
        "_embed_config": _cfg_stub,
        "_ensure_embed_server": _launch_stub,
        "print": _print_stub,
    }
    names = {"_embed_backoff", "_embed_watch_tick"}
    mod = ast.Module(body=[n for n in TREE.body
                           if isinstance(n, ast.FunctionDef) and n.name in names],
                     type_ignores=[])
    exec(compile(ast.fix_missing_locations(mod), "<embed_watch>", "exec"), ns)
    return ns


ns = fresh_ns()
seq = [ns["_embed_backoff"](n) for n in (0, 1, 2, 3, 4, 5, 6, 7, 99)]
check("★ 退避序列 30→60→120→240→480→900 封顶",
      seq == [30, 30, 60, 120, 240, 480, 900, 900, 900], seq)

# 2.1 存活：不尝试拉起、连续失败计数归零
ns = fresh_ns()
ns["_embed_fail_streak"] = 4
ENV.rc = 0
d = ns["_embed_watch_tick"]()
check("★ 存活时只等 30s", d == 30, d)
check("★ 存活时不重复拉起", ENV.launch_calls == 0, ENV.launch_calls)
check("★ 恢复后失败计数归零", ns["_embed_fail_streak"] == 0, ns["_embed_fail_streak"])
check("★ 恢复留痕", any("已恢复" in x for x in ENV.logs), ENV.logs)

# 2.2 显式关闭向量检索：不重试、不刷屏
ns = fresh_ns()
ENV.cfg = {"use_vector_recall": False}
d = ns["_embed_watch_tick"]()
check("★ 显式关闭时拉长巡检到 15min", d == 900, d)
check("★ 显式关闭时不拉起", ENV.launch_calls == 0, ENV.launch_calls)
check("★ 显式关闭不算失败", ns["_embed_fail_streak"] == 0)

# 2.3 连续拉起失败：指数退避 + 每轮留痕
ns = fresh_ns()
delays = [ns["_embed_watch_tick"]() for _ in range(7)]
check("★ 连续失败退避序列", delays == [30, 60, 120, 240, 480, 900, 900], delays)
check("★ 每轮都尝试了拉起", ENV.launch_calls == 7, ENV.launch_calls)
check("★ 失败计数累加到 7", ns["_embed_fail_streak"] == 7, ns["_embed_fail_streak"])
check("★ 日志写明连续次数与退避",
      any("连续第 3 次拉起失败" in x and "120s" in x for x in ENV.logs), ENV.logs[:4])

# 2.4 拉起成功后立即回到 30s
ns = fresh_ns()
for _ in range(3):
    ns["_embed_watch_tick"]()
ENV.launch_ok = True
d = ns["_embed_watch_tick"]()
check("★ 成功后回到 30s", d == 30, d)
check("★ 成功后计数归零", ns["_embed_fail_streak"] == 0)
check("★ 成功留痕", any("拉起成功" in x for x in ENV.logs), ENV.logs[-2:])

# 2.5 探测异常 / 拉起异常：不吞、不崩、同样退避
ns = fresh_ns()
ENV.boom = True
d = ns["_embed_watch_tick"]()
check("★ 探测异常时也退避", d == 30 and ns["_embed_fail_streak"] == 1, d)
check("★ 探测异常留痕", any("巡检异常" in x for x in ENV.logs), ENV.logs)
ns = fresh_ns()
ENV.launch_boom = True
d = ns["_embed_watch_tick"]()
check("★ 拉起异常时不崩", d == 30 and ns["_embed_fail_streak"] == 1, d)
check("★ 拉起异常留痕", any("拉起过程异常" in x for x in ENV.logs), ENV.logs)

# ── 3. 行为闸：目录当 exe 必须被挡在 Popen 之前 ──────────────────
def run_ensure(case, popen_calls):
    """在隔离命名空间里跑真的 _ensure_embed_server，Popen 换成记账假件。"""
    tmp = tempfile.mkdtemp(prefix="egg_embed_guard_")
    model = os.path.join(tmp, "model.gguf")
    io.open(model, "w", encoding="utf-8").write("x")
    exe_file = os.path.join(tmp, "llama-server.exe")
    io.open(exe_file, "w", encoding="utf-8").write("x")

    exe = tmp if case == "exe_is_dir" else exe_file
    model_arg = os.path.join(tmp, "missing.gguf") if case == "model_missing" else model
    logs = []

    fake_subprocess = types.ModuleType("subprocess")
    fake_subprocess.CREATE_NO_WINDOW = 0

    def _popen(argv, **_kw):
        popen_calls.append(list(argv))
        return types.SimpleNamespace(poll=lambda: None)

    fake_subprocess.Popen = _popen
    old = sys.modules.get("subprocess")
    sys.modules["subprocess"] = fake_subprocess
    try:
        ns2 = {
            "os": os, "Path": Path,
            "socket": _SocketModule(), "PROJECT": Path(tmp),
            "_EMBED_PORT": 8083, "_embed_proc": None,
            "_EMBED_DEFAULT_EXE": Path(""), "_EMBED_BUILTIN_EXE": Path("unused.exe"),
            "_EMBED_DEFAULT_MODEL": Path(""), "_EMBED_BUILTIN_MODEL": Path("unused.gguf"),
            "_embed_config": lambda: {"embed_exe": str(exe),
                                      "embed_model_path": str(model_arg)},
            "_embed_warn_once": lambda msg: logs.append("[embed] " + str(msg)),
            "print": lambda *a: logs.append(" ".join(str(x) for x in a)),
        }
        mod = ast.Module(body=[n for n in TREE.body
                               if isinstance(n, ast.FunctionDef)
                               and n.name == "_ensure_embed_server"],
                         type_ignores=[])
        exec(compile(ast.fix_missing_locations(mod), "<embed_ensure>", "exec"), ns2)
        ENV.rc = 1          # 不存活，强制走到路径判据
        ret = ns2["_ensure_embed_server"]()
    finally:
        if old is None:
            del sys.modules["subprocess"]
        else:
            sys.modules["subprocess"] = old
    return ret, logs, exe_file, model


calls = []
ret, logs, _, _ = run_ensure("exe_is_dir", calls)
check("★ exe 是目录时直接判失败（真机 7085 次事故的根因）", ret is False, ret)
check("★ 目录没被送进 Popen", calls == [], calls)
check("★ 目录判失败有明确日志", any("找不到 embedding 程序/模型文件" in x for x in logs), logs)

calls = []
ret, logs, _, _ = run_ensure("model_missing", calls)
check("★ 模型文件缺失时也判失败", ret is False, ret)
check("★ 模型缺失时也没拉起", calls == [], calls)

calls = []
ret, logs, exe_file, model = run_ensure("ok", calls)
check("★ 正常路径仍能拉起", ret is True, ret)
check("★ 正常路径 Popen 只调一次", len(calls) == 1, calls)
check("★ Popen 首参是可执行文件", bool(calls) and calls[0][0] == exe_file, calls[:1])
check("★ 正常路径带着 -m 模型", bool(calls) and "-m" in calls[0] and model in calls[0],
      calls[:1])


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    for n in FAIL:
        print("  FAIL ", n)
    sys.exit(1 if FAIL else 0)
