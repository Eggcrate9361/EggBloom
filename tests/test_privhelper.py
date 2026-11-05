# -*- coding: utf-8 -*-
"""tests/test_privhelper.py —— 高权限助手（2026-11-05）

真机上 EggBloom 被降到 Medium（Electron 自降权），文件操作与协议拉起都受限。
方案：由 SYSTEM 计划任务拉起的小服务（不经过 Electron，不降权），
只开窄口子：移动/删除/建目录/启动进程。

判据：
  ① 令牌不对 → 拒绝，且**不执行**
  ② 路径不在允许根下 → 拒绝，且不执行
  ③ 允许根内的操作能真正完成
  ④ 客户端在助手不可用时返回 None（上层退回本地），不抛异常
  ⑤ filesafe 只在 PermissionError 时才找助手（行为可预测）
"""
import json
import os
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import harness.privhelper as ph  # noqa: E402
import harness.privclient as pc  # noqa: E402

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


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Server:
    def __init__(self, port, roots):
        self.port, self.roots = port, roots
        self._old = None

    def __enter__(self):
        ph.ensure_token()
        self._old = os.environ.get("EGGBLOOM_PRIVHELPER_ROOTS")
        os.environ["EGGBLOOM_PRIVHELPER_ROOTS"] = ";".join(self.roots)
        self.srv = ph.serve(self.port)
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()
        return self

    def __exit__(self, *a):
        try:
            self.srv.shutdown()
        except Exception:
            pass
        if self._old is None:
            os.environ.pop("EGGBLOOM_PRIVHELPER_ROOTS", None)
        else:
            os.environ["EGGBLOOM_PRIVHELPER_ROOTS"] = self._old
        return False


def rpc(port, op, args, token, timeout=5):
    body = json.dumps({"op": op, "args": args}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:%d/rpc" % port, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8", "ignore"))


def health(port, timeout=3):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % port,
                                    timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return None


def main():
    token = ph.ensure_token()
    base = tempfile.mkdtemp(prefix="privhelper_")
    port = free_port()

    with Server(port, [base]):
        print("[A] /health")
        h = health(port)
        check("★ 服务在", h and h.get("ok"), h)
        check("列出操作", h and set(h.get("ops") or []) >= {"fs.move", "fs.delete", "spawn"})

        print("\n[B] 权限边界")
        r = rpc(port, "fs.mkdir", {"path": os.path.join(base, "a")}, "wrong-token")
        check("★ 错令牌被拒", r.get("ok") is False, r)
        check("★ 错令牌没有副作用", not os.path.exists(os.path.join(base, "a")))

        outside = "C:\\Windows\\privhelper_should_fail" if os.name == "nt" \
            else "/privhelper_should_fail"
        r2 = rpc(port, "fs.mkdir", {"path": outside}, token)
        check("★ 允许根外被拒", r2.get("ok") is False, r2)

        print("\n[C] 允许根内真的能执行")
        inner = os.path.join(base, "work")
        r3 = rpc(port, "fs.mkdir", {"path": inner}, token)
        check("mkdir", r3.get("ok") is True and os.path.isdir(inner), r3)

        src = os.path.join(base, "x.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write("hi")
        dst = os.path.join(base, "y.txt")
        r4 = rpc(port, "fs.move", {"src": src, "dst": dst}, token)
        check("★ move", r4.get("ok") is True and os.path.exists(dst)
              and not os.path.exists(src), r4)

        r5 = rpc(port, "fs.delete", {"path": dst}, token)
        check("★ delete", r5.get("ok") is True and not os.path.exists(dst), r5)

        r6 = rpc(port, "nope.op", {}, token)
        check("未知操作被拒", r6.get("ok") is False, r6)

    print("\n[D] 客户端：不可用时返回 None，不抛异常")
    old_port, old_tok = pc.PORT, pc.TOKEN_FILE
    try:
        pc.PORT = free_port()          # 没人监听
        pc.TOKEN_FILE = os.path.join(base, "no_token")
        check("★ available() False", pc.available() is False)
        check("★ call() 返回 None", pc.call("ping") is None)
        check("★ move() 返回 None", pc.move("a", "b") is None)
    finally:
        pc.PORT, pc.TOKEN_FILE = old_port, old_tok

    print("\n[E] filesafe 只在 PermissionError 时找助手")
    fs = open(os.path.join(ROOT, "harness", "filesafe.py"),
              encoding="utf-8", errors="replace").read()
    check("★ 有 _priv_move", "def _priv_move(" in fs)
    check("★ 捕获的是 PermissionError", "except PermissionError:" in fs)
    i = fs.find("except PermissionError:")
    tail = fs[i:i + 260]
    check("★ 失败仍抛原异常（不假装成功）", "raise" in tail, tail[:80])

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
